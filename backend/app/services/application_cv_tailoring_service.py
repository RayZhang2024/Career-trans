"""Durable, user-scoped V2B strategy, draft, and approval workflow."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Callable
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.application_cv_tailoring import ApplicationCVTailoringAgent
from app.core.config import Settings, get_settings
from app.models.application_cv_tailoring import ApplicationCVTailoringSession
from app.models.application_preparation import ApplicationPreparation
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.models.user_job_discovery import UserJobEvaluation
from app.providers.llm import SemanticOutputError, SemanticProviderRequestError, SemanticProviderUnavailableError
from app.schemas.application_cv_tailoring import (
    TailoringAction, TailoringAttemptStatus, TailoringDraftGenerateRequest, TailoringPreflightRead,
    TailoringSessionRead, TailoringStartRequest, TailoringStatus, TailoringStrategy,
    TailoringStrategyReview, TailoringStrategyReviewWrite, TailoringDraftReviewWrite,
    TailoringApproveWrite,
    TailoringProfileOutline,
    TailoringEmploymentOutline,
    TailoringProjectOutline,
)
from app.schemas.application_preparation import (
    ApplicationEvidenceSnapshotStatus, ApplicationEvidenceSource, ApplicationIdentitySnapshot,
    ApplicationPreparationResult, ApplicationTailoringReviewSnapshot, ApplicationTargetSnapshot,
    CVWritingDraft, PersistedApplicationPreparationEnvelope,
)
from app.schemas.candidate import CandidateContext
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.job_ranking import RankedJobOpportunity
from app.schemas.job import JobProfile
from app.schemas.matching import EvidenceSourceType
from app.services.application_document_renderer import ApplicationDocumentRenderer
from app.services.application_preparation_service import ApplicationPreparationService
from app.services.ai_settings_service import AiSettingsService
from app.services.canonical_candidate_read_service import (
    CandidateEvidenceMaterializationIncomplete, CanonicalCandidateReadService,
)
from app.services.llm_runtime import ResolvedRuntimeSnapshot
from app.services.semantic_runtime_attribution import available_attribution, canonical_attribution_json, read_attribution
from app.services.user_job_discovery_service import UserJobDiscoveryService, reusable_current_evaluation
from app.schemas.ai_settings import SemanticOperation


from app.services.application_cv_tailoring_budget import (
    DRAFT_OUTPUT_TOKEN_LIMIT, MAX_INPUT_CHARACTERS, MAX_REQUIREMENTS, MAX_REVIEW_REVISIONS,
    PROVIDER_TIMEOUT_SECONDS, STRATEGY_OUTPUT_TOKEN_LIMIT,
)


class ApplicationCVTailoringError(ValueError):
    def __init__(self, message: str, *, status_code: int = 409, preflight_state: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.preflight_state = preflight_state


@dataclass(frozen=True, slots=True)
class _Authority:
    job: DiscoveredJob
    evaluation: UserJobEvaluation
    opportunity: RankedJobOpportunity
    target: ApplicationTargetSnapshot
    identity: ApplicationIdentitySnapshot
    context: CandidateContext
    structured: CandidateCVData
    sources: dict[tuple[str, str], str]
    candidate_fingerprint: str
    runtime_fingerprint: str


class ApplicationCVTailoringService:
    """Two explicit model requests; every review/read path is provider-free."""

    def __init__(
        self, session: Session, *, runtime_snapshot: ResolvedRuntimeSnapshot,
        application_service: ApplicationPreparationService,
        agent_factory: Callable[[], ApplicationCVTailoringAgent],
        settings: Settings | None = None,
        runtime_snapshot_resolver: Callable[[str], ResolvedRuntimeSnapshot] | None = None,
    ) -> None:
        self._session = session
        self._runtime = runtime_snapshot
        self._application_service = application_service
        self._agent_factory = agent_factory
        self._settings = settings or get_settings()
        self._runtime_snapshot_resolver = runtime_snapshot_resolver or (lambda _user_id: self._runtime)

    def preflight(self, user_id: str, discovered_job_id: str) -> TailoringPreflightRead:
        state = "ready"
        title = "Selected job"
        try:
            authority = self._authority(user_id, discovered_job_id)
            title = authority.target.title
            try:
                self._check_input_budget(self._strategy_context(authority, 2))
            except ApplicationCVTailoringError as exc:
                if exc.status_code == 413:
                    state = "input_too_large"
                else:
                    raise
        except ApplicationCVTailoringError as exc:
            state = exc.preflight_state or "current_evaluation_required"
            job = self._session.get(DiscoveredJob, discovered_job_id)
            if job is not None:
                title = job.title
        runtime = self._current_runtime(user_id)
        sessions = self._session.scalars(
            select(ApplicationCVTailoringSession)
            .where(
                ApplicationCVTailoringSession.user_id == user_id,
                ApplicationCVTailoringSession.discovered_job_id == discovered_job_id,
            )
            .order_by(ApplicationCVTailoringSession.created_at.desc(), ApplicationCVTailoringSession.id.desc())
            .limit(10)
        ).all()
        return TailoringPreflightRead(
            discovered_job_id=discovered_job_id, job_title=title, state=state,
            provider=runtime.provider,
            model=runtime.operation(SemanticOperation.APPLICATION_DRAFTING).model,
            strategy_output_token_limit=STRATEGY_OUTPUT_TOKEN_LIMIT,
            draft_output_token_limit=DRAFT_OUTPUT_TOKEN_LIMIT,
            timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
            max_requirements=MAX_REQUIREMENTS,
            max_input_characters=MAX_INPUT_CHARACTERS,
            sessions=[self._read(row, user_id) for row in sessions],
        )

    def get(self, user_id: str, tailoring_id: str) -> TailoringSessionRead:
        row = self._owned(user_id, tailoring_id)
        return self._read(row, user_id)

    def get_by_request(self, user_id: str, client_request_id: str) -> TailoringSessionRead:
        row = self._session.scalar(select(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.user_id == user_id,
            ApplicationCVTailoringSession.client_request_id == client_request_id,
        ))
        if row is None:
            raise LookupError("Tailored CV request not found.")
        return self._read(row, user_id)

    def list_for_job(self, user_id: str, discovered_job_id: str) -> list[TailoringSessionRead]:
        rows = self._session.scalars(
            select(ApplicationCVTailoringSession)
            .where(
                ApplicationCVTailoringSession.user_id == user_id,
                ApplicationCVTailoringSession.discovered_job_id == discovered_job_id,
            )
            .order_by(ApplicationCVTailoringSession.created_at.desc(), ApplicationCVTailoringSession.id.desc())
            .limit(10)
        ).all()
        return [self._read(row, user_id) for row in rows]

    def start(self, user_id: str, request: TailoringStartRequest) -> TailoringSessionRead:
        request_fingerprint = _fingerprint({
            "discovered_job_id": request.discovered_job_id,
            "target_pages": request.target_pages,
        })
        existing = self._session.scalar(select(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.user_id == user_id,
            ApplicationCVTailoringSession.client_request_id == request.client_request_id,
        ))
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise ApplicationCVTailoringError("This request ID belongs to a different tailoring request.")
            return self._read(existing, user_id)

        authority = self._authority(user_id, request.discovered_job_id)
        strategy_context = self._strategy_context(authority, request.target_pages)
        self._check_input_budget(strategy_context)
        now = _now()
        row = ApplicationCVTailoringSession(
            user_id=user_id, discovered_job_id=request.discovered_job_id,
            client_request_id=request.client_request_id, request_fingerprint=request_fingerprint,
            status=TailoringStatus.STRATEGY_IN_PROGRESS.value,
            strategy_status=TailoringAttemptStatus.IN_PROGRESS.value,
            draft_status=TailoringAttemptStatus.NOT_STARTED.value,
            target_snapshot_json=_json(authority.target),
            identity_snapshot_json=_json(authority.identity),
            profile_outline_json=_json(_profile_outline(authority.structured)),
            candidate_fingerprint=authority.candidate_fingerprint,
            candidate_evaluation_fingerprint=authority.evaluation.candidate_evaluation_fingerprint,
            evaluation_contract_fingerprint=authority.evaluation.evaluation_contract_fingerprint,
            evaluation_id=authority.evaluation.id, runtime_fingerprint=authority.runtime_fingerprint,
            provider=self._runtime.provider,
            model=self._runtime.operation(SemanticOperation.APPLICATION_DRAFTING).model,
            target_pages=request.target_pages,
            runtime_attribution_json=canonical_attribution_json(available_attribution(
                self._runtime, (SemanticOperation.APPLICATION_DRAFTING,),
            )),
            created_at=now, updated_at=now,
        )
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raced = self._session.scalar(select(ApplicationCVTailoringSession).where(
                ApplicationCVTailoringSession.user_id == user_id,
                ApplicationCVTailoringSession.client_request_id == request.client_request_id,
            ))
            if raced is None or raced.request_fingerprint != request_fingerprint:
                raise ApplicationCVTailoringError("A duplicate tailoring request was rejected safely.")
            return self._read(raced, user_id)
        self._session.refresh(row)

        try:
            agent = self._agent_factory()
        except Exception:
            return self._finish_strategy_failure(row, user_id, "provider_configuration_unavailable", uncertain=False)
        try:
            strategy = agent.recommend_strategy(strategy_context)
            self._validate_strategy(strategy, authority)
        except SemanticProviderRequestError:
            return self._finish_strategy_failure(row, user_id, "provider_request_rejected", uncertain=False)
        except SemanticOutputError:
            return self._finish_strategy_failure(row, user_id, "strategy_result_invalid", uncertain=False, invalid=True)
        except Exception:
            return self._finish_strategy_failure(row, user_id, "provider_outcome_uncertain", uncertain=True)

        if not self._is_current(row, user_id):
            row.strategy_json = _json(strategy)
            row.strategy_status = TailoringAttemptStatus.STALE.value
            row.status = TailoringStatus.STALE.value
            row.strategy_revision = 1
            row.failure_code = "authority_changed_during_strategy"
            row.updated_at = _now()
            self._session.commit()
            return self._read(row, user_id)
        row.strategy_json = _json(strategy)
        row.strategy_status = TailoringAttemptStatus.COMPLETED.value
        row.status = TailoringStatus.STRATEGY_REVIEW.value
        row.strategy_revision = 1
        row.strategy_history_json = _json([{
            "revision": 1, "kind": "generated", "strategy": strategy.model_dump(mode="json"), "recorded_at": _iso(_now()),
        }])
        row.updated_at = _now()
        self._session.commit()
        return self._read(row, user_id)

    def review_strategy(self, user_id: str, tailoring_id: str, write: TailoringStrategyReviewWrite) -> TailoringSessionRead:
        row = self._owned(user_id, tailoring_id)
        if not self._is_current(row, user_id):
            self._mark_stale(row)
            raise ApplicationCVTailoringError("The Profile, job evaluation, or AI settings changed. Start a new tailored CV from the current job.")
        if row.status not in {TailoringStatus.STRATEGY_REVIEW.value, TailoringStatus.STRATEGY_ACCEPTED.value}:
            raise ApplicationCVTailoringError("This strategy is no longer open for review.")
        if write.expected_revision != row.strategy_revision:
            raise ApplicationCVTailoringError("This strategy changed in another tab. Refresh before saving.", status_code=409)
        if len(json.loads(row.strategy_history_json or "[]")) >= MAX_REVIEW_REVISIONS:
            raise ApplicationCVTailoringError("The strategy revision limit was reached. Start a new tailored CV.")
        strategy = TailoringStrategy.model_validate_json(row.strategy_json or "{}")
        positioning_override = (write.positioning_override or "").strip() or None
        positioning_changed = bool(
            positioning_override and _normalise_claim_text(positioning_override) != _normalise_claim_text(strategy.positioning)
        )
        if write.accepted is True and positioning_changed and not write.acknowledge_unverified_positioning:
            raise ApplicationCVTailoringError(
                "Your positioning adjustment is user-written and not verified by its citations. Review and acknowledge it before accepting the strategy.",
                status_code=422,
            )
        by_id = {item.recommendation_id: item for item in strategy.recommendations}
        choices = write.choices or [
            TailoringStrategyReview(
                recommendation_id=item.recommendation_id,
                decision={
                    TailoringAction.FEATURE: "keep",
                    TailoringAction.REORDER: "reorder",
                    TailoringAction.CONDENSE: "deemphasise",
                    TailoringAction.OMIT: "skip",
                }[item.action],
                order=index,
            )
            for index, item in enumerate(strategy.recommendations)
        ]
        ids = [item.recommendation_id for item in choices]
        orders = [item.order for item in choices]
        if len(ids) != len(set(ids)) or len(orders) != len(set(orders)) or any(value not in by_id for value in ids):
            raise ApplicationCVTailoringError("Strategy choices must use each current recommendation at most once.")
        if set(ids) != set(by_id):
            raise ApplicationCVTailoringError("Save one choice for each strategy recommendation.")
        match_types = {item.requirement_index: item.match_type.value for item in self._target_for_row(row).requirement_matches}
        for choice in choices:
            recommendation = by_id[choice.recommendation_id]
            if match_types.get(recommendation.requirement_index) not in {"demonstrated", "transferable"} and choice.decision.value != "skip":
                raise ApplicationCVTailoringError("Inferred, missing, unknown, and incompatible requirements cannot be selected as CV evidence.")
        review = {
            "accepted": write.accepted,
            "positioning_override": positioning_override,
            "positioning_review_status": (
                "user_acknowledged_unverified" if positioning_changed and write.acknowledge_unverified_positioning
                else "unverified_needs_acknowledgement" if positioning_changed
                else "not_overridden"
            ),
            "positioning_user_decision": "acknowledged_unverified_positioning" if positioning_changed and write.acknowledge_unverified_positioning else None,
            "choices": [item.model_dump(mode="json") for item in choices],
        }
        history = json.loads(row.strategy_history_json or "[]")
        next_revision = row.strategy_revision + 1
        history.append({
            "revision": next_revision,
            "kind": "accepted" if write.accepted is True else "rejected" if write.accepted is False else "edited",
            "review": review, "recorded_at": _iso(_now()),
        })
        next_status = (
            TailoringStatus.STRATEGY_ACCEPTED.value if write.accepted is True
            else TailoringStatus.STRATEGY_REJECTED.value if write.accepted is False
            else TailoringStatus.STRATEGY_REVIEW.value
        )
        result = self._session.execute(update(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.id == row.id,
            ApplicationCVTailoringSession.user_id == user_id,
            ApplicationCVTailoringSession.strategy_revision == write.expected_revision,
            ApplicationCVTailoringSession.status.in_([
                TailoringStatus.STRATEGY_REVIEW.value, TailoringStatus.STRATEGY_ACCEPTED.value,
            ]),
        ).values(
            strategy_review_json=_json(review), strategy_history_json=_json(history),
            strategy_revision=next_revision, status=next_status, updated_at=_now(),
        ))
        if result.rowcount != 1:
            self._session.rollback()
            raise ApplicationCVTailoringError("This strategy changed in another tab. Refresh before saving.", status_code=409)
        self._session.commit()
        self._session.expire_all()
        return self.get(user_id, tailoring_id)

    def generate_draft(self, user_id: str, tailoring_id: str, request: TailoringDraftGenerateRequest) -> TailoringSessionRead:
        row = self._owned(user_id, tailoring_id)
        if row.draft_client_request_id:
            if row.draft_client_request_id != request.client_request_id:
                raise ApplicationCVTailoringError("A draft request has already been recorded for this strategy. Start a new tailoring request to retry.")
            return self._read(row, user_id)
        if row.status != TailoringStatus.STRATEGY_ACCEPTED.value or request.expected_strategy_revision != row.strategy_revision:
            raise ApplicationCVTailoringError("Accept the current strategy before requesting a CV draft.")
        if not self._is_current(row, user_id):
            self._mark_stale(row)
            raise ApplicationCVTailoringError("The Profile, job evaluation, or AI settings changed. Start a new tailored CV from the current job.")
        authority = self._authority(user_id, row.discovered_job_id)
        strategy, review = self._strategy_and_review(row)
        if self._positioning_status(strategy, review) == "unverified_needs_acknowledgement":
            raise ApplicationCVTailoringError("Review and acknowledge the user-written positioning before requesting a draft.", status_code=422)
        selected = self._selected_sources(authority.sources, strategy, review)
        if not selected:
            raise ApplicationCVTailoringError("Keep at least one evidence-backed strategy item before generating a tailored CV.")
        structural = self._structural_anchor_sources(authority, selected)
        allowed = {**selected, **structural}
        context = self._draft_context(authority, strategy, review, selected, structural, row.target_pages)
        self._check_input_budget(context)

        result = self._session.execute(update(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.id == row.id,
            ApplicationCVTailoringSession.user_id == user_id,
            ApplicationCVTailoringSession.status == TailoringStatus.STRATEGY_ACCEPTED.value,
            ApplicationCVTailoringSession.strategy_revision == request.expected_strategy_revision,
            ApplicationCVTailoringSession.draft_status == TailoringAttemptStatus.NOT_STARTED.value,
        ).values(
            status=TailoringStatus.DRAFT_IN_PROGRESS.value,
            draft_status=TailoringAttemptStatus.IN_PROGRESS.value,
            draft_client_request_id=request.client_request_id,
            updated_at=_now(),
        ))
        if result.rowcount != 1:
            self._session.rollback()
            latest = self._owned(user_id, tailoring_id)
            if latest.draft_client_request_id == request.client_request_id:
                return self._read(latest, user_id)
            raise ApplicationCVTailoringError("A draft request is already in progress or this strategy changed.", status_code=409)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ApplicationCVTailoringError("This draft request ID was already used.") from exc
        self._session.expire_all()
        row = self._owned(user_id, tailoring_id)

        try:
            agent = self._agent_factory()
        except Exception:
            return self._finish_draft_failure(row, user_id, "provider_configuration_unavailable", uncertain=False)
        try:
            draft = agent.draft_tailored_cv(context)
            self._application_service._materialize_cv(draft, authority.structured, allowed)
            self._validate_draft_strategy_refs(draft, selected, structural, authority.structured)
        except SemanticProviderRequestError:
            return self._finish_draft_failure(row, user_id, "provider_request_rejected", uncertain=False)
        except (SemanticOutputError, ValueError):
            return self._finish_draft_failure(row, user_id, "draft_result_invalid", uncertain=False, invalid=True)
        except Exception:
            return self._finish_draft_failure(row, user_id, "provider_outcome_uncertain", uncertain=True)

        if not self._is_current(row, user_id):
            row.draft_json = _json(draft)
            row.draft_status = TailoringAttemptStatus.STALE.value
            row.status = TailoringStatus.STALE.value
            row.draft_revision = 1
            row.failure_code = "authority_changed_during_draft"
            row.updated_at = _now()
            self._session.commit()
            return self._read(row, user_id)
        row.draft_json = _json(draft)
        row.draft_status = TailoringAttemptStatus.COMPLETED.value
        row.status = TailoringStatus.DRAFT_REVIEW.value
        row.draft_revision = 1
        row.draft_history_json = _json([{
            "revision": 1, "kind": "generated", "draft": draft.model_dump(mode="json"),
            "factual_review": {
                "status": "model_generated_not_semantically_verified",
                "decision": "awaiting_user_review",
                "acknowledged": False,
            },
            "recorded_at": _iso(_now()),
        }])
        row.updated_at = _now()
        self._session.commit()
        return self._read(row, user_id)

    def review_draft(self, user_id: str, tailoring_id: str, write: TailoringDraftReviewWrite) -> TailoringSessionRead:
        row = self._owned(user_id, tailoring_id)
        if row.status != TailoringStatus.DRAFT_REVIEW.value or write.expected_revision != row.draft_revision:
            raise ApplicationCVTailoringError("This draft changed or is no longer editable. Refresh before saving.", status_code=409)
        if not self._is_current(row, user_id):
            self._mark_stale(row)
            raise ApplicationCVTailoringError("This draft is stale because its Profile, job evaluation, or AI settings changed.")
        authority = self._authority(user_id, row.discovered_job_id)
        strategy, review = self._strategy_and_review(row)
        selected = self._selected_sources(authority.sources, strategy, review)
        structural = self._structural_anchor_sources(authority, selected)
        allowed = {**selected, **structural}
        try:
            self._application_service._materialize_cv(write.draft, authority.structured, allowed)
            self._validate_draft_strategy_refs(write.draft, selected, structural, authority.structured)
        except ValueError as exc:
            # A rejected user edit is a validation response, not an internal error.
            # The frontend keeps the unsaved edit visible and lets the user correct it.
            raise ApplicationCVTailoringError(str(exc), status_code=422) from exc
        history = json.loads(row.draft_history_json or "[]")
        if len(history) >= MAX_REVIEW_REVISIONS:
            raise ApplicationCVTailoringError("The draft revision limit was reached. Start a new tailored CV.")
        generated = CVWritingDraft.model_validate(history[0]["draft"])
        edited_claims = self._draft_claim_content(write.draft) != self._draft_claim_content(generated)
        previous_review = (history[-1].get("factual_review") or {}) if history else {}
        acknowledged = bool(write.acknowledge_unverified_claims) if edited_claims else False
        factual_review = {
            "status": (
                "user_edits_acknowledged_unverified" if edited_claims and acknowledged
                else "user_edits_unverified" if edited_claims
                else "model_generated_not_semantically_verified"
            ),
            "decision": "acknowledged_by_user" if edited_claims and acknowledged else "not_acknowledged" if edited_claims else "restored_model_generated_text",
            "acknowledged": acknowledged,
            "changed_from_model_draft": edited_claims,
            "recorded_at": _iso(_now()),
        }
        next_revision = row.draft_revision + 1
        history.append({
            "revision": next_revision, "kind": "user_edit", "draft": write.draft.model_dump(mode="json"),
            "factual_review": factual_review, "recorded_at": _iso(_now()),
        })
        result = self._session.execute(update(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.id == row.id,
            ApplicationCVTailoringSession.user_id == user_id,
            ApplicationCVTailoringSession.status == TailoringStatus.DRAFT_REVIEW.value,
            ApplicationCVTailoringSession.draft_revision == write.expected_revision,
        ).values(
            draft_json=_json(write.draft), draft_history_json=_json(history),
            draft_revision=next_revision, updated_at=_now(),
        ))
        if result.rowcount != 1:
            self._session.rollback()
            raise ApplicationCVTailoringError("This draft changed in another tab. Refresh before saving.", status_code=409)
        self._session.commit()
        self._session.expire_all()
        return self.get(user_id, tailoring_id)

    def approve(self, user_id: str, tailoring_id: str, write: TailoringApproveWrite) -> TailoringSessionRead:
        row = self._owned(user_id, tailoring_id)
        if row.status == TailoringStatus.APPROVED.value and row.approved_preparation_id:
            return self._read(row, user_id)
        if (
            row.status != TailoringStatus.DRAFT_REVIEW.value
            or write.expected_strategy_revision != row.strategy_revision
            or write.expected_draft_revision != row.draft_revision
            or row.draft_json is None
        ):
            raise ApplicationCVTailoringError("Save and review the current draft before approving it.", status_code=409)
        if not self._is_current(row, user_id):
            self._mark_stale(row)
            raise ApplicationCVTailoringError("This draft is stale. It cannot be approved; start again from the current job.")
        authority = self._authority(user_id, row.discovered_job_id)
        strategy, review = self._strategy_and_review(row)
        positioning_status = self._positioning_status(strategy, review)
        if positioning_status == "unverified_needs_acknowledgement":
            raise ApplicationCVTailoringError("A user-written positioning adjustment still needs explicit acknowledgement before approval.", status_code=422)
        factual_review = self._draft_factual_review(row)
        if factual_review.get("status") == "user_edits_unverified" or (factual_review.get("changed_from_model_draft") and not factual_review.get("acknowledged")):
            raise ApplicationCVTailoringError("Edited factual claims are not verified by their citations. Review and acknowledge the saved draft before approval.", status_code=422)
        if not review.get("accepted"):
            raise ApplicationCVTailoringError("Accept the current strategy before approving a CV.")
        selected = self._selected_sources(authority.sources, strategy, review)
        structural = self._structural_anchor_sources(authority, selected)
        allowed = {**selected, **structural}
        draft = CVWritingDraft.model_validate_json(row.draft_json)
        cv = self._application_service._materialize_cv(draft, authority.structured, allowed)
        self._validate_draft_strategy_refs(draft, selected, structural, authority.structured)
        cv = cv.model_copy(update={"roles": [role for role in cv.roles if role.bullets]})
        draft_status = factual_review.get("status", "model_generated_not_semantically_verified")
        override_status = positioning_status
        review_snapshot = ApplicationTailoringReviewSnapshot(
            draft_claim_status=(
                "user_edits_acknowledged_unverified" if draft_status == "user_edits_acknowledged_unverified"
                else "model_generated_not_semantically_verified"
            ),
            draft_user_decision=(
                "acknowledged_unverified_user_edits" if draft_status == "user_edits_acknowledged_unverified"
                else "approved_model_generated_text"
            ),
            positioning_status=("user_acknowledged_unverified" if override_status == "user_acknowledged_unverified" else "not_overridden"),
            positioning_user_decision=(
                "acknowledged_unverified_positioning" if override_status == "user_acknowledged_unverified" else "not_applicable"
            ),
            strategy_revision=row.strategy_revision, draft_revision=row.draft_revision,
        )
        result = ApplicationPreparationResult(
            cv=cv, cover_letter=None, answers=[], target_pages=row.target_pages, tailoring_review=review_snapshot,
        )
        renderer = ApplicationDocumentRenderer()
        result, pages, layout_status = renderer.compact_cv_to_target(authority.identity, authority.target, result)
        result = result.model_copy(update={"actual_pdf_pages": pages, "layout_status": layout_status})
        persisted = PersistedApplicationPreparationEnvelope(
            persistence_version=2,
            result=result,
            evidence_snapshot_status=ApplicationEvidenceSnapshotStatus.AVAILABLE.value,
            evidence_sources=[
                ApplicationEvidenceSource(source_type=kind, source_ref=ref, text=text)
                for (kind, ref), text in sorted(allowed.items())
            ],
        )
        preparation = ApplicationPreparation(
            user_id=user_id,
            target_snapshot_json=_json(authority.target),
            identity_snapshot_json=_json(authority.identity),
            preparation_input_fingerprint=_fingerprint({
                "tailoring_session_id": row.id, "strategy_revision": row.strategy_revision,
                "draft_revision": row.draft_revision, "draft": draft.model_dump(mode="json"),
                "target": authority.target.model_dump(mode="json"),
                "candidate": authority.candidate_fingerprint,
            }),
            preparation_contract_fingerprint=self._application_service._contract_fingerprint(),
            preparation_result_json=_json(persisted),
            runtime_attribution_json=canonical_attribution_json(available_attribution(
                self._runtime, (SemanticOperation.APPLICATION_DRAFTING,),
            )),
            created_at=_now(),
        )
        self._session.add(preparation)
        self._session.flush()
        result = self._session.execute(update(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.id == row.id,
            ApplicationCVTailoringSession.user_id == user_id,
            ApplicationCVTailoringSession.status == TailoringStatus.DRAFT_REVIEW.value,
            ApplicationCVTailoringSession.strategy_revision == write.expected_strategy_revision,
            ApplicationCVTailoringSession.draft_revision == write.expected_draft_revision,
        ).values(
            status=TailoringStatus.APPROVED.value,
            approved_preparation_id=preparation.id,
            updated_at=_now(),
        ))
        if result.rowcount != 1:
            self._session.rollback()
            raise ApplicationCVTailoringError("The draft changed before approval. Refresh and review again.", status_code=409)
        self._session.commit()
        self._session.expire_all()
        return self.get(user_id, tailoring_id)

    def _authority(self, user_id: str, discovered_job_id: str, runtime: ResolvedRuntimeSnapshot | None = None) -> _Authority:
        runtime = runtime or self._current_runtime(user_id)
        job = self._session.get(DiscoveredJob, discovered_job_id)
        if job is None or not UserJobDiscoveryService(self._session, runtime_snapshot=runtime).is_currently_actionable(job):
            raise ApplicationCVTailoringError("This job is no longer actionable.", preflight_state="job_not_actionable", status_code=404)
        reader = CanonicalCandidateReadService(self._session)
        try:
            snapshot = reader.read(user_id)
            context = reader.candidate_context(snapshot, require_structured_profile=True, require_complete_evidence=True)
        except CandidateEvidenceMaterializationIncomplete as exc:
            raise ApplicationCVTailoringError("Current candidate evidence is not fully available.", preflight_state="candidate_not_ready") from exc
        if context is None or snapshot.structured_profile is None or snapshot.profile is None:
            raise ApplicationCVTailoringError("A confirmed canonical Profile and complete evidence are required.", preflight_state="candidate_not_ready")
        profile = snapshot.profile
        user = self._session.get(User, user_id)
        if user is None or not profile.display_name or not profile.display_name.strip() or not (profile.preferred_email or user.email):
            raise ApplicationCVTailoringError("Add a display name and email to the canonical Profile before preparing a CV.", preflight_state="identity_required")
        identity = ApplicationIdentitySnapshot(
            display_name=profile.display_name.strip(),
            email=(profile.preferred_email or user.email).strip(),
            phone=profile.phone, location=profile.location, linkedin_url=profile.linkedin_url,
            github_url=profile.github_url, portfolio_url=profile.portfolio_url,
        )
        evaluation = reusable_current_evaluation(
            self._session, user_id, job, candidate_reader=reader,
            runtime_snapshot=runtime, candidate_context=context,
        )
        if evaluation is None:
            raise ApplicationCVTailoringError(
                "A current evaluated job is required. Open the current Fit result before tailoring a CV.",
                preflight_state="current_evaluation_required",
            )
        opportunity = RankedJobOpportunity.model_validate_json(evaluation.evaluation_json)
        if not opportunity.job_profile or not opportunity.requirement_matches:
            raise ApplicationCVTailoringError("The current evaluation has no reusable requirement matches.", preflight_state="current_evaluation_required")
        target = _target_snapshot(job, opportunity)
        sources = self._application_service._bounded_sources(context, snapshot.structured_profile, target.requirement_matches)
        if not sources:
            raise ApplicationCVTailoringError("No confirmed candidate sources are available for a tailored CV.", preflight_state="candidate_not_ready")
        full_fingerprint = _fingerprint({
            "candidate_evaluation": evaluation.candidate_evaluation_fingerprint,
            "context": context.model_dump(mode="json"),
            "structured_profile": snapshot.structured_profile.model_dump(mode="json"),
            "identity": identity.model_dump(mode="json"),
        })
        return _Authority(
            job=job, evaluation=evaluation, opportunity=opportunity, target=target, identity=identity,
            context=context, structured=snapshot.structured_profile, sources=sources,
            candidate_fingerprint=full_fingerprint,
            runtime_fingerprint=_runtime_fingerprint(runtime),
        )

    def _strategy_context(self, authority: _Authority, target_pages: int) -> dict[str, object]:
        requirements = []
        for match in sorted(authority.target.requirement_matches, key=lambda item: item.requirement_index):
            requirements.append({
                "requirement_index": match.requirement_index,
                "recommendation_id": f"requirement-{match.requirement_index}",
                "text": match.requirement.text,
                "importance": match.requirement.importance.value,
                "category": match.requirement.category.value,
                "match_type": match.match_type.value,
                "evidence_refs": [item.model_dump(mode="json") for item in match.evidence_refs],
            })
        if not requirements or len(requirements) > MAX_REQUIREMENTS:
            raise ApplicationCVTailoringError("This job's requirement set exceeds the bounded tailoring limit.", status_code=413)
        base = self._application_service._draft_context(authority.target, authority.sources, authority.structured)
        return {
            **base,
            "requirements": requirements,
            "target_pages": target_pages,
            "method": "evidence_grounded_ats_friendly_cv_tailoring_v1",
            "candidate_authority": "confirmed canonical Profile and active confirmed Career Adviser evidence",
        }

    def _draft_context(
        self, authority: _Authority, strategy: TailoringStrategy, review: dict[str, object],
        selected: dict[tuple[str, str], str], structural: dict[tuple[str, str], str], target_pages: int,
    ) -> dict[str, object]:
        allowed = {**selected, **structural}
        base = self._application_service._draft_context(authority.target, allowed, authority.structured)
        return {
            **base,
            "sources": [
                {"source_type": kind, "source_ref": ref, "text": value}
                for (kind, ref), value in sorted(allowed.items())
            ],
            "selected_support_refs": [
                {"source_type": kind, "source_ref": ref}
                for kind, ref in sorted(selected)
            ],
            "structural_attribution_refs": [
                {"source_type": kind, "source_ref": ref}
                for kind, ref in sorted(structural)
            ],
            "allowed_skills": sorted({
                value.strip() for (kind, _ref), value in selected.items()
                if kind == EvidenceSourceType.SKILLS.value and value.strip()
            }, key=str.casefold),
            "requirements": [
                {
                    "requirement_index": item.requirement_index,
                    "text": item.requirement.text,
                    "importance": item.requirement.importance.value,
                    "category": item.requirement.category.value,
                    "match_type": item.match_type.value,
                }
                for item in sorted(authority.target.requirement_matches, key=lambda value: value.requirement_index)
            ],
            "accepted_strategy": strategy.model_dump(mode="json"),
            "user_review": review,
            "target_pages": target_pages,
            "tailoring_contract": (
                "Only accepted recommendation sources are substantive content support. Structural employment/project refs are for attribution only; "
                "cite one selected support ref plus the matching role/project anchor for each role/project bullet. "
                "Only the selected allowed_skills may appear. User positioning is not candidate evidence."
            ),
        }

    def _selected_sources(
        self, sources: dict[tuple[str, str], str], strategy: TailoringStrategy, review: dict[str, object],
    ) -> dict[tuple[str, str], str]:
        choices = {
            item["recommendation_id"]: item
            for item in review.get("choices", []) if isinstance(item, dict) and item.get("recommendation_id")
        }
        selected: set[tuple[str, str]] = set()
        for recommendation in strategy.recommendations:
            choice = choices.get(recommendation.recommendation_id)
            if choice and choice.get("decision") != "skip":
                selected.update((ref.source_type.value, ref.source_ref) for ref in recommendation.source_refs)
        return {key: value for key, value in sources.items() if key in selected}

    @staticmethod
    def _structural_anchor_sources(
        authority: _Authority, selected: dict[tuple[str, str], str],
    ) -> dict[tuple[str, str], str]:
        """Keep identity-only role/project anchors separate from selected evidence."""
        anchors: dict[tuple[str, str], str] = {}
        for (kind, ref) in authority.sources:
            if (kind, ref) in selected:
                continue
            if kind == EvidenceSourceType.EMPLOYMENT.value and ref.startswith("employment:"):
                try:
                    index = int(ref.split(":", 1)[1])
                    item = authority.structured.employment[index]
                except (ValueError, IndexError):
                    continue
                identity = [item.title, item.employer]
                if item.start_date or item.end_date:
                    identity.append(" – ".join(value for value in (item.start_date, item.end_date) if value))
                if item.location:
                    identity.append(item.location)
                anchors[(kind, ref)] = ". ".join(value for value in identity if value)
            elif kind == EvidenceSourceType.PROJECT.value and ref.startswith("project:"):
                try:
                    index = int(ref.split(":", 1)[1])
                    item = authority.structured.projects[index]
                except (ValueError, IndexError):
                    continue
                anchors[(kind, ref)] = item.name
        return anchors

    def _validate_strategy(self, strategy: TailoringStrategy, authority: _Authority) -> None:
        matches = {item.requirement_index: item for item in authority.target.requirement_matches}
        seen: set[int] = set()
        source_keys = set(authority.sources)
        for item in strategy.recommendations:
            match = matches.get(item.requirement_index)
            if match is None or item.recommendation_id != f"requirement-{item.requirement_index}" or item.requirement_index in seen:
                raise SemanticOutputError("CV tailoring returned a duplicate or noncanonical requirement mapping.")
            seen.add(item.requirement_index)
            if any((ref.source_type.value, ref.source_ref) not in source_keys for ref in item.source_refs):
                raise SemanticOutputError("CV tailoring cited a source outside the current bounded Profile.")
            if match.match_type.value not in {"demonstrated", "transferable"} and item.action is not TailoringAction.OMIT:
                raise SemanticOutputError("CV tailoring promoted an unsupported requirement.")
        gap_indexes = [item.requirement_index for item in strategy.gaps_to_clarify]
        if len(gap_indexes) != len(set(gap_indexes)) or any(index not in matches for index in gap_indexes):
            raise SemanticOutputError("CV tailoring returned duplicate or noncanonical gap mappings.")
        if len({item.requirement_index for item in strategy.recommendations}) != len(strategy.recommendations):
            raise SemanticOutputError("CV tailoring returned duplicate requirement mappings.")

    @staticmethod
    def _validate_draft_strategy_refs(
        draft, selected: dict[tuple[str, str], str], structural: dict[tuple[str, str], str], data: CandidateCVData,
    ) -> None:
        allowed = set(selected) | set(structural)
        summary_refs = {(ref.source_type.value, ref.source_ref) for ref in draft.summary_source_refs}
        if not summary_refs or not summary_refs <= set(selected):
            raise ValueError("The CV summary must cite only evidence selected in the accepted strategy.")
        selected_skills = {
            value.strip().casefold() for (kind, _ref), value in selected.items()
            if kind == EvidenceSourceType.SKILLS.value and value.strip()
        }
        if any(skill.strip().casefold() not in selected_skills for skill in draft.key_skills):
            raise ValueError("The CV draft reintroduced a skill that was not selected in the accepted strategy.")
        for role in draft.role_drafts:
            anchor = (EvidenceSourceType.EMPLOYMENT.value, f"employment:{role.employment_index}")
            if anchor not in allowed:
                raise ValueError("The CV draft references a role without a current structural attribution anchor.")
            for bullet in role.bullets:
                refs = {(ref.source_type.value, ref.source_ref) for ref in bullet.source_refs}
                if not refs <= allowed or anchor not in refs or not refs.intersection(selected):
                    raise ValueError("Each role bullet needs selected Profile support and its matching role attribution anchor.")
        for project in draft.project_drafts:
            anchor = (EvidenceSourceType.PROJECT.value, f"project:{project.project_index}")
            refs = {(ref.source_type.value, ref.source_ref) for ref in project.source_refs}
            if not refs <= allowed or anchor not in refs or not refs.intersection(selected):
                raise ValueError("Each project bullet needs selected Profile support and its matching project attribution anchor.")

    @staticmethod
    def _draft_claim_content(draft: CVWritingDraft) -> dict[str, object]:
        return {
            "professional_summary": draft.professional_summary,
            "summary_source_refs": sorted((item.source_type.value, item.source_ref) for item in draft.summary_source_refs),
            "key_skills": [value.casefold() for value in draft.key_skills],
            "roles": {
                item.employment_index: sorted(
                    (bullet.text, tuple(sorted((ref.source_type.value, ref.source_ref) for ref in bullet.source_refs)))
                    for bullet in item.bullets
                )
                for item in draft.role_drafts
            },
            "projects": {
                item.project_index: (
                    item.text, tuple(sorted((ref.source_type.value, ref.source_ref) for ref in item.source_refs))
                )
                for item in draft.project_drafts
            },
        }

    @staticmethod
    def _positioning_status(strategy: TailoringStrategy, review: dict[str, object]) -> str:
        override = (review.get("positioning_override") or "").strip()
        changed = bool(override and _normalise_claim_text(override) != _normalise_claim_text(strategy.positioning))
        if not changed:
            return "not_overridden"
        return str(review.get("positioning_review_status") or "unverified_needs_acknowledgement")

    @staticmethod
    def _draft_factual_review(row: ApplicationCVTailoringSession) -> dict[str, object]:
        history = json.loads(row.draft_history_json or "[]")
        if not history or not row.draft_json:
            return {"status": "not_started", "acknowledged": False, "changed_from_model_draft": False}
        saved = history[-1].get("factual_review")
        if saved:
            return saved
        try:
            generated = CVWritingDraft.model_validate(history[0]["draft"])
            current = CVWritingDraft.model_validate_json(row.draft_json)
        except (KeyError, ValueError, TypeError):
            return {"status": "user_edits_unverified", "acknowledged": False, "changed_from_model_draft": True}
        changed = ApplicationCVTailoringService._draft_claim_content(generated) != ApplicationCVTailoringService._draft_claim_content(current)
        return {
            "status": "user_edits_unverified" if changed else "model_generated_not_semantically_verified",
            "acknowledged": False, "changed_from_model_draft": changed,
        }

    @staticmethod
    def _check_input_budget(value: dict[str, object]) -> None:
        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) > MAX_INPUT_CHARACTERS:
            raise ApplicationCVTailoringError("The confirmed Profile and job requirements exceed the bounded CV tailoring input.", status_code=413)

    def _finish_strategy_failure(self, row, user_id: str, code: str, *, uncertain: bool, invalid: bool = False):
        if uncertain:
            row.strategy_status = TailoringAttemptStatus.OUTCOME_UNCERTAIN.value
            row.status = TailoringStatus.STRATEGY_UNCERTAIN.value
        elif invalid:
            row.strategy_status = TailoringAttemptStatus.RESULT_INVALID.value
            row.status = TailoringStatus.STRATEGY_INVALID.value
        else:
            row.strategy_status = TailoringAttemptStatus.FAILED.value
            row.status = TailoringStatus.STRATEGY_FAILED.value
        row.failure_code = code
        row.updated_at = _now()
        self._session.commit()
        return self._read(row, user_id)

    def _finish_draft_failure(self, row, user_id: str, code: str, *, uncertain: bool, invalid: bool = False):
        if uncertain:
            row.draft_status = TailoringAttemptStatus.OUTCOME_UNCERTAIN.value
            row.status = TailoringStatus.DRAFT_UNCERTAIN.value
        elif invalid:
            row.draft_status = TailoringAttemptStatus.RESULT_INVALID.value
            row.status = TailoringStatus.DRAFT_INVALID.value
        else:
            row.draft_status = TailoringAttemptStatus.FAILED.value
            row.status = TailoringStatus.DRAFT_FAILED.value
        row.failure_code = code
        row.updated_at = _now()
        self._session.commit()
        return self._read(row, user_id)

    def _strategy_and_review(self, row) -> tuple[TailoringStrategy, dict[str, object]]:
        if not row.strategy_json:
            raise ApplicationCVTailoringError("This tailoring session has no completed strategy.")
        return TailoringStrategy.model_validate_json(row.strategy_json), json.loads(row.strategy_review_json or "{}")

    def _is_current(self, row: ApplicationCVTailoringSession, user_id: str) -> bool:
        try:
            runtime = self._current_runtime(user_id)
            authority = self._authority(user_id, row.discovered_job_id, runtime)
            saved_target = ApplicationTargetSnapshot.model_validate_json(row.target_snapshot_json)
            return (
                row.evaluation_id == authority.evaluation.id
                and row.candidate_evaluation_fingerprint == authority.evaluation.candidate_evaluation_fingerprint
                and row.evaluation_contract_fingerprint == authority.evaluation.evaluation_contract_fingerprint
                and saved_target.job_content_hash == authority.job.content_hash
                and row.candidate_fingerprint == authority.candidate_fingerprint
                and row.runtime_fingerprint == authority.runtime_fingerprint
                and _json(ApplicationIdentitySnapshot.model_validate_json(row.identity_snapshot_json)) == _json(authority.identity)
            )
        except Exception:
            return False

    def _read(self, row: ApplicationCVTailoringSession, user_id: str) -> TailoringSessionRead:
        current = self._is_current(row, user_id)
        status = TailoringStatus(row.status)
        strategy_status = TailoringAttemptStatus(row.strategy_status)
        draft_status = TailoringAttemptStatus(row.draft_status)
        age = _now() - _aware(row.updated_at)
        if age > timedelta(seconds=PROVIDER_TIMEOUT_SECONDS + 10):
            if status is TailoringStatus.STRATEGY_IN_PROGRESS:
                status = TailoringStatus.STRATEGY_UNCERTAIN
                strategy_status = TailoringAttemptStatus.OUTCOME_UNCERTAIN
            elif status is TailoringStatus.DRAFT_IN_PROGRESS:
                status = TailoringStatus.DRAFT_UNCERTAIN
                draft_status = TailoringAttemptStatus.OUTCOME_UNCERTAIN
        if not current and status is not TailoringStatus.APPROVED:
            status = TailoringStatus.STALE
        review = json.loads(row.strategy_review_json or "{}")
        strategy = TailoringStrategy.model_validate_json(row.strategy_json) if row.strategy_json else None
        factual_review = self._draft_factual_review(row)
        positioning_status = self._positioning_status(strategy, review) if strategy else "not_overridden"
        from app.schemas.application_cv_tailoring import TailoringStrategyReview
        choices = [TailoringStrategyReview.model_validate(item) for item in review.get("choices", [])]
        return TailoringSessionRead(
            id=row.id, client_request_id=row.client_request_id, discovered_job_id=row.discovered_job_id,
            status=status, strategy_status=strategy_status, draft_status=draft_status, is_current=current,
            target=ApplicationTargetSnapshot.model_validate_json(row.target_snapshot_json),
            identity=ApplicationIdentitySnapshot.model_validate_json(row.identity_snapshot_json),
            profile_outline=TailoringProfileOutline.model_validate_json(row.profile_outline_json or "{}"),
            strategy=strategy, strategy_accepted=bool(review.get("accepted", False)),
            strategy_positioning_override=review.get("positioning_override"),
            strategy_positioning_review_status=positioning_status,
            strategy_choices=choices,
            strategy_revision=row.strategy_revision,
            draft=CVWritingDraft.model_validate_json(row.draft_json) if row.draft_json else None,
            draft_revision=row.draft_revision,
            draft_claim_review_status=factual_review.get("status", "not_started"),
            draft_claim_review_acknowledged=bool(factual_review.get("acknowledged", False)),
            target_pages=row.target_pages,
            failure_code=row.failure_code, approved_preparation_id=row.approved_preparation_id,
            runtime_attribution=read_attribution(row.runtime_attribution_json),
            created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
        )

    def _mark_stale(self, row: ApplicationCVTailoringSession) -> None:
        if row.status != TailoringStatus.APPROVED.value:
            row.status = TailoringStatus.STALE.value
            row.strategy_status = TailoringAttemptStatus.STALE.value if row.strategy_status == TailoringAttemptStatus.COMPLETED.value else row.strategy_status
            if row.draft_status == TailoringAttemptStatus.COMPLETED.value:
                row.draft_status = TailoringAttemptStatus.STALE.value
            row.failure_code = "authority_changed"
            row.updated_at = _now()
            self._session.commit()

    def _target_for_row(self, row: ApplicationCVTailoringSession) -> ApplicationTargetSnapshot:
        return ApplicationTargetSnapshot.model_validate_json(row.target_snapshot_json)

    def _owned(self, user_id: str, tailoring_id: str) -> ApplicationCVTailoringSession:
        row = self._session.scalar(select(ApplicationCVTailoringSession).where(
            ApplicationCVTailoringSession.id == tailoring_id,
            ApplicationCVTailoringSession.user_id == user_id,
        ))
        if row is None:
            raise LookupError("Tailored CV request not found.")
        return row

    def _current_runtime(self, user_id: str) -> ResolvedRuntimeSnapshot:
        return self._runtime_snapshot_resolver(user_id)


def _target_snapshot(job: DiscoveredJob, opportunity: RankedJobOpportunity):
    from app.schemas.application_preparation import ApplicationTargetKind
    profile = opportunity.job_profile
    return ApplicationTargetSnapshot(
        source_kind=ApplicationTargetKind.DISCOVERED_JOB,
        canonical_discovered_job_id=job.id, public_url=job.url,
        title=profile.title or job.title, company=profile.company or job.company,
        location=profile.location or job.location, work_arrangement=profile.work_arrangement or job.work_arrangement,
        employment_type=profile.employment_type or job.employment_type,
        job_profile=profile, requirement_matches=opportunity.requirement_matches,
        job_content_hash=job.content_hash,
    )


def _profile_outline(data: CandidateCVData) -> TailoringProfileOutline:
    """Save only the structural identity needed to review a historical draft."""
    return TailoringProfileOutline(
        employment=[TailoringEmploymentOutline(
            employment_index=index, employer=item.employer, title=item.title,
            start_date=item.start_date, end_date=item.end_date, location=item.location,
        ) for index, item in enumerate(data.employment[:20])],
        projects=[TailoringProjectOutline(project_index=index, name=item.name) for index, item in enumerate(data.projects[:30])],
        education=[f"{item.qualification} — {item.institution}" for item in data.education[:30]],
        credentials=[item.name for item in data.credentials[:30]],
    )


def _runtime_fingerprint(runtime: ResolvedRuntimeSnapshot) -> str:
    return _fingerprint(runtime.fingerprint_projection((SemanticOperation.APPLICATION_DRAFTING,)))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat()


def _normalise_claim_text(value: str) -> str:
    return " ".join(value.split()).casefold()
