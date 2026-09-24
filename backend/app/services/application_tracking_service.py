from datetime import datetime, timezone
import json

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.application_preparation import ApplicationPreparation
from app.models.application_tracking import ApplicationTrackingEvent, ApplicationTrackingRecord
from app.schemas.application_tracking import (
    ApplicationTrackingCreate,
    ApplicationTrackingEventRead,
    ApplicationTrackingListItem,
    ApplicationTrackingRead,
    ApplicationTrackingStatus,
    ApplicationTrackingStatusEventCreate,
    ApplicationTrackingTarget,
)


class ApplicationTrackingConflictError(ValueError):
    """A user-visible tracking conflict with no database details."""


class ApplicationTrackingService:
    """Provider-free user tracking over immutable preparation target snapshots."""

    def __init__(self, session: Session):
        self.session = session

    def create(self, user_id: str, payload: ApplicationTrackingCreate) -> ApplicationTrackingRead:
        preparation = self._owned_preparation(user_id, payload.preparation_id)
        if preparation is None:
            raise LookupError("Application preparation not found.")

        now = datetime.now(timezone.utc)
        record = ApplicationTrackingRecord(
            preparation_id=preparation.id,
            current_status=payload.status.value,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            self.session.add(record)
            self.session.flush()
            self.session.add(
                ApplicationTrackingEvent(
                    tracking_id=record.id,
                    revision=1,
                    from_status=None,
                    to_status=payload.status.value,
                    recorded_at=now,
                )
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ApplicationTrackingConflictError("This preparation is already being tracked.") from exc
        except Exception:
            self.session.rollback()
            raise
        return self.get_detail(user_id, record.id)

    def list_for_user(self, user_id: str) -> list[ApplicationTrackingListItem]:
        rows = self.session.execute(
            select(ApplicationTrackingRecord, ApplicationPreparation)
            .join(ApplicationPreparation, ApplicationPreparation.id == ApplicationTrackingRecord.preparation_id)
            .where(ApplicationPreparation.user_id == user_id)
            .order_by(
                ApplicationTrackingRecord.updated_at.desc(),
                ApplicationTrackingRecord.created_at.desc(),
                ApplicationTrackingRecord.id.desc(),
            )
        ).all()
        return [self._list_item(record, preparation) for record, preparation in rows]

    def get_by_preparation(self, user_id: str, preparation_id: str) -> ApplicationTrackingRead:
        row = self.session.execute(
            select(ApplicationTrackingRecord.id)
            .join(ApplicationPreparation, ApplicationPreparation.id == ApplicationTrackingRecord.preparation_id)
            .where(ApplicationPreparation.user_id == user_id, ApplicationPreparation.id == preparation_id)
        ).scalar_one_or_none()
        if row is None:
            raise LookupError("Application tracking not found.")
        return self.get_detail(user_id, row)

    def get_detail(self, user_id: str, tracking_id: str) -> ApplicationTrackingRead:
        row = self._owned_record(user_id, tracking_id)
        if row is None:
            raise LookupError("Application tracking not found.")
        record, preparation = row
        events = self.session.scalars(
            select(ApplicationTrackingEvent)
            .where(ApplicationTrackingEvent.tracking_id == record.id)
            .order_by(ApplicationTrackingEvent.revision.asc())
        ).all()
        return ApplicationTrackingRead(
            id=record.id,
            preparation_id=record.preparation_id,
            target=self._target(preparation),
            current_status=ApplicationTrackingStatus(record.current_status),
            revision=record.revision,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
            events=[
                ApplicationTrackingEventRead(
                    revision=event.revision,
                    from_status=ApplicationTrackingStatus(event.from_status) if event.from_status else None,
                    to_status=ApplicationTrackingStatus(event.to_status),
                    recorded_at=_utc(event.recorded_at),
                )
                for event in events
            ],
        )

    def append_status_event(
        self, user_id: str, tracking_id: str, payload: ApplicationTrackingStatusEventCreate
    ) -> ApplicationTrackingRead:
        owned = self._owned_record(user_id, tracking_id)
        if owned is None:
            raise LookupError("Application tracking not found.")
        record, _preparation = owned
        if record.current_status == payload.status.value:
            raise ApplicationTrackingConflictError("The requested status is already current.")

        expected_revision = payload.expected_revision
        next_revision = expected_revision + 1
        previous_status = record.current_status if record.revision == expected_revision else None
        now = datetime.now(timezone.utc)
        try:
            result = self.session.execute(
                update(ApplicationTrackingRecord)
                .where(ApplicationTrackingRecord.id == tracking_id, ApplicationTrackingRecord.revision == expected_revision)
                .values(current_status=payload.status.value, revision=next_revision, updated_at=now)
                .execution_options(synchronize_session="fetch")
            )
            if result.rowcount == 1 and previous_status is not None:
                self.session.add(
                    ApplicationTrackingEvent(
                        tracking_id=tracking_id,
                        revision=next_revision,
                        from_status=previous_status,
                        to_status=payload.status.value,
                        recorded_at=now,
                    )
                )
                self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ApplicationTrackingConflictError("The tracking record changed before this update.") from exc
        except Exception:
            self.session.rollback()
            raise
        if result.rowcount != 1 or previous_status is None:
            self.session.rollback()
            if self._owned_record(user_id, tracking_id) is None:
                raise LookupError("Application tracking not found.")
            raise ApplicationTrackingConflictError("The tracking record changed before this update.")
        return self.get_detail(user_id, tracking_id)

    def _owned_preparation(self, user_id: str, preparation_id: str) -> ApplicationPreparation | None:
        return self.session.scalar(
            select(ApplicationPreparation).where(
                ApplicationPreparation.id == preparation_id,
                ApplicationPreparation.user_id == user_id,
            )
        )

    def _owned_record(self, user_id: str, tracking_id: str) -> tuple[ApplicationTrackingRecord, ApplicationPreparation] | None:
        row = self.session.execute(
            select(ApplicationTrackingRecord, ApplicationPreparation)
            .join(ApplicationPreparation, ApplicationPreparation.id == ApplicationTrackingRecord.preparation_id)
            .where(ApplicationTrackingRecord.id == tracking_id, ApplicationPreparation.user_id == user_id)
        ).one_or_none()
        return row

    @staticmethod
    def _target(preparation: ApplicationPreparation) -> ApplicationTrackingTarget:
        # Tracking intentionally depends only on the immutable target snapshot,
        # not on generated result/evidence-envelope compatibility.
        target = json.loads(preparation.target_snapshot_json)
        return ApplicationTrackingTarget(
            preparation_id=preparation.id,
            preparation_created_at=_utc(preparation.created_at),
            source_kind=target["source_kind"],
            title=target["title"],
            company=target.get("company"),
            location=target.get("location"),
            public_url=target.get("public_url"),
        )

    @classmethod
    def _list_item(cls, record: ApplicationTrackingRecord, preparation: ApplicationPreparation) -> ApplicationTrackingListItem:
        return ApplicationTrackingListItem(
            id=record.id,
            preparation_id=record.preparation_id,
            target=cls._target(preparation),
            current_status=ApplicationTrackingStatus(record.current_status),
            revision=record.revision,
            created_at=_utc(record.created_at),
            updated_at=_utc(record.updated_at),
        )


def _utc(value: datetime) -> datetime:
    """SQLite returns timezone=True timestamps as naive; restore UTC authority."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
