import runpy
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import User  # noqa: F401 - registers all application tables with Base.metadata
from app.models.candidate_adviser import (
    CandidateAdviserAssessmentRecord,
    CandidateAdviserClarificationRecord,
)
from app.schemas.candidate_compatibility import CandidateSchemaStatus
from app.services.candidate_compatibility_inspector import CandidatePhysicalSchemaInspector
from app.services.candidate_compatibility_runtime import CandidateCompatibilityRuntime


MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "20261006_candidate_adviser_bounded_refinement_sqlite.py"
ASSESSMENT_TABLE = "candidate_adviser_assessments"
CLARIFICATION_TABLE = "candidate_adviser_clarifications"


def _engine(path: Path):
    return create_engine(f"sqlite:///{path}")


def _seed_legacy_rows(engine, *, broken_migration_already_applied: bool) -> None:
    with Session(engine) as session, session.begin():
        session.add(User(id="synthetic-user", email="migration@example.test", password_hash="synthetic"))
        session.add(CandidateAdviserAssessmentRecord(
            id="assessment",
            user_id="synthetic-user",
            input_fingerprint="f" * 64,
            contract_version="clarification_areas_v1" if broken_migration_already_applied else "legacy_questions",
            status="confirmed",
            assessment_json='{"open_questions":[{"text":"Keep this exact JSON"}]}',
            created_at=datetime(2026, 10, 6, 8, 30),
            updated_at=datetime(2026, 10, 6, 8, 31),
        ))
        session.add(CandidateAdviserClarificationRecord(
            id="clarification",
            user_id="synthetic-user",
            clarification_id="clarification-id",
            question_key="question-key",
            origin_assessment_fingerprint="f" * 64,
            parent_area_id="persisted-area-link" if broken_migration_already_applied else None,
            round_number=1 if broken_migration_already_applied else None,
            question_text="Preserve this confirmed answer?",
            question_source_references_json='[{"source_type":"intake","reference":"career_direction"}]',
            suggested_answers_json='[{"option_id":"persisted-option","text":"Persisted option"}]',
            structured_response_json='{"selected_option_ids":["persisted-option"],"custom_answer_text":"","special_selection":null}',
            priority_index=7,
            answer_text="Synthetic confirmed response",
            interpretation_json='{"confirmed_context_summary":"Preserve this interpretation"}',
            status="confirmed",
            created_at=datetime(2026, 10, 6, 8, 32),
            updated_at=datetime(2026, 10, 6, 8, 33),
            confirmed_at=datetime(2026, 10, 6, 8, 34),
        ))


def _make_pre_280_database(engine, *, broken_migration_already_applied: bool) -> None:
    Base.metadata.create_all(engine)
    _seed_legacy_rows(engine, broken_migration_already_applied=broken_migration_already_applied)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE candidate_adviser_clarification_areas")
        connection.exec_driver_sql("DROP TABLE candidate_adviser_refinement_journeys")
        connection.exec_driver_sql("DROP INDEX ix_candidate_adviser_clarifications_parent_area_id")
        connection.exec_driver_sql(f'ALTER TABLE "{CLARIFICATION_TABLE}" DROP COLUMN parent_area_id')
        connection.exec_driver_sql(f'ALTER TABLE "{CLARIFICATION_TABLE}" DROP COLUMN round_number')
        connection.exec_driver_sql(f'ALTER TABLE "{ASSESSMENT_TABLE}" DROP COLUMN contract_version')

    if broken_migration_already_applied:
        # Reproduce the physical declarations made by the original #280 SQLite
        # migration, including the refinement tables and relationship index.
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"ALTER TABLE \"{ASSESSMENT_TABLE}\" "
                "ADD COLUMN contract_version TEXT NOT NULL DEFAULT 'legacy_questions'"
            )
            connection.exec_driver_sql(
                f'ALTER TABLE "{CLARIFICATION_TABLE}" ADD COLUMN parent_area_id TEXT NULL'
            )
            connection.exec_driver_sql(
                f'ALTER TABLE "{CLARIFICATION_TABLE}" ADD COLUMN round_number INTEGER NULL'
            )
            Base.metadata.tables["candidate_adviser_refinement_journeys"].create(connection)
            Base.metadata.tables["candidate_adviser_clarification_areas"].create(connection)
            connection.exec_driver_sql(
                "CREATE INDEX ix_candidate_adviser_clarifications_parent_area_id "
                f"ON {CLARIFICATION_TABLE}(parent_area_id)"
            )
            connection.exec_driver_sql(
                f"UPDATE {ASSESSMENT_TABLE} SET contract_version='clarification_areas_v1' WHERE id='assessment'"
            )
            connection.exec_driver_sql(
                f"UPDATE {CLARIFICATION_TABLE} SET parent_area_id='persisted-area-link' WHERE id='clarification'"
            )


