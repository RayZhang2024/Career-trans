"""Operational orchestration for the reviewed candidate compatibility phases."""

from collections.abc import Callable

from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.models.user import User
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityDatabaseState,
    CandidateCompatibilityOperation,
    CandidateCompatibilityRuntimeRead,
    CandidateCompatibilityStatus,
    CandidateSchemaCompatibilityRead,
    CandidateSchemaStatus,
)
from app.services.candidate_compatibility_inspector import CandidatePhysicalSchemaInspector
from app.services.candidate_legacy_compatibility_inspector import CandidateCompatibilityDryRunService
from app.services.candidate_legacy_data_reconciliation import CandidateLegacyDataReconciliationService
from app.services.candidate_sqlite_schema_repair import (
    CandidateSQLiteSchemaCompatibilityRepairService,
    CandidateSchemaCompatibilityRepairBlocked,
    CandidateSchemaCompatibilityRepairFailed,
)


class CandidateCompatibilityRuntimeBlocked(RuntimeError):
    """Raised when runtime orchestration cannot safely proceed."""

    def __init__(self, message: str, *, schema: CandidateSchemaCompatibilityRead | None = None) -> None:
        super().__init__(message)
        self.schema = schema


class CandidateCompatibilityRuntime:
    """Inspect or apply SQLite candidate compatibility without provider calls."""

    def __init__(
        self,
        engine: Engine,
        *,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory or sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False
        )

    def inspect(self) -> CandidateCompatibilityRuntimeRead:
        self._require_sqlite()
        table_names = set(inspect(self.engine).get_table_names())
        if not table_names:
            return CandidateCompatibilityRuntimeRead(
                operation=CandidateCompatibilityOperation.INSPECT,
                database_state=CandidateCompatibilityDatabaseState.FRESH_UNINITIALIZED,
            )

        if "users" not in table_names:
            schema = CandidatePhysicalSchemaInspector(self.engine).inspect()
            return CandidateCompatibilityRuntimeRead(
                operation=CandidateCompatibilityOperation.INSPECT,
                database_state=CandidateCompatibilityDatabaseState.RETAINED_WITHOUT_USERS,
                schema_before=schema,
            )

        schema = CandidatePhysicalSchemaInspector(self.engine).inspect()
        if schema.status is not CandidateSchemaStatus.COMPATIBLE:
            return CandidateCompatibilityRuntimeRead(
                operation=CandidateCompatibilityOperation.INSPECT,
                database_state=CandidateCompatibilityDatabaseState.RETAINED_WITH_USERS,
                schema_before=schema,
            )

        user_ids = self._user_ids()
        with self.session_factory() as session:
            plan = CandidateCompatibilityDryRunService(session).inspect(user_ids)
        return CandidateCompatibilityRuntimeRead(
            operation=CandidateCompatibilityOperation.INSPECT,
            database_state=CandidateCompatibilityDatabaseState.RETAINED_WITH_USERS,
            schema_before=schema,
            user_count=plan.summary.user_count,
            unresolved_count=plan.summary.unresolved,
            not_yet_confirmed_count=plan.summary.not_yet_confirmed,
            dry_run=plan,
        )

    def apply(self) -> CandidateCompatibilityRuntimeRead:
        self._require_sqlite()

        table_names = set(inspect(self.engine).get_table_names())
        fresh = not table_names
        if fresh:
            # A truly empty local database is the only case where broad bootstrap
            # may precede the reviewed candidate-domain repair.
            Base.metadata.create_all(bind=self.engine)
            state = CandidateCompatibilityDatabaseState.FRESH_UNINITIALIZED
            schema_before = None
        else:
            state = CandidateCompatibilityDatabaseState.RETAINED_WITH_USERS
            if "users" not in table_names:
                schema = CandidatePhysicalSchemaInspector(self.engine).inspect()
                raise CandidateCompatibilityRuntimeBlocked(
                    "Retained database has no users table; refusing broad table creation or reconciliation.",
                    schema=schema,
                )
            schema_before = CandidatePhysicalSchemaInspector(self.engine).inspect()

        try:
            repair = CandidateSQLiteSchemaCompatibilityRepairService(self.engine).repair()
        except CandidateSchemaCompatibilityRepairBlocked as exc:
            raise CandidateCompatibilityRuntimeBlocked(str(exc), schema=exc.before) from exc
        except CandidateSchemaCompatibilityRepairFailed as exc:
            raise CandidateCompatibilityRuntimeBlocked(str(exc), schema=exc.after) from exc

        # Candidate safety has been established before unrelated ORM tables are
        # created. This order supports downstream reconciliation dependencies.
        Base.metadata.create_all(bind=self.engine)
        schema_after = CandidatePhysicalSchemaInspector(self.engine).inspect()
        if schema_after.status is not CandidateSchemaStatus.COMPATIBLE:
            raise CandidateCompatibilityRuntimeBlocked(
                "Candidate schema postflight is not compatible; user reconciliation was not started.",
                schema=schema_after,
            )

        user_ids = self._user_ids()
        batch = CandidateLegacyDataReconciliationService(self.session_factory).reconcile_users(user_ids)
        return CandidateCompatibilityRuntimeRead(
            operation=CandidateCompatibilityOperation.APPLY,
            database_state=state,
            schema_before=schema_before or repair.before,
            schema_after=schema_after,
            repair_changed=repair.changed,
            repair_count=len(repair.applied_repairs),
            user_count=batch.user_count,
            changed_count=batch.changed_count,
            unresolved_count=batch.unresolved_count,
            not_yet_confirmed_count=sum(
                result.status_after is CandidateCompatibilityStatus.NOT_YET_CONFIRMED
                for result in batch.users
            ),
            reconciliation=batch,
        )

    def _require_sqlite(self) -> None:
        if self.engine.dialect.name != "sqlite":
            raise CandidateCompatibilityRuntimeBlocked(
                "Candidate compatibility inspect/apply supports SQLite databases only."
            )

    def _user_ids(self) -> list[str]:
        # Querying user IDs is postponed until a compatible candidate schema has
        # been verified (and, for apply, after the remaining ORM tables exist).
        with self.engine.connect() as connection:
            return list(connection.scalars(select(User.id).order_by(User.id)).all())


def run_candidate_compatibility_startup(engine: Engine) -> CandidateCompatibilityRuntimeRead | None:
    """Apply the SQLite workflow at startup; leave other dialects unchanged."""
    if engine.dialect.name != "sqlite":
        Base.metadata.create_all(bind=engine)
        return None
    return CandidateCompatibilityRuntime(engine).apply()
