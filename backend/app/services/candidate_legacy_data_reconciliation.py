import json
from collections.abc import Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.user import User
from app.schemas.candidate_compatibility import (
    CandidateBatchReconciliationRead,
    CandidateCompatibilityAction,
    CandidateCompatibilityStatus,
    CandidateDataReconciliationAction,
    CandidateSchemaCompatibilityRead,
    CandidateSchemaStatus,
    CandidateStructuredAuthorityStatus,
    CandidateUserReconciliationRead,
)
from app.schemas.cv_ingestion import CandidateCVData
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.candidate_compatibility_inspector import CandidatePhysicalSchemaInspector
from app.services.candidate_legacy_compatibility_inspector import CandidateLegacyCompatibilityInspector


class CandidateDataReconciliationBlocked(RuntimeError):
    """Raised when the physical candidate schema is not ready for data repair."""

    def __init__(self, schema: CandidateSchemaCompatibilityRead) -> None:
        super().__init__(
            "Candidate data reconciliation requires a compatible physical schema; "
            f"inspection reported {schema.status.value}."
        )
        self.schema = schema


class CandidateLegacyDataReconciliationService:
    """Reconcile approved legacy candidate data, one owned transaction per user."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal

    def reconcile_user(self, user_id: str) -> CandidateUserReconciliationRead:
        observed: dict[str, object] = {"status": None, "evidence_count": 0}
        try:
            with self._session_factory() as session:
                with session.begin():
                    result = self._reconcile_user_in_transaction(session, user_id, observed)
                return result
        except CandidateDataReconciliationBlocked:
            raise
        except Exception as exc:
            status = observed["status"]
            count = int(observed["evidence_count"])
            return CandidateUserReconciliationRead(
                user_id=user_id,
                changed=False,
                status_before=status if isinstance(status, CandidateCompatibilityStatus) else None,
                status_after=status if isinstance(status, CandidateCompatibilityStatus) else None,
                actions=[CandidateDataReconciliationAction.UNRESOLVED],
                evidence_count_before=count,
                evidence_count_after=count,
                blocking_issues=[f"User reconciliation rolled back ({type(exc).__name__})."],
            )

    def reconcile_users(self, user_ids: Iterable[str]) -> CandidateBatchReconciliationRead:
        results: list[CandidateUserReconciliationRead] = []
        for user_id in sorted(set(user_ids)):
            try:
                results.append(self.reconcile_user(user_id))
            except CandidateDataReconciliationBlocked as exc:
                results.append(CandidateUserReconciliationRead(
                    user_id=user_id,
                    changed=False,
                    actions=[CandidateDataReconciliationAction.UNRESOLVED],
                    blocking_issues=[str(exc)],
                ))
        return CandidateBatchReconciliationRead(
            users=results,
            user_count=len(results),
            changed_count=sum(result.changed for result in results),
            unresolved_count=sum(
                CandidateDataReconciliationAction.UNRESOLVED in result.actions
                for result in results
            ),
        )

    def _reconcile_user_in_transaction(
        self,
        session: Session,
        user_id: str,
        observed: dict[str, object],
    ) -> CandidateUserReconciliationRead:
        if session.get_bind().dialect.name == "sqlite":
            # Pysqlite legacy transaction mode does not start a database
            # transaction for SELECT. The resolver uses a nested savepoint;
            # without a real outer BEGIN, releasing that savepoint can commit
            # evidence-only writes before the per-user transaction finishes.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        schema = CandidatePhysicalSchemaInspector(session).inspect()
        if schema.status is not CandidateSchemaStatus.COMPATIBLE:
            raise CandidateDataReconciliationBlocked(schema)

        if session.scalar(select(User.id).where(User.id == user_id)) is None:
            return CandidateUserReconciliationRead(
                user_id=user_id,
                changed=False,
                actions=[CandidateDataReconciliationAction.UNRESOLVED],
                blocking_issues=["Requested user does not exist; no user or candidate data was created."],
            )

        # This classification is deliberately rebuilt inside the owned user
        # transaction. Its selected CV ID is the same deterministic choice
        # used by the Phase 1 classifier for maximal timestamp ties.
        before = CandidateLegacyCompatibilityInspector(session).inspect_user(user_id)
        observed["status"] = before.status
        evidence_count_before = self._evidence_count(session, user_id)
        observed["evidence_count"] = evidence_count_before
        if before.status is CandidateCompatibilityStatus.UNRESOLVED:
            details = [issue.detail for issue in before.issues if issue.blocking]
            return CandidateUserReconciliationRead(
                user_id=user_id,
                changed=False,
                status_before=before.status,
                status_after=before.status,
                actions=[CandidateDataReconciliationAction.UNRESOLVED],
                evidence_count_before=evidence_count_before,
                evidence_count_after=evidence_count_before,
                blocking_issues=details or ["Phase 1 classification is unresolved."],
            )

        approved_phase3_actions = {
            CandidateCompatibilityAction.PRESERVE_CURRENT_STRUCTURED,
            CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV,
            CandidateCompatibilityAction.RECONCILE_ACTIVE_EVIDENCE,
            CandidateCompatibilityAction.NO_ACTION,
        }
        if any(action not in approved_phase3_actions for action in before.planned_actions):
            return CandidateUserReconciliationRead(
                user_id=user_id,
                changed=False,
                status_before=before.status,
                status_after=before.status,
                actions=[CandidateDataReconciliationAction.UNRESOLVED],
                evidence_count_before=evidence_count_before,
                evidence_count_after=evidence_count_before,
                blocking_issues=["Phase 1 classification contains an action outside the approved Phase 3 scope."],
            )

        structured = session.scalar(
            select(CandidateStructuredProfile)
            .where(CandidateStructuredProfile.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        source_cv_draft_id: str | None = None
        reconstructed = False
        data: CandidateCVData | None = None
        actions: list[CandidateDataReconciliationAction] = []

        if before.structured_authority is CandidateStructuredAuthorityStatus.PRESERVE:
            if structured is None:
                raise RuntimeError("Current structured authority changed during fresh classification.")
            data = CandidateCVData.model_validate_json(structured.structured_json)
        elif before.structured_authority is CandidateStructuredAuthorityStatus.RECONSTRUCTABLE_FROM_CONFIRMED_CV:
            if structured is not None:
                # A current authority may have appeared between classification
                # and this locked read. It wins over the older confirmed CV.
                data = CandidateCVData.model_validate_json(structured.structured_json)
            else:
                source_cv_draft_id = before.latest_confirmed_cv_id
                if source_cv_draft_id is None:
                    raise RuntimeError("Fresh classifier omitted its selected confirmed CV source.")
                source = session.scalar(
                    select(CandidateCVIngestionDraft)
                    .where(
                        CandidateCVIngestionDraft.id == source_cv_draft_id,
                        CandidateCVIngestionDraft.user_id == user_id,
                        CandidateCVIngestionDraft.state == "confirmed",
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if source is None:
                    raise RuntimeError("Freshly selected confirmed CV source is no longer available.")
                data = CandidateCVData.model_validate_json(source.merged_json)
                structured = CandidateStructuredProfile(
                    user_id=user_id,
                    structured_json=json.dumps(data.model_dump(mode="json")),
                )
                session.add(structured)
                session.flush()
                reconstructed = True
                actions.append(CandidateDataReconciliationAction.RECONSTRUCTED_STRUCTURED_PROFILE)
        elif before.structured_authority is CandidateStructuredAuthorityStatus.UNRESOLVED:
            return CandidateUserReconciliationRead(
                user_id=user_id,
                changed=False,
                status_before=before.status,
                status_after=before.status,
                actions=[CandidateDataReconciliationAction.UNRESOLVED],
                evidence_count_before=evidence_count_before,
                evidence_count_after=evidence_count_before,
                blocking_issues=["Current structured authority is unresolved and was preserved."],
            )
        else:
            # Scalar Profile and non-confirmed CV state are not migration
            # authority. Evidence is not created without structured truth.
            after = CandidateLegacyCompatibilityInspector(session).inspect_user(user_id)
            return CandidateUserReconciliationRead(
                user_id=user_id,
                changed=False,
                status_before=before.status,
                status_after=after.status,
                actions=[CandidateDataReconciliationAction.NO_ACTION],
                evidence_count_before=evidence_count_before,
                evidence_count_after=self._evidence_count(session, user_id),
            )

        if data is None:
            raise RuntimeError("Reconciliation has no validated structured data.")

        resolver = ActiveCandidateEvidenceResolver(session)
        active = resolver.inspect_active(user_id, data)
        evidence_reconciled = False
        if not active.complete:
            resolver.resolve(user_id, data)
            evidence_reconciled = True
            actions.append(CandidateDataReconciliationAction.RECONCILED_ACTIVE_EVIDENCE)

        session.flush()
        session.expire_all()
        persisted = session.scalar(
            select(CandidateStructuredProfile)
            .where(CandidateStructuredProfile.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if persisted is None:
            raise RuntimeError("Structured Profile postflight verification failed.")
        post_data = CandidateCVData.model_validate_json(persisted.structured_json)
        post_evidence = ActiveCandidateEvidenceResolver(session).inspect_active(user_id, post_data)
        if not post_evidence.complete:
            raise RuntimeError("Active evidence postflight verification failed.")

        after = CandidateLegacyCompatibilityInspector(session).inspect_user(user_id)
        if (
            after.status is not CandidateCompatibilityStatus.ALREADY_COMPATIBLE
            or after.structured_authority is not CandidateStructuredAuthorityStatus.PRESERVE
            or after.evidence_status.value != "complete"
            or CandidateCompatibilityAction.RECONSTRUCT_STRUCTURED_FROM_CONFIRMED_CV in after.planned_actions
            or CandidateCompatibilityAction.RECONCILE_ACTIVE_EVIDENCE in after.planned_actions
        ):
            raise RuntimeError("Compatibility classifier postflight verification failed.")

        if not actions:
            actions.append(CandidateDataReconciliationAction.NO_ACTION)
        evidence_count_after = self._evidence_count(session, user_id)
        return CandidateUserReconciliationRead(
            user_id=user_id,
            changed=reconstructed or evidence_reconciled,
            status_before=before.status,
            status_after=after.status,
            actions=actions,
            structured_reconstructed=reconstructed,
            evidence_reconciled=evidence_reconciled,
            evidence_count_before=evidence_count_before,
            evidence_count_after=evidence_count_after,
            source_cv_draft_id=source_cv_draft_id,
        )

    @staticmethod
    def _evidence_count(session: Session, user_id: str) -> int:
        return int(session.scalar(
            select(func.count()).select_from(CandidateEvidenceRecord)
            .where(CandidateEvidenceRecord.user_id == user_id)
        ) or 0)
