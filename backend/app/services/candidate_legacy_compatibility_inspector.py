import json
from collections.abc import Iterable
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application_preparation import ApplicationPreparation
from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
)
from app.models.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalRecord
from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateStructuredProfile,
)
from app.models.candidate_profile import CandidateProfile
from app.models.candidate_profile_revision import CandidateProfileRevisionRecord
from app.models.candidate_structured_item_lineage import CandidateStructuredItemLineageRecord
from app.models.user_job_discovery import DiscoveryRun, UserJobEvaluation
from app.schemas.candidate_adviser import (
    CandidateAdviserAssessmentStatus,
    CandidateAdviserClarificationStatus,
    ClarificationAnswerKind,
    ClarificationInterpretation,
)
from app.schemas.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalState
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityAction,
    CandidateCompatibilityPlan,
    CandidateCompatibilityStatus,
    CandidateCompatibilitySummary,
    CandidateEvidenceCompatibilityStatus,
    CandidateLegacyIssue,
    CandidateLegacyIssueCode,
    CandidateSchemaStatus,
    CandidateStructuredAuthorityStatus,
    CandidateUserCompatibilityRead,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.profile_revision import CandidateProfileRevisionState
from app.schemas.structured_profile import StructuredProfileSection
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_adviser_service import CandidateAdviserService
from app.services.candidate_compatibility_inspector import (
    CandidatePhysicalSchemaInspector,
    CANDIDATE_DOMAIN_TABLES,
)
from app.services.structured_profile_comparison import StructuredProfileComparisonService
from app.services.structured_profile_identity import structured_profile_item_fingerprint


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_candidate_cv(raw: str | None) -> CandidateCVData:
    if raw is None:
        raise ValueError("Confirmed CV merged_json is null.")
    return CandidateCVData.model_validate(json.loads(raw))


def _time_key(value: datetime | None) -> tuple[int, int, int, int, int, int, int]:
    if value is None:
        return (0, 0, 0, 0, 0, 0, 0)
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return (
        normalized.year, normalized.month, normalized.day, normalized.hour,
        normalized.minute, normalized.second, normalized.microsecond,
    )


