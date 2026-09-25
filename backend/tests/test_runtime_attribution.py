"""Provider-free V1C attribution contract regressions."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateCVReviewBaseline
from app.core.config import Settings
from app.schemas.ai_settings import ReasoningEffort, SemanticOperation, UserAiPreferences
from app.schemas.cv_ingestion import CandidateCVData, CVIngestionState
from app.schemas.semantic_runtime_attribution import (
    SemanticRuntimeAttribution,
    SemanticRuntimeAttributionStatus,
)
from app.services.cv_ingestion_service import CVIngestionReadService, CVIngestionService
from app.services.semantic_runtime_attribution import (
    RuntimeAttributionIntegrityError,
    available_attribution,
    canonical_attribution_json,
    legacy_unavailable_attribution,
    not_used_attribution,
    read_attribution,
)
from app.services.llm_runtime import resolve_runtime_snapshot


class _Interpreter:
    def __init__(self, result: CandidateCVData | None = None, error: Exception | None = None) -> None:
        self.calls = 0
        self.result = result or CandidateCVData(skills=[{"name": "Python"}])
        self.error = error

    def interpret(self, _documents):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _upload_markdown(service: CVIngestionService, user: str = "user-a"):
    return service.upload(user, [("synthetic.md", "text/markdown", b"# CV\nBuilt a synthetic service.")])


def test_runtime_attribution_contract_and_projection_are_strict_and_privacy_minimal(runtime_snapshot_a):
    value = available_attribution(runtime_snapshot_a, (SemanticOperation.CV_SEMANTIC_EXTRACTION,))
    assert value.status == SemanticRuntimeAttributionStatus.AVAILABLE
    assert value.model_dump(mode="json") == {
        "status": "available",
        "provider": "openai",
        "operations": {"cv_semantic_extraction": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}},
    }
    serialized = canonical_attribution_json(value)
    assert serialized == json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for forbidden in ("revision", "capability", "inherit", "credential", "base_url", "prompt", "token", "latency", "preference"):
        assert forbidden not in serialized
    with pytest.raises(ValidationError):
        SemanticRuntimeAttribution(status="available", provider="", operations={})
    with pytest.raises(ValidationError):
        SemanticRuntimeAttribution(status="available", provider="openai", operations={})
    with pytest.raises(ValidationError):
        SemanticRuntimeAttribution(status="available", provider="openai", operations={"job_extraction": {"model": "", "reasoning_effort": None}})
    with pytest.raises(ValidationError):
        SemanticRuntimeAttribution.model_validate({"status": "available", "provider": "openai", "operations": {"invented_operation": {"model": "m", "reasoning_effort": None}}})
    with pytest.raises(ValidationError):
        SemanticRuntimeAttribution(status="not_used", provider="openai", operations={})
    with pytest.raises(ValidationError):
        SemanticRuntimeAttribution(status="legacy_unavailable", provider=None, operations={"job_extraction": {"model": "old", "reasoning_effort": None}})
    assert not_used_attribution().model_dump(mode="json") == {"status": "not_used", "provider": None, "operations": {}}
    assert legacy_unavailable_attribution().model_dump(mode="json") == {"status": "legacy_unavailable", "provider": None, "operations": {}}


def test_gpt6_resolved_model_and_effort_are_projected_into_v1c_attribution():
    snapshot = resolve_runtime_snapshot(
        Settings(),
        UserAiPreferences(default_model="gpt-6-astra", default_reasoning_effort=ReasoningEffort.XHIGH),
        preference_revision=3,
        persisted_override_provider="openai",
    )
    attribution = available_attribution(snapshot, (SemanticOperation.CV_SEMANTIC_EXTRACTION,))
    assert attribution.model_dump(mode="json") == {
        "status": "available",
        "provider": "openai",
        "operations": {"cv_semantic_extraction": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}},
    }


def test_cv_uploaded_null_structured_not_used_semantic_available_and_failure_null(db_session, runtime_snapshot_a):
    structured_service = CVIngestionService(db_session, runtime_snapshot=runtime_snapshot_a)
    structured = structured_service.upload("user-a", [("cv.json", "application/json", b'{"skills":[{"name":"Python"}]}')])
    assert structured.runtime_attribution is None
    structured_done = structured_service.interpret("user-a", structured.id)
    assert structured_done.runtime_attribution.status == "not_used"

    interpreter = _Interpreter()
    service = CVIngestionService(db_session, interpreter=interpreter, runtime_snapshot=runtime_snapshot_a)
    uploaded = _upload_markdown(service)
    interpreted = service.interpret("user-a", uploaded.id)
    assert interpreter.calls == 1
    assert interpreted.runtime_attribution == available_attribution(runtime_snapshot_a, (SemanticOperation.CV_SEMANTIC_EXTRACTION,))
    persisted = db_session.get(CandidateCVIngestionDraft, uploaded.id)
    saved = persisted.runtime_attribution_json
    service.edit_review("user-a", uploaded.id, interpreted.merged)
    service.confirm("user-a", uploaded.id)
    assert db_session.get(CandidateCVIngestionDraft, uploaded.id).runtime_attribution_json == saved

    failing = CVIngestionService(db_session, interpreter=_Interpreter(error=RuntimeError("safe test failure")), runtime_snapshot=runtime_snapshot_a)
    failed_draft = _upload_markdown(failing)
    with pytest.raises(RuntimeError):
        failing.interpret("user-a", failed_draft.id)
    failure_row = db_session.get(CandidateCVIngestionDraft, failed_draft.id)
    assert failure_row.state == CVIngestionState.UPLOADED
    assert failure_row.runtime_attribution_json is None
    assert CVIngestionReadService(db_session).read("user-a", failed_draft.id).runtime_attribution is None


def test_semantic_cv_requires_owner_snapshot_before_interpreter_execution(db_session):
    interpreter = _Interpreter()
    service = CVIngestionService(db_session, interpreter=interpreter)
    draft = _upload_markdown(service)
    with pytest.raises(ValueError, match="resolved runtime snapshot"):
        service.interpret("user-a", draft.id)
    assert interpreter.calls == 0
    row = db_session.get(CandidateCVIngestionDraft, draft.id)
    assert row.state == CVIngestionState.UPLOADED and row.runtime_attribution_json is None


def test_cv_baseline_failure_rolls_back_attribution_and_review_transition(db_session, runtime_snapshot_a, monkeypatch):
    service = CVIngestionService(db_session, interpreter=_Interpreter(), runtime_snapshot=runtime_snapshot_a)
    draft = _upload_markdown(service)
    create_baseline = service._create_baseline_once

    def fail_after_baseline_flush(current_draft, evidence):
        create_baseline(current_draft, evidence)
        raise RuntimeError("safe baseline failure")

    monkeypatch.setattr(service, "_create_baseline_once", fail_after_baseline_flush)
    with pytest.raises(RuntimeError, match="safe baseline failure"):
        service.interpret("user-a", draft.id)

    persisted = db_session.get(CandidateCVIngestionDraft, draft.id)
    assert persisted.state == CVIngestionState.UPLOADED
    assert persisted.runtime_attribution_json is None
    assert persisted.merged_json is None
    assert db_session.scalar(select(CandidateCVReviewBaseline).where(CandidateCVReviewBaseline.draft_id == draft.id)) is None


def test_cv_read_projects_legacy_terminal_and_fails_closed_on_malformed_attribution(db_session):
    service = CVIngestionService(db_session)
    draft = _upload_markdown(service)
    row = db_session.get(CandidateCVIngestionDraft, draft.id)
    row.state = CVIngestionState.REVIEW_READY
    row.merged_json = CandidateCVData().model_dump_json()
    db_session.commit()
    assert CVIngestionReadService(db_session).read("user-a", draft.id).runtime_attribution.status == "legacy_unavailable"
    row.state = CVIngestionState.CONFIRMED
    db_session.commit()
    assert CVIngestionReadService(db_session).read("user-a", draft.id).runtime_attribution.status == "legacy_unavailable"
    row.state = CVIngestionState.UPLOADED
    assert CVIngestionReadService(db_session).read("user-a", draft.id).runtime_attribution is None
    row.runtime_attribution_json = '{"status":"available","provider":"private"}'
    db_session.commit()
    with pytest.raises(RuntimeAttributionIntegrityError, match="Persisted runtime attribution is invalid"):
        CVIngestionReadService(db_session).read("user-a", draft.id)


def test_cv_history_read_is_owned_and_independent_of_runtime_builder(db_session, runtime_snapshot_a, monkeypatch):
    from app.api import deps

    calls = []
    monkeypatch.setattr(deps, "get_semantic_response_client", lambda settings, **kwargs: (calls.append(kwargs["runtime_snapshot"]) or object()))
    monkeypatch.setattr(deps, "SemanticCVInterpreter", lambda _client, _model: _Interpreter())
    service = deps._build_cv_ingestion_service(db_session, runtime_snapshot_a)
    draft = _upload_markdown(service)
    service.interpret("user-a", draft.id)
    assert calls == [runtime_snapshot_a]
    row = db_session.get(CandidateCVIngestionDraft, draft.id)
    row.runtime_attribution_json = canonical_attribution_json(available_attribution(runtime_snapshot_a, (SemanticOperation.CV_SEMANTIC_EXTRACTION,)))
    db_session.commit()
    monkeypatch.setattr(deps, "get_user_runtime_snapshot", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("runtime resolved during historical read")))
    from app.core.config import Settings
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(default_llm_provider="openai", cv_semantic_extraction_model="gpt-5.6-luna"))
    read_service = deps.get_user_cv_ingestion_read_service(db_session)
    assert read_service.read("user-a", draft.id).runtime_attribution == available_attribution(runtime_snapshot_a, (SemanticOperation.CV_SEMANTIC_EXTRACTION,))
    with pytest.raises(LookupError):
        read_service.read("user-b", draft.id)


def test_application_builder_uses_one_owner_snapshot_for_clients_and_persistence(db_session, runtime_snapshot_a, monkeypatch):
    from app.api import deps
    from app.core.config import Settings

    settings = Settings(default_llm_provider="openai")
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    constructed = []
    monkeypatch.setattr(deps, "get_semantic_response_client", lambda _settings, **kwargs: (constructed.append(kwargs["runtime_snapshot"]) or object()))

    class FakeDraftingAgent:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr(deps, "OpenAIApplicationDraftingAgent", FakeDraftingAgent)
    service = deps.get_application_preparation_service(db_session, runtime_snapshot_a)
    service._get_drafting_agent()
    assert constructed and all(snapshot is runtime_snapshot_a for snapshot in constructed)
    assert service._runtime_snapshot is runtime_snapshot_a


def test_cv_historical_http_read_uses_real_settings_free_dependency(client, db_session, monkeypatch):
    from app.api import deps
    from app.core.security import create_access_token
    from app.models.user import User

    db_session.add_all([
        User(id="owner", email="owner@example.test", password_hash="safe"),
        User(id="other", email="other@example.test", password_hash="safe"),
    ]); db_session.commit()
    draft = _upload_markdown(CVIngestionService(db_session), "owner")
    owner_headers = {"Authorization": f"Bearer {create_access_token('owner')}"}
    other_headers = {"Authorization": f"Bearer {create_access_token('other')}"}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("historical CV GET resolved current settings/provider")

    monkeypatch.setattr(deps, "get_user_runtime_snapshot", forbidden)
    monkeypatch.setattr(deps, "get_semantic_response_client", forbidden)
    monkeypatch.setattr(deps, "get_settings", forbidden)
    monkeypatch.setattr(deps, "get_settings", forbidden)
    response = client.get(f"/api/v1/cv-ingestion/{draft.id}", headers=owner_headers)
    assert response.status_code == 200 and response.json()["runtime_attribution"] is None
    assert client.get(f"/api/v1/cv-ingestion/{draft.id}", headers=other_headers).status_code == 404
    row = db_session.get(CandidateCVIngestionDraft, draft.id)
    row.runtime_attribution_json = '{"status":"available","provider":"private provider response"}'
    db_session.commit()
    invalid = client.get(f"/api/v1/cv-ingestion/{draft.id}", headers=owner_headers)
    assert invalid.status_code == 500
    assert invalid.json() == {"detail": "Persisted runtime attribution is invalid."}


def test_read_attribution_never_masks_malformed_persisted_json():
    with pytest.raises(RuntimeAttributionIntegrityError):
        read_attribution("not-json")