def _migration():
    return runpy.run_path(str(MIGRATION_PATH))["upgrade"]


def _rows(connection: sqlite3.Connection, table: str) -> tuple[list[str], tuple]:
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    return columns, connection.execute(
        f"SELECT {quoted_columns} "
        f'FROM "{table}" ORDER BY id'
    ).fetchall()


def _sqlite_snapshot(path: Path, table: str) -> tuple[list[str], tuple]:
    with sqlite3.connect(path) as connection:
        return _rows(connection, table)


def _assert_orm_compatible(engine) -> None:
    schema = CandidatePhysicalSchemaInspector(engine).inspect()
    assert schema.status is CandidateSchemaStatus.COMPATIBLE
    tables = {table.table: table for table in schema.tables}
    assert tables[ASSESSMENT_TABLE].status is CandidateSchemaStatus.COMPATIBLE
    assert tables[CLARIFICATION_TABLE].status is CandidateSchemaStatus.COMPATIBLE


@pytest.mark.parametrize("broken_migration_already_applied", [False, True], ids=["pre-280", "broken-280"])
def test_sqlite_refinement_migration_is_row_preserving_idempotent_and_startup_compatible(
    tmp_path: Path,
    broken_migration_already_applied: bool,
) -> None:
    database = tmp_path / "adviser-migration.db"
    engine = _engine(database)
    _make_pre_280_database(engine, broken_migration_already_applied=broken_migration_already_applied)

    if broken_migration_already_applied:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX ix_synthetic_assessment_json "
                f"ON {ASSESSMENT_TABLE}(assessment_json)"
            )
            connection.exec_driver_sql(
                f"CREATE TRIGGER trg_synthetic_assessment_update AFTER UPDATE ON {ASSESSMENT_TABLE} "
                "BEGIN SELECT NEW.id; END"
            )
        with engine.connect() as connection:
            assessment_before = _rows(connection.connection, ASSESSMENT_TABLE)
            clarification_before = _rows(connection.connection, CLARIFICATION_TABLE)
        assessment_contract = next(row for row in _table_info(database, ASSESSMENT_TABLE) if row[1] == "contract_version")
        clarification_parent = next(row for row in _table_info(database, CLARIFICATION_TABLE) if row[1] == "parent_area_id")
        assert (assessment_contract[2], assessment_contract[3]) == ("TEXT", 1)
        assert (clarification_parent[2], clarification_parent[3]) == ("TEXT", 0)
        assert CandidatePhysicalSchemaInspector(engine).inspect().status is CandidateSchemaStatus.UNSUPPORTED_DRIFT
    else:
        assessment_columns_before, assessment_before = _sqlite_snapshot(database, ASSESSMENT_TABLE)
        clarification_columns_before, clarification_before = _sqlite_snapshot(database, CLARIFICATION_TABLE)
        assert "contract_version" not in assessment_columns_before
        assert "parent_area_id" not in clarification_columns_before

    migration = _migration()
    with sqlite3.connect(database) as connection:
        migration(connection)
        assessment_after_first_run = _rows(connection, ASSESSMENT_TABLE)
        clarification_after_first_run = _rows(connection, CLARIFICATION_TABLE)
        migration(connection)
        assert _rows(connection, ASSESSMENT_TABLE) == assessment_after_first_run
        assert _rows(connection, CLARIFICATION_TABLE) == clarification_after_first_run

    if broken_migration_already_applied:
        assert assessment_after_first_run == assessment_before
        assert clarification_after_first_run == clarification_before
        with sqlite3.connect(database) as connection:
            preserved_objects = {
                (row[0], row[1]) for row in connection.execute(
                    "SELECT type, name FROM sqlite_master WHERE tbl_name=?", (ASSESSMENT_TABLE,)
                )
            }
        assert ("index", "ix_synthetic_assessment_json") in preserved_objects
        assert ("trigger", "trg_synthetic_assessment_update") in preserved_objects
    else:
        current_assessment_columns, current_assessment = assessment_after_first_run
        current_clarification_columns, current_clarification = clarification_after_first_run
        assert [name for name in assessment_columns_before if name in current_assessment_columns] == assessment_columns_before
        assert [name for name in clarification_columns_before if name in current_clarification_columns] == clarification_columns_before
        assert tuple(current_assessment[0][current_assessment_columns.index(name)] for name in assessment_columns_before) == assessment_before[0]
        assert tuple(current_clarification[0][current_clarification_columns.index(name)] for name in clarification_columns_before) == clarification_before[0]
        assert current_assessment[0][current_assessment_columns.index("contract_version")] == "legacy_questions"
        assert current_clarification[0][current_clarification_columns.index("parent_area_id")] is None
        assert current_clarification[0][current_clarification_columns.index("round_number")] is None

    assert dict((name, (declared_type, not_null)) for _, name, declared_type, not_null, *_ in _table_info(database, ASSESSMENT_TABLE))["contract_version"] == ("VARCHAR(32)", 0)
    assert dict((name, (declared_type, not_null)) for _, name, declared_type, not_null, *_ in _table_info(database, CLARIFICATION_TABLE))["parent_area_id"] == ("VARCHAR(36)", 0)
    _assert_orm_compatible(engine)

    startup = CandidateCompatibilityRuntime(engine).apply()
    assert startup.schema_after.status is CandidateSchemaStatus.COMPATIBLE
    assert CandidatePhysicalSchemaInspector(engine).inspect().status is CandidateSchemaStatus.COMPATIBLE
    engine.dispose()


