"""Provider-free, user-scoped durable job-decision authority."""

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.discovered_job import DiscoveredJob
from app.models.user_job_decision import UserJobDecision
from app.schemas.discovery import DiscoveredJobState, JobVerificationStatus
from app.schemas.user_job_decision import UserJobDecisionListItem, UserJobDecisionListResponse, UserJobDecisionMutation, UserJobDecisionRead, UserJobDecisionValue
from app.services.public_job_actionability import is_public_job_actionable


class UserJobDecisionConflict(Exception):
    pass


def utc_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class UserJobDecisionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def read(self, user_id: str, discovered_job_id: str) -> UserJobDecisionRead:
        if self._session.get(DiscoveredJob, discovered_job_id) is None:
            raise LookupError("Discovered job not found.")
        return self._read(self._row(user_id, discovered_job_id), discovered_job_id)

    def list(self, user_id: str, *, decision: UserJobDecisionValue, limit: int) -> UserJobDecisionListResponse:
        if decision is UserJobDecisionValue.UNDECIDED:
            raise ValueError("Undecided decisions cannot be listed.")
        rows = self._session.execute(select(UserJobDecision, DiscoveredJob).join(DiscoveredJob, DiscoveredJob.id == UserJobDecision.discovered_job_id).where(UserJobDecision.user_id == user_id, UserJobDecision.decision == decision.value).order_by(UserJobDecision.updated_at.desc(), UserJobDecision.id.asc()).limit(limit + 1)).all()
        return UserJobDecisionListResponse(items=[self._list_item(row, job) for row, job in rows[:limit]], limit=limit, truncated=len(rows) > limit)

    def mutate(self, user_id: str, discovered_job_id: str, payload: UserJobDecisionMutation) -> UserJobDecisionRead:
        if self._session.get(DiscoveredJob, discovered_job_id) is None:
            raise LookupError("Discovered job not found.")
        current = self._row(user_id, discovered_job_id)
        if current is None:
            if payload.expected_revision is not None:
                raise UserJobDecisionConflict("Decision changed elsewhere.")
            if payload.decision is UserJobDecisionValue.UNDECIDED:
                return self._read(None, discovered_job_id)
            now = datetime.now(timezone.utc)
            row = UserJobDecision(user_id=user_id, discovered_job_id=discovered_job_id, decision=payload.decision.value, revision=1, created_at=now, updated_at=now)
            try:
                self._session.add(row)
                self._session.commit()
            except IntegrityError as exc:
                self._session.rollback()
                raise UserJobDecisionConflict("Decision changed elsewhere.") from exc
            return self._read(row, discovered_job_id)
        expected = payload.expected_revision
        if expected is None:
            raise UserJobDecisionConflict("Decision changed elsewhere.")
        if current.decision == payload.decision.value:
            unchanged = self._session.scalar(select(UserJobDecision).where(UserJobDecision.user_id == user_id, UserJobDecision.discovered_job_id == discovered_job_id, UserJobDecision.revision == expected, UserJobDecision.decision == payload.decision.value))
            if unchanged is None:
                raise UserJobDecisionConflict("Decision changed elsewhere.")
            return self._read(unchanged, discovered_job_id)
        result = self._session.execute(update(UserJobDecision).where(UserJobDecision.user_id == user_id, UserJobDecision.discovered_job_id == discovered_job_id, UserJobDecision.revision == expected).values(decision=payload.decision.value, revision=expected + 1, updated_at=datetime.now(timezone.utc)))
        if result.rowcount != 1:
            self._session.rollback()
            raise UserJobDecisionConflict("Decision changed elsewhere.")
        self._session.commit()
        row = self._row(user_id, discovered_job_id)
        if row is None:
            raise UserJobDecisionConflict("Decision changed elsewhere.")
        return self._read(row, discovered_job_id)

    def _row(self, user_id: str, discovered_job_id: str) -> UserJobDecision | None:
        return self._session.scalar(select(UserJobDecision).where(UserJobDecision.user_id == user_id, UserJobDecision.discovered_job_id == discovered_job_id))

    @staticmethod
    def _read(row: UserJobDecision | None, discovered_job_id: str) -> UserJobDecisionRead:
        if row is None:
            return UserJobDecisionRead(discovered_job_id=discovered_job_id, decision=UserJobDecisionValue.UNDECIDED)
        return UserJobDecisionRead(discovered_job_id=row.discovered_job_id, decision=UserJobDecisionValue(row.decision), revision=row.revision, created_at=utc_timestamp(row.created_at), updated_at=utc_timestamp(row.updated_at))

    @classmethod
    def _list_item(cls, row: UserJobDecision, job: DiscoveredJob) -> UserJobDecisionListItem:
        return UserJobDecisionListItem(**cls._read(row, job.id).model_dump(), title=job.title, company=job.company, location=job.location, url=job.url, posted_at=utc_timestamp(job.posted_at), work_arrangement=job.work_arrangement, employment_type=job.employment_type, state=DiscoveredJobState(job.state), verification_status=JobVerificationStatus(job.verification_status), verification_reason=job.verification_reason, actionable=is_public_job_actionable(job), first_seen_at=utc_timestamp(job.first_seen_at), last_seen_at=utc_timestamp(job.last_seen_at))
