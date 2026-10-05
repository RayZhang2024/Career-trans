from dataclasses import dataclass

from sqlalchemy import Engine, MetaData, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.database import Base
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityAction,
    CandidateSchemaAppliedRepair,
    CandidateSchemaCompatibilityRead,
    CandidateSchemaRepairAction,
    CandidateSchemaRepairRead,
    CandidateSchemaStatus,
)
from app.services.candidate_compatibility_inspector import (
    CANDIDATE_DOMAIN_TABLES,
    CandidatePhysicalSchemaInspector,
)


ALLOWED_NULLABLE_COLUMNS: dict[str, frozenset[str]] = {
    "candidate_profiles": frozenset({
        "job_search_criteria",
        "display_name",
        "preferred_email",
        "phone",
        "linkedin_url",
        "github_url",
        "portfolio_url",
    }),
    "candidate_cv_ingestion_drafts": frozenset({"runtime_attribution_json"}),
    "candidate_adviser_profile_proposals": frozenset({"overlap_resolution_json", "applied_at"}),
    "candidate_adviser_clarifications": frozenset({"suggested_answers_json", "structured_response_json"}),
}


class CandidateSchemaCompatibilityRepairBlocked(RuntimeError):
    """Raised when physical drift falls outside the reviewed additive policy."""

    def __init__(self, message: str, before: CandidateSchemaCompatibilityRead) -> None:
        super().__init__(message)
        self.before = before


class CandidateSchemaCompatibilityRepairFailed(RuntimeError):
    """Raised after a failed/rolled-back attempt, with observed post-failure state."""

    def __init__(
        self,
        message: str,
        *,
        before: CandidateSchemaCompatibilityRead,
        after: CandidateSchemaCompatibilityRead,
        attempted_repairs: list[CandidateSchemaAppliedRepair],
    ) -> None:
        super().__init__(message)
        self.before = before
        self.after = after
        self.attempted_repairs = attempted_repairs


@dataclass(frozen=True)
class _RepairOperation:
    action: CandidateSchemaRepairAction
    table: str
    column: str | None = None
    index_name: str | None = None

    def as_read(self) -> CandidateSchemaAppliedRepair:
        return CandidateSchemaAppliedRepair(
            action=self.action,
            table=self.table,
            column=self.column,
            index=self.index_name,
        )


