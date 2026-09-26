from sqlalchemy import Engine, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.database import Base
from app.schemas.candidate_compatibility import (
    CandidateCompatibilityAction,
    CandidateColumnCompatibility,
    CandidateSchemaCompatibilityRead,
    CandidateSchemaStatus,
    CandidateTableCompatibility,
)


# Only candidate-profile-domain tables are inventoried here. Job/discovery schema
# evolution is intentionally outside this bounded compatibility report.
CANDIDATE_DOMAIN_TABLES = (
    "candidate_profiles",
    "candidate_cv_ingestion_drafts",
    "candidate_structured_profiles",
    "candidate_evidence",
    "candidate_cv_review_baselines",
    "candidate_adviser_intakes",
    "candidate_adviser_assessments",
    "candidate_adviser_clarifications",
    "candidate_profile_revisions",
    "candidate_adviser_profile_proposals",
    "candidate_structured_item_lineage",
    "candidate_cv_overlap_reviews",
)


def _inspection_bind(bind: Engine | Connection | Session) -> Engine | Connection:
    if isinstance(bind, Session):
        return bind.connection()
    return bind


def _type_affinity(column_type) -> type:
    return getattr(column_type, "_type_affinity", type(column_type))


def _type_signature(column_type, dialect) -> str:
    signature = "".join(
        character.lower() for character in column_type.compile(dialect=dialect)
        if character.isalnum()
    )
    # SQLite's DATE/TIMESTAMP declarations are storage-compatible aliases for
    # SQLAlchemy DateTime columns and occur in historical hand-written DDL.
    return "datetime" if signature in {"datetime", "timestamp"} else signature