def _table_info(database: Path, table: str) -> list[tuple]:
    with sqlite3.connect(database) as connection:
        return connection.execute(f'PRAGMA table_info("{table}")').fetchall()


def test_sqlite_refinement_migration_rolls_back_partial_reconstruction(tmp_path: Path) -> None:
    database = tmp_path / "adviser-migration-rollback.db"
    engine = _engine(database)
    _make_pre_280_database(engine, broken_migration_already_applied=True)
    before_assessment = _sqlite_snapshot(database, ASSESSMENT_TABLE)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "PRAGMA writable_schema=ON"
        )
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (CLARIFICATION_TABLE,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
            (create_sql.replace("parent_area_id TEXT NULL", "parent_area_id BLOB NULL"), CLARIFICATION_TABLE),
        )
        connection.execute("PRAGMA writable_schema=OFF")
        connection.execute("PRAGMA schema_version=999")
        with pytest.raises(RuntimeError, match="unrecognized physical declaration"):
            _migration()(connection)

    assert _sqlite_snapshot(database, ASSESSMENT_TABLE) == before_assessment
    with sqlite3.connect(database) as connection:
        schema_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (ASSESSMENT_TABLE,)
        ).fetchone()[0]
    assert "contract_version TEXT NOT NULL DEFAULT 'legacy_questions'" in schema_sql
    engine.dispose()