class CandidateLegacyCompatibilityInspector:
    """Provider-free per-user dry-run classification; performs no writes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def inspect_user(self, user_id: str) -> CandidateUserCompatibilityRead:
        return self._inspect_user(user_id)

    def inspect_users(self, user_ids: Iterable[str]) -> list[CandidateUserCompatibilityRead]:
        return [self._inspect_user(user_id) for user_id in sorted(set(user_ids))]

    def _inspect_user(self, user_id: str) -> CandidateUserCompatibilityRead:
        issues: list[CandidateLegacyIssue] = []
        actions: list[CandidateCompatibilityAction] = []
        try:
            profile = self._session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
            structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
            drafts = self._session.scalars(
                select(CandidateCVIngestionDraft)
                .where(CandidateCVIngestionDraft.user_id == user_id)
                .order_by(CandidateCVIngestionDraft.updated_at, CandidateCVIngestionDraft.id)
            ).all()
            authority, data, confirmed_count, latest_id, equivalent_count, cv_unconfirmed, unresolved = self._classify_structured_authority(
                user_id, profile is not None, structured, drafts, issues, actions
            )

            if unresolved:
                status = CandidateCompatibilityStatus.UNRESOLVED
                evidence_data = data or CandidateCVData()
            else:
                evidence_data = data or CandidateCVData()

            active = ActiveCandidateEvidenceResolver(self._session).inspect_active(user_id, evidence_data)
            has_missing_evidence = bool(active.missing_fingerprints)
            has_stale_evidence = bool(active.stale_fingerprints)
            if has_missing_evidence and has_stale_evidence:
                evidence_status = CandidateEvidenceCompatibilityStatus.MISSING_AND_STALE
            elif has_missing_evidence:
                evidence_status = CandidateEvidenceCompatibilityStatus.MISSING
            elif has_stale_evidence:
                evidence_status = CandidateEvidenceCompatibilityStatus.STALE
            else:
                evidence_status = CandidateEvidenceCompatibilityStatus.COMPLETE
            if not active.complete:
                actions.append(CandidateCompatibilityAction.RECONCILE_ACTIVE_EVIDENCE)
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.EVIDENCE_INCOMPLETE,
                    detail="Current resolver inspection found active evidence rows missing or stale.",
                    count=len(active.missing_fingerprints) + len(active.stale_fingerprints),
                ))
                if active.stale_fingerprints and not active.missing_fingerprints:
                    issues.append(CandidateLegacyIssue(
                        code=CandidateLegacyIssueCode.LEGACY_EVIDENCE_REUSABLE_BUT_STALE,
                        detail="The existing resolver found matching persisted rows through its legacy fingerprint bridge; current metadata/fingerprint still needs reconciliation.",
                        count=len(active.stale_fingerprints),
                    ))

            assessment_record_exists = self._session.scalar(select(
                CandidateAdviserAssessmentRecord.id
            ).where(CandidateAdviserAssessmentRecord.user_id == user_id)) is not None
            if active.complete:
                assessment_status = self._assessment_status(user_id)
                if assessment_status != "unavailable":
                    issues.append(CandidateLegacyIssue(
                        code=CandidateLegacyIssueCode.ADVISER_ASSESSMENT_STATUS,
                        detail="Persisted Adviser assessment is inspected read-only and is not rerun or confirmed.",
                        count=1,
                    ))
            else:
                assessment_status = "unavailable"
                if assessment_record_exists:
                    issues.append(CandidateLegacyIssue(
                        code=CandidateLegacyIssueCode.ADVISER_ASSESSMENT_STATUS,
                        detail="A persisted Adviser assessment exists, but its currentness is unavailable until active evidence is complete.",
                        count=1,
                    ))

            clarifications = self._session.scalars(
                select(CandidateAdviserClarificationRecord).where(
                    CandidateAdviserClarificationRecord.user_id == user_id
                ).order_by(CandidateAdviserClarificationRecord.clarification_id)
            ).all()
            confirmed_factual_count = 0
            unconfirmed_clarification_count = 0
            for clarification in clarifications:
                if clarification.status != CandidateAdviserClarificationStatus.CONFIRMED:
                    unconfirmed_clarification_count += 1
                    continue
                if not clarification.interpretation_json:
                    continue
                try:
                    interpretation = ClarificationInterpretation.model_validate_json(clarification.interpretation_json)
                except (ValidationError, ValueError, TypeError):
                    continue
                if interpretation.answer_kind in {ClarificationAnswerKind.CAREER_FACT, ClarificationAnswerKind.MIXED}:
                    confirmed_factual_count += 1
            if confirmed_factual_count:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.CONFIRMED_FACTUAL_CLARIFICATIONS,
                    detail="Confirmed factual/mixed clarifications are evidence sources only; they do not reconstruct structured Profile items.",
                    count=confirmed_factual_count,
                ))
            if unconfirmed_clarification_count:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.UNCONFIRMED_CLARIFICATIONS_IGNORED,
                    detail="Unconfirmed clarifications are excluded from active factual evidence and structured authority.",
                    count=unconfirmed_clarification_count,
                ))

            revisions = self._session.scalars(select(CandidateProfileRevisionRecord).where(
                CandidateProfileRevisionRecord.user_id == user_id
            )).all()
            pending_revision_count = sum(
                row.state in {CandidateProfileRevisionState.DRAFT, CandidateProfileRevisionState.REVIEW_READY}
                for row in revisions
            )
            historical_revision_count = len(revisions) - pending_revision_count
            if pending_revision_count:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.PENDING_PROFILE_REVISIONS,
                    detail="Draft and review-ready Profile revisions remain noncanonical and are not promoted.",
                    count=pending_revision_count,
                ))
            if historical_revision_count:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.HISTORICAL_PROFILE_REVISIONS,
                    detail="Confirmed/discarded Profile revisions remain history; current authority is preserved.",
                    count=historical_revision_count,
                ))

            proposals = self._session.scalars(select(CandidateAdviserProfileProposalRecord).where(
                CandidateAdviserProfileProposalRecord.user_id == user_id
            )).all()
            pending_proposal_count = sum(row.state == CandidateAdviserProfileProposalState.PENDING for row in proposals)
            historical_proposal_count = len(proposals) - pending_proposal_count
            if proposals:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.ADVISER_PROPOSALS_PRESERVED,
                    detail="Pending, rejected, and transferred Adviser proposals remain noncanonical history.",
                    count=len(proposals),
                ))

            lineage_count = self._session.scalar(select(func.count()).select_from(
                CandidateStructuredItemLineageRecord
            ).where(CandidateStructuredItemLineageRecord.user_id == user_id)) or 0
            if data is not None and not lineage_count and any(getattr(data, section.value) for section in StructuredProfileSection):
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.LINEAGE_UNAVAILABLE,
                    detail="Current structured facts without prospective lineage remain valid; historical provenance is not inferred.",
                ))

            duplicate_count = self._duplicate_fact_count(data) if data is not None else 0
            if duplicate_count:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.DUPLICATE_CURRENT_FACT,
                    detail="Duplicate current structured occurrences are reported but are not removed or rewritten.",
                    count=duplicate_count,
                ))

            discovery_count = (
                self._session.scalar(select(func.count()).select_from(DiscoveryRun).where(DiscoveryRun.user_id == user_id)) or 0
            ) + (
                self._session.scalar(select(func.count()).select_from(UserJobEvaluation).where(UserJobEvaluation.user_id == user_id)) or 0
            )
            application_count = self._session.scalar(select(func.count()).select_from(
                ApplicationPreparation
            ).where(ApplicationPreparation.user_id == user_id)) or 0
            if discovery_count or application_count:
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.HISTORICAL_ARTIFACTS_PRESERVED,
                    detail="Existing discovery and application artifacts are counted only and remain immutable history.",
                    count=discovery_count + application_count,
                ))

            if unresolved:
                status = CandidateCompatibilityStatus.UNRESOLVED
            elif authority is CandidateStructuredAuthorityStatus.RECONSTRUCTABLE_FROM_CONFIRMED_CV or not active.complete:
                status = CandidateCompatibilityStatus.REPAIRABLE
            elif authority is CandidateStructuredAuthorityStatus.MISSING_UNCONFIRMED:
                status = CandidateCompatibilityStatus.NOT_YET_CONFIRMED
                if profile is not None:
                    issues.append(CandidateLegacyIssue(
                        code=CandidateLegacyIssueCode.PROFILE_ONLY_USER,
                        detail="Scalar Profile remains readable; no structured facts or evidence are inferred from it.",
                    ))
            else:
                status = CandidateCompatibilityStatus.ALREADY_COMPATIBLE

            if not actions:
                actions.append(CandidateCompatibilityAction.NO_ACTION)
            return CandidateUserCompatibilityRead(
                user_id=user_id,
                status=status,
                profile_exists=profile is not None,
                structured_authority=authority,
                confirmed_cv_count=confirmed_count,
                latest_confirmed_cv_id=latest_id,
                equivalent_latest_confirmed_source_count=equivalent_count,
                unconfirmed_cv_count=cv_unconfirmed,
                evidence_status=evidence_status,
                expected_evidence_count=active.expected_count,
                active_evidence_count=len(active.evidence),
                missing_evidence_count=len(active.missing_fingerprints),
                stale_evidence_count=len(active.stale_fingerprints),
                confirmed_factual_clarification_count=confirmed_factual_count,
                unconfirmed_clarification_count=unconfirmed_clarification_count,
                adviser_assessment_status=assessment_status,
                pending_profile_revision_count=pending_revision_count,
                historical_profile_revision_count=historical_revision_count,
                pending_adviser_proposal_count=pending_proposal_count,
                historical_adviser_proposal_count=historical_proposal_count,
                lineage_event_count=lineage_count,
                duplicate_current_fact_count=duplicate_count,
                discovery_artifact_count=discovery_count,
                application_artifact_count=application_count,
                issues=issues,
                planned_actions=list(dict.fromkeys(actions)),
            )
        except Exception as exc:
            # The plan is resumable across users: a malformed user's row does
            # not abort classification of other explicitly requested owners.
            return CandidateUserCompatibilityRead(
                user_id=user_id,
                status=CandidateCompatibilityStatus.UNRESOLVED,
                profile_exists=False,
                structured_authority=CandidateStructuredAuthorityStatus.UNRESOLVED,
                evidence_status=CandidateEvidenceCompatibilityStatus.UNAVAILABLE,
                issues=[CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.INSPECTION_FAILED,
                    detail=f"User compatibility inspection failed safely ({type(exc).__name__}).",
                    blocking=True,
                )],
                planned_actions=[CandidateCompatibilityAction.MANUAL_RESOLUTION_REQUIRED],
            )

    def _classify_structured_authority(self, user_id, profile_exists, structured, drafts, issues, actions):
        confirmed = [draft for draft in drafts if draft.state == "confirmed"]
        unconfirmed = [draft for draft in drafts if draft.state != "confirmed"]

        if structured is not None:
            try:
                data = CandidateCVData.model_validate_json(structured.structured_json)
            except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.INVALID_CURRENT_STRUCTURED_PROFILE,
                    detail="Current structured authority exists but does not validate as current CandidateCVData.",
                    related_ids=[structured.id],
                    blocking=True,
                ))
                return CandidateStructuredAuthorityStatus.UNRESOLVED, None, len(confirmed), None, 0, len(unconfirmed), True

            actions.append(CandidateCompatibilityAction.PRESERVE_CURRENT_STRUCTURED)
            latest_valid = self._latest_confirmed(confirmed, issues, strict=False)
            if latest_valid is not None and _canonical(latest_valid[1].model_dump(mode="json")) != _canonical(data.model_dump(mode="json")):
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.HISTORICAL_SOURCE_DIFFERS,
                    detail="A confirmed CV source differs from current structured authority; the current authority wins and is preserved.",
                    related_ids=[latest_valid[0].id],
                ))
            return CandidateStructuredAuthorityStatus.PRESERVE, data, len(confirmed), latest_valid[0].id if latest_valid else None, latest_valid[2] if latest_valid else 0, len(unconfirmed), False

        latest = self._latest_confirmed(confirmed, issues, strict=True)
        if confirmed and latest is None:
            return CandidateStructuredAuthorityStatus.UNRESOLVED, None, len(confirmed), None, 0, len(unconfirmed), True
        if latest is not None:
            actions.append(CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV)
            return CandidateStructuredAuthorityStatus.RECONSTRUCTABLE_FROM_CONFIRMED_CV, latest[1], len(confirmed), latest[0].id, latest[2], len(unconfirmed), False
        if unconfirmed:
            issues.append(CandidateLegacyIssue(
                code=CandidateLegacyIssueCode.UNCONFIRMED_CV_PRESENT,
                detail="Uploaded and review-ready CV drafts remain unconfirmed and are not promoted.",
                count=len(unconfirmed),
            ))
        return CandidateStructuredAuthorityStatus.MISSING_UNCONFIRMED, None, 0, None, 0, len(unconfirmed), False

    @staticmethod
    def _latest_confirmed(confirmed, issues, *, strict):
        if not confirmed:
            return None
        newest_key = max(_time_key(row.updated_at) for row in confirmed)
        latest_rows = [row for row in confirmed if _time_key(row.updated_at) == newest_key]
        parsed: list[tuple[CandidateCVIngestionDraft, CandidateCVData]] = []
        for row in latest_rows:
            try:
                parsed.append((row, _parse_candidate_cv(row.merged_json)))
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
                issues.append(CandidateLegacyIssue(
                    code=CandidateLegacyIssueCode.INVALID_CONFIRMED_CV,
                    detail="The latest confirmed CV payload is malformed or does not validate as current CandidateCVData.",
                    related_ids=[row.id],
                    blocking=strict,
                ))
                return None
        payloads = {_canonical(data.model_dump(mode="json")) for _, data in parsed}
        if len(payloads) > 1:
            issues.append(CandidateLegacyIssue(
                code=CandidateLegacyIssueCode.AMBIGUOUS_CONFIRMED_CVS,
                detail="Equally latest confirmed CV drafts contain different payloads; chronology cannot be inferred from their IDs.",
                count=len(parsed),
                related_ids=sorted(row.id for row, _ in parsed),
                blocking=strict,
            ))
            return None
        # The ID is used only to select a reproducible representative when the
        # equally latest payloads are identical, never to invent chronology.
        row, data = sorted(parsed, key=lambda pair: pair[0].id)[0]
        return row, data, len(parsed)

    def _assessment_status(self, user_id: str) -> str:
        try:
            assessment = CandidateAdviserService(self._session).get_assessment_read_only(user_id)
            return assessment.status.value if assessment is not None else "unavailable"
        except Exception:
            return "unavailable"

    @staticmethod
    def _duplicate_fact_count(data: CandidateCVData) -> int:
        comparator = StructuredProfileComparisonService()
        duplicate_occurrences = 0
        for section in StructuredProfileSection:
            prior = []
            prior_fingerprints: set[str] = set()
            for item in getattr(data, section.value):
                fingerprint = structured_profile_item_fingerprint(section, item)
                compared = comparator.compare(section, item, prior)
                if fingerprint in prior_fingerprints or compared.relationship.value == "reinforcement":
                    duplicate_occurrences += 1
                prior.append(item)
                prior_fingerprints.add(fingerprint)
        return duplicate_occurrences


class CandidateCompatibilityDryRunService:
    """Combined physical-schema and per-user dry-run report; no migration writes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def inspect(self, user_ids: Iterable[str]) -> CandidateCompatibilityPlan:
        schema = CandidatePhysicalSchemaInspector(self._session).inspect()
        users: list[CandidateUserCompatibilityRead] = []
        if schema.status is CandidateSchemaStatus.COMPATIBLE:
            users = CandidateLegacyCompatibilityInspector(self._session).inspect_users(user_ids)
        counts = {status: sum(user.status is status for user in users) for status in CandidateCompatibilityStatus}
        return CandidateCompatibilityPlan(
            schema=schema,
            users=users,
            summary=CandidateCompatibilitySummary(
                user_count=len(users),
                already_compatible=counts[CandidateCompatibilityStatus.ALREADY_COMPATIBLE],
                repairable=counts[CandidateCompatibilityStatus.REPAIRABLE],
                unresolved=counts[CandidateCompatibilityStatus.UNRESOLVED],
                not_yet_confirmed=counts[CandidateCompatibilityStatus.NOT_YET_CONFIRMED],
            ),
        )