def _normal_sql(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _constraint_signature(constraint) -> tuple:
    if constraint.__class__.__name__ == "UniqueConstraint":
        return ("unique", tuple(column.name for column in constraint.columns))
    if constraint.__class__.__name__ == "ForeignKeyConstraint":
        pairs = tuple(
            (
                element.parent.name,
                element.column.table.name,
                element.column.name,
                str(element.ondelete).lower() if element.ondelete else None,
            )
            for element in constraint.elements
        )
        return ("foreign_key", pairs)
    if constraint.__class__.__name__ == "CheckConstraint":
        return ("check", constraint.name, _normal_sql(str(constraint.sqltext)))
    return ()


class CandidatePhysicalSchemaInspector:
    """Read-only SQLite/database catalog inspection; never queries ORM rows."""

    def __init__(self, bind: Engine | Connection | Session) -> None:
        self._bind = _inspection_bind(bind)

    def inspect(self) -> CandidateSchemaCompatibilityRead:
        inspector = inspect(self._bind)
        tables = [self._inspect_table(inspector, name, self._bind.dialect) for name in CANDIDATE_DOMAIN_TABLES]
        if any(table.status is CandidateSchemaStatus.UNSUPPORTED_DRIFT for table in tables):
            status = CandidateSchemaStatus.UNSUPPORTED_DRIFT
        elif any(table.status is CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE for table in tables):
            status = CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
        else:
            status = CandidateSchemaStatus.COMPATIBLE
        actions = list(dict.fromkeys(action for table in tables for action in table.planned_actions))
        return CandidateSchemaCompatibilityRead(
            dialect=self._bind.dialect.name,
            tables=tables,
            status=status,
            planned_actions=actions,
        )

    @staticmethod
    def _inspect_table(inspector, table_name: str, dialect) -> CandidateTableCompatibility:
        table = Base.metadata.tables[table_name]
        if not inspector.has_table(table_name):
            return CandidateTableCompatibility(
                table=table_name,
                table_exists=False,
                status=CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE,
                planned_actions=[CandidateCompatibilityAction.CREATE_MISSING_SCHEMA_TABLE],
                diagnostics=["The current ORM table is absent; create_all can create a new table without rewriting candidate data."],
            )

        actual_columns = {value["name"]: value for value in inspector.get_columns(table_name)}
        expected_columns = {column.name: column for column in table.columns}
        missing: list[CandidateColumnCompatibility] = []
        diagnostics: list[str] = []
        unsupported = False
        repairable = False

        primary_key = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        expected_primary_key = {column.name for column in table.primary_key.columns}
        if primary_key != expected_primary_key:
            unsupported = True
            diagnostics.append("Primary-key shape differs from the current candidate-domain table definition.")

        for name, expected in expected_columns.items():
            actual = actual_columns.get(name)
            if actual is None:
                missing.append(CandidateColumnCompatibility(
                    name=name,
                    present=False,
                    expected_type=str(expected.type),
                    expected_nullable=bool(expected.nullable),
                ))
                if name in expected_primary_key or name == "user_id":
                    unsupported = True
                    diagnostics.append(f"Required ownership/identity column {name} is missing.")
                elif expected.nullable or expected.server_default is not None:
                    repairable = True
                else:
                    unsupported = True
                    diagnostics.append(f"Required non-null column {name} has no database default for safe additive repair.")
                continue

            actual_type = actual["type"]
            actual_nullable = bool(actual.get("nullable", True))
            # SQLite reports nullable=True for ordinary primary-key columns in
            # PRAGMA table_info even though the PK itself enforces identity.
            # Compare nullability only where the column is not part of the PK.
            if (
                _type_affinity(expected.type) is not _type_affinity(actual_type)
                or _type_signature(expected.type, dialect) != _type_signature(actual_type, dialect)
            ):
                unsupported = True
                diagnostics.append(f"Column {name} has an incompatible physical type.")
            if name not in expected_primary_key and bool(expected.nullable) != actual_nullable:
                unsupported = True
                diagnostics.append(f"Column {name} nullability differs from the current definition.")
            if name == "user_id":
                foreign_keys = inspector.get_foreign_keys(table_name)
                ownership_fk = any(
                    value.get("constrained_columns") == ["user_id"]
                    and value.get("referred_table") == "users"
                    and value.get("referred_columns") == ["id"]
                    for value in foreign_keys
                )
                expected_fk = any(
                    element.parent.name == "user_id"
                    and element.column.table.name == "users"
                    and element.column.name == "id"
                    for constraint in table.foreign_key_constraints
                    for element in constraint.elements
                )
                if expected_fk and not ownership_fk:
                    unsupported = True
                    diagnostics.append("Ownership column user_id does not reference users.id as expected.")

        expected_indexes = {index.name: index for index in table.indexes if index.name}
        actual_indexes = {value["name"]: value for value in inspector.get_indexes(table_name)}
        missing_indexes = sorted(set(expected_indexes) - set(actual_indexes))
        malformed_indexes = [
            name for name, expected in expected_indexes.items()
            if name in actual_indexes
            and (
                list(actual_indexes[name].get("column_names") or [])
                != [column.name for column in expected.columns]
                or bool(actual_indexes[name].get("unique")) != bool(expected.unique)
            )
        ]
        if malformed_indexes:
            unsupported = True
            diagnostics.append("One or more existing indexes differ from the current candidate-domain definitions.")
        unsafe_missing_indexes = [
            name for name in missing_indexes if expected_indexes[name].unique
        ]
        if unsafe_missing_indexes:
            unsupported = True
            diagnostics.append("A required unique index is missing; data must be checked before it can be restored.")
        elif missing_indexes:
            repairable = True

        expected_constraints = {
            signature for constraint in table.constraints
            if (signature := _constraint_signature(constraint))
        }
        actual_constraints: set[tuple] = set()
        actual_constraints.update(
            ("unique", tuple(value.get("column_names") or []))
            for value in inspector.get_unique_constraints(table_name)
        )
        actual_constraints.update(
            (
                "foreign_key",
                tuple(zip(
                    value.get("constrained_columns") or [],
                    [value.get("referred_table")] * len(value.get("constrained_columns") or []),
                    value.get("referred_columns") or [],
                    [str(value.get("options", {}).get("ondelete")).lower() if value.get("options", {}).get("ondelete") else None] * len(value.get("constrained_columns") or []),
                )),
            )
            for value in inspector.get_foreign_keys(table_name)
        )
        actual_constraints.update(
            ("check", value.get("name"), _normal_sql(value.get("sqltext") or ""))
            for value in inspector.get_check_constraints(table_name)
        )
        missing_constraints = sorted(
            (repr(value) for value in expected_constraints - actual_constraints)
        )
        if missing_constraints:
            unsupported = True
            diagnostics.append("One or more required uniqueness, ownership, or state constraints are absent.")

        if unsupported:
            status = CandidateSchemaStatus.UNSUPPORTED_DRIFT
            actions = [CandidateCompatibilityAction.MANUAL_RESOLUTION_REQUIRED]
        elif repairable:
            status = CandidateSchemaStatus.ADDITIVE_REPAIR_AVAILABLE
            actions = []
            if missing:
                actions.append(CandidateCompatibilityAction.ADD_MISSING_SCHEMA_COLUMN)
            if missing_indexes:
                actions.append(CandidateCompatibilityAction.ADD_MISSING_SCHEMA_INDEX)
            if not actions:
                actions.append(CandidateCompatibilityAction.NO_ACTION)
        else:
            status = CandidateSchemaStatus.COMPATIBLE
            actions = [CandidateCompatibilityAction.NO_ACTION]

        return CandidateTableCompatibility(
            table=table_name,
            table_exists=True,
            missing_columns=missing,
            missing_indexes=missing_indexes,
            missing_constraints=missing_constraints,
            status=status,
            planned_actions=actions,
            diagnostics=diagnostics,
        )


def inspect_candidate_schema(bind: Engine | Connection | Session) -> CandidateSchemaCompatibilityRead:
    """Convenience function for a read-only candidate physical-schema report."""
    return CandidatePhysicalSchemaInspector(bind).inspect()