class CandidateSQLiteSchemaCompatibilityRepairService:
    """Explicit, SQLite-only repair for the reviewed candidate physical schema."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def repair(self) -> CandidateSchemaRepairRead:
        if self._engine.dialect.name != "sqlite":
            raise ValueError(
                "Candidate schema compatibility repair supports SQLite only; "
                f"received dialect {self._engine.dialect.name!r}."
            )

        before: CandidateSchemaCompatibilityRead | None = None
        operations: list[_RepairOperation] = []
        applied: list[CandidateSchemaAppliedRepair] = []
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                # Pysqlite's legacy transaction mode does not necessarily start
                # a DB transaction for DDL. BEGIN IMMEDIATE makes the DDL batch
                # part of an actual SQLite transaction and excludes competing
                # schema writers between preflight and postflight.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                before = CandidatePhysicalSchemaInspector(connection).inspect()
                if not inspect(connection).has_table("users"):
                    raise CandidateSchemaCompatibilityRepairBlocked(
                        "The physical users table prerequisite is absent; candidate schema repair is blocked.",
                        before,
                    )
                operations = self._build_plan(before)

                for operation in operations:
                    self._apply_operation(connection, operation)
                    applied.append(operation.as_read())

                after = CandidatePhysicalSchemaInspector(connection).inspect()
                if after.status is not CandidateSchemaStatus.COMPATIBLE:
                    raise RuntimeError(
                        "Candidate schema postflight did not reach compatible status."
                    )
                transaction.commit()
                return CandidateSchemaRepairRead(
                    changed=bool(applied),
                    before=before,
                    after=after,
                    applied_repairs=applied,
                )
            except CandidateSchemaCompatibilityRepairBlocked:
                transaction.rollback()
                raise
            except Exception as exc:
                transaction.rollback()
                before_report = before or CandidatePhysicalSchemaInspector(self._engine).inspect()
                after_report = CandidatePhysicalSchemaInspector(self._engine).inspect()
                raise CandidateSchemaCompatibilityRepairFailed(
                    "Candidate SQLite schema repair failed; inspect the reported state "
                    "and rerun the idempotent repair after addressing the failure.",
                    before=before_report,
                    after=after_report,
                    attempted_repairs=applied,
                ) from exc

    def _build_plan(
        self,
        before: CandidateSchemaCompatibilityRead,
    ) -> list[_RepairOperation]:
        if before.status is CandidateSchemaStatus.UNSUPPORTED_DRIFT:
            raise CandidateSchemaCompatibilityRepairBlocked(
                "Unsupported candidate-domain schema drift blocks all repair operations.",
                before,
            )

        table_reads = {table.table: table for table in before.tables}
        if set(table_reads) != set(CANDIDATE_DOMAIN_TABLES):
            raise CandidateSchemaCompatibilityRepairBlocked(
                "Schema inspection did not return the complete candidate-domain inventory.",
                before,
            )

        missing_table_names = {
            table_name for table_name, table_read in table_reads.items()
            if not table_read.table_exists
        }
        operations: list[_RepairOperation] = []
        ordered_missing_tables = [
            table.name for table in Base.metadata.sorted_tables
            if table.name in missing_table_names and table.name in CANDIDATE_DOMAIN_TABLES
        ]
        if set(ordered_missing_tables) != missing_table_names:
            raise CandidateSchemaCompatibilityRepairBlocked(
                "A missing table is outside the bounded candidate-domain allowlist.",
                before,
            )
        operations.extend(
            _RepairOperation(CandidateSchemaRepairAction.CREATE_TABLE, table_name)
            for table_name in ordered_missing_tables
        )

        proposal_read = table_reads.get("candidate_adviser_profile_proposals")
        if (
            proposal_read is not None
            and CandidateCompatibilityAction.RECONSTRUCT_HISTORICAL_ADVISER_PROPOSAL_TABLE
            in proposal_read.planned_actions
        ):
            operations.append(_RepairOperation(
                CandidateSchemaRepairAction.RECONSTRUCT_HISTORICAL_ADVISER_PROPOSAL_TABLE,
                "candidate_adviser_profile_proposals",
            ))

        column_operations: list[_RepairOperation] = []
        index_operations: list[_RepairOperation] = []
        for table_name in CANDIDATE_DOMAIN_TABLES:
            table_read = table_reads[table_name]
            if not table_read.table_exists:
                continue
            if (
                table_name == "candidate_adviser_profile_proposals"
                and CandidateCompatibilityAction.RECONSTRUCT_HISTORICAL_ADVISER_PROPOSAL_TABLE
                in table_read.planned_actions
            ):
                continue
            metadata_table = Base.metadata.tables[table_name]
            allowed_columns = ALLOWED_NULLABLE_COLUMNS.get(table_name, frozenset())
            for missing in sorted(table_read.missing_columns, key=lambda item: item.name):
                column = metadata_table.columns.get(missing.name)
                if (
                    missing.name not in allowed_columns
                    or column is None
                    or not column.nullable
                    or column.primary_key
                    or column.server_default is not None
                    or column.default is not None
                    or column.unique
                    or column.foreign_keys
                ):
                    raise CandidateSchemaCompatibilityRepairBlocked(
                        f"Missing column {table_name}.{missing.name} is outside the reviewed nullable-column policy.",
                        before,
                    )
                column_operations.append(_RepairOperation(
                    CandidateSchemaRepairAction.ADD_COLUMN,
                    table_name,
                    column=missing.name,
                ))

            for index_name in sorted(table_read.missing_indexes):
                index = next(
                    (candidate for candidate in metadata_table.indexes if candidate.name == index_name),
                    None,
                )
                if index is None or index.unique or index.table.name != table_name:
                    raise CandidateSchemaCompatibilityRepairBlocked(
                        f"Missing index {index_name!r} is not a reviewed ordinary candidate index.",
                        before,
                    )
                index_operations.append(_RepairOperation(
                    CandidateSchemaRepairAction.CREATE_INDEX,
                    table_name,
                    index_name=index_name,
                ))

        operations.extend(column_operations)
        operations.extend(index_operations)
        return operations

    def _apply_operation(self, connection: Connection, operation: _RepairOperation) -> None:
        table = Base.metadata.tables[operation.table]
        if operation.action is CandidateSchemaRepairAction.RECONSTRUCT_HISTORICAL_ADVISER_PROPOSAL_TABLE:
            self._reconstruct_historical_proposal_table(connection, table)
            return
        if operation.action is CandidateSchemaRepairAction.CREATE_TABLE:
            Base.metadata.create_all(connection, tables=[table], checkfirst=True)
            return
        if operation.action is CandidateSchemaRepairAction.ADD_COLUMN:
            column = table.columns[operation.column]
            preparer = connection.dialect.identifier_preparer
            table_name = preparer.quote(table.name)
            column_name = preparer.quote(column.name)
            compiled_type = column.type.compile(dialect=connection.dialect)
            connection.exec_driver_sql(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {compiled_type}"
            )
            return
        if operation.action is CandidateSchemaRepairAction.CREATE_INDEX:
            index = next(
                candidate for candidate in table.indexes
                if candidate.name == operation.index_name
            )
            connection.execute(CreateIndex(index))
            return
        raise ValueError(f"Unsupported planned schema action: {operation.action!r}")

    @staticmethod
    def _reconstruct_historical_proposal_table(connection: Connection, table) -> None:
        table_name = table.name
        temp_name = f"_{table_name}_transfer_repair"
        preparer = connection.dialect.identifier_preparer
        quoted_table = preparer.quote(table_name)
        quoted_temp = preparer.quote(temp_name)
        actual_columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
        new_column_names = [column.name for column in table.columns if column.name not in actual_columns]
        expected_added = {
            "overlap_resolution_json",
            "transferred_profile_revision_id",
            "transferred_at",
            "applied_at",
        }
        if not set(new_column_names).issubset(expected_added):
            raise RuntimeError("Historical proposal reconstruction encountered unrecognized columns.")

        old_columns = [column.name for column in table.columns if column.name in actual_columns]
        old_projection = ", ".join(preparer.quote(name) for name in old_columns)
        before_rows = connection.exec_driver_sql(
            f"SELECT {old_projection} FROM {quoted_table} ORDER BY {preparer.quote('id')}"
        ).all()
        before_count = len(before_rows)

        temporary_metadata = MetaData()
        Base.metadata.tables["users"].to_metadata(temporary_metadata)
        Base.metadata.tables["candidate_profile_revisions"].to_metadata(temporary_metadata)
        temporary = table.to_metadata(temporary_metadata, name=temp_name)
        connection.execute(CreateTable(temporary))
        target_names = [column.name for column in table.columns]
        selected = [
            preparer.quote(name) if name in actual_columns else "NULL"
            for name in target_names
        ]
        target_projection = ", ".join(preparer.quote(name) for name in target_names)
        connection.exec_driver_sql(
            f"INSERT INTO {quoted_temp} ({target_projection}) "
            f"SELECT {', '.join(selected)} FROM {quoted_table}"
        )

        copied_rows = connection.exec_driver_sql(
            f"SELECT {target_projection} FROM {quoted_temp} ORDER BY {preparer.quote('id')}"
        ).all()
        expected_rows = [
            tuple(row[old_columns.index(name)] if name in actual_columns else None for name in target_names)
            for row in before_rows
        ]
        if len(copied_rows) != before_count or copied_rows != expected_rows:
            raise RuntimeError("Historical proposal reconstruction failed row-count or preservation validation.")

        connection.exec_driver_sql(f"DROP TABLE {quoted_table}")
        connection.exec_driver_sql(
            f"ALTER TABLE {quoted_temp} RENAME TO {quoted_table}"
        )
        for index in table.indexes:
            connection.execute(CreateIndex(index))

        # Keep the copied row count check adjacent to the swap as a second guard.
        after_count = connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM {quoted_table}"
        ).scalar_one()
        if after_count != before_count:
            raise RuntimeError("Historical proposal row count changed during table swap.")
