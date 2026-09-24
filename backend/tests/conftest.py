from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.config import Settings
from app.schemas.ai_settings import ReasoningEffort, UserAiPreferences
from app.main import app
from app.services.llm_runtime import resolve_runtime_snapshot
from app import models  # noqa: F401


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as session:
        yield session

    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def runtime_snapshot_a():
    settings = Settings(
        _env_file=None,
        default_llm_provider="openai",
        cv_semantic_extraction_model="gpt-5.6-luna",
        candidate_adviser_model="gpt-5.6-luna",
        job_extraction_model="gpt-5.6-luna",
        requirement_matching_model="gpt-5.6-luna",
        career_alignment_model="gpt-5.6-luna",
        job_relevance_model="gpt-5.6-luna",
        job_archetype_model="gpt-5.6-luna",
        agentic_discovery_model="gpt-5.6-luna",
        application_drafting_model="gpt-5.6-luna",
    )
    return resolve_runtime_snapshot(
        settings,
        UserAiPreferences(default_model="gpt-5.6-sol", default_reasoning_effort=ReasoningEffort.HIGH),
        preference_revision=7,
        persisted_override_provider="openai",
    )
