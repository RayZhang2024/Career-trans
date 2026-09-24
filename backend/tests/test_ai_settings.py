import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.user import User
from app.models.user_ai_settings import UserAiSettings
from app.schemas.ai_settings import (
    PreferenceActivity,
    ReasoningEffort,
    SemanticOperation,
    UserAiPreferences,
    UserAiSettingsReplace,
)
from app.services.ai_settings_service import (
    AiSettingsConflictError,
    AiSettingsService,
    AiSettingsValidationError,
)
from app.services.llm_runtime import (
    JOB_EVALUATION_OPERATIONS,
    PREPARATION_OPERATIONS,
    MODEL_CAPABILITIES,
    RuntimePreferenceError,
    resolve_runtime_snapshot,
    validate_preferences,
)
from app.services.user_job_discovery_service import UserJobDiscoveryService
from app.services.application_preparation_service import ApplicationPreparationService


def _user(session, user_id: str) -> None:
    session.add(User(id=user_id, email=f"{user_id}@example.test", password_hash="not-a-real-password"))
    session.commit()


def _base_settings(**kwargs) -> Settings:
    return Settings(**({"openai_api_key": None} | kwargs))


def test_no_row_is_virtual_revision_zero_with_deployment_inheritance(db_session) -> None:
    _user(db_session, "runtime-user")
    settings = _base_settings(
        cv_semantic_extraction_model="gpt-5.6-terra",
        candidate_adviser_model="gpt-5.6-terra",
        job_extraction_model="gpt-5.6-terra",
        requirement_matching_model="gpt-5.6-terra",
        career_alignment_model="gpt-5.6-terra",
        job_relevance_model="gpt-5.6-terra",
        job_archetype_model="gpt-5.6-terra",
        agentic_discovery_model="gpt-5.6-terra",
        application_drafting_model="gpt-5.6-terra",
    )
    service = AiSettingsService(db_session, settings=settings)

    response = service.read("runtime-user")

    assert response.revision == 0
    assert response.preference_activity is PreferenceActivity.INHERITED
    assert response.overrides_active is False
    assert response.effective[SemanticOperation.JOB_RELEVANCE].model == "gpt-5.6-terra"
    assert response.effective[SemanticOperation.JOB_RELEVANCE].reasoning_effort is None
    assert response.effective[SemanticOperation.JOB_RELEVANCE].inherited_model is True


def test_full_replacement_revision_cas_and_user_isolation(db_session) -> None:
    _user(db_session, "runtime-a")
    _user(db_session, "runtime-b")
    service = AiSettingsService(db_session, settings=_base_settings())
    first = UserAiSettingsReplace(expected_revision=0, default_model="gpt-5.6-sol", default_reasoning_effort="high")

    written = service.replace("runtime-a", first)

    assert written.revision == 1
    assert written.preference_activity is PreferenceActivity.ACTIVE
    assert written.effective[SemanticOperation.CAREER_ALIGNMENT].model == "gpt-5.6-sol"
    assert written.effective[SemanticOperation.CAREER_ALIGNMENT].reasoning_effort is ReasoningEffort.HIGH
    assert service.read("runtime-b").revision == 0
    with pytest.raises(AiSettingsConflictError):
        service.replace("runtime-a", first)
    assert service.read("runtime-a").revision == 1

    second = UserAiSettingsReplace(
        expected_revision=1,
        default_model="gpt-5.6-terra",
        operation_overrides={"job_relevance": {"model": "gpt-5.6-luna", "reasoning_effort": "low"}},
    )
    updated = service.replace("runtime-a", second)
    assert updated.revision == 2
    assert updated.effective[SemanticOperation.JOB_RELEVANCE].model == "gpt-5.6-luna"
    assert updated.effective[SemanticOperation.JOB_RELEVANCE].reasoning_effort is ReasoningEffort.LOW
    assert updated.effective[SemanticOperation.JOB_ARCHETYPE].model == "gpt-5.6-terra"


def test_invalid_preference_write_is_atomic_and_rejects_unknown_fields(db_session) -> None:
    _user(db_session, "matrix-user")
    service = AiSettingsService(db_session, settings=_base_settings(job_archetype_model="gpt-4o"))
    with pytest.raises(AiSettingsValidationError, match="reasoning effort"):
        service.replace("matrix-user", UserAiSettingsReplace(expected_revision=0, default_reasoning_effort="max"))
    assert db_session.get(UserAiSettings, "matrix-user") is None
    with pytest.raises(ValidationError):
        UserAiSettingsReplace.model_validate({"expected_revision": 0, "provider": "ollama"})
    with pytest.raises(ValidationError):
        UserAiSettingsReplace.model_validate({"expected_revision": 0, "operation_overrides": {"invented": {"model": "gpt-5.6-luna"}}})
    with pytest.raises(ValidationError):
        UserAiSettingsReplace.model_validate({"expected_revision": 0, "operation_overrides": {"job_relevance": {}}})


def test_partial_operation_override_inherits_other_field_and_defaults_do_not_rewrite_it() -> None:
    settings = _base_settings()
    initial = UserAiPreferences.model_validate({
        "default_model": "gpt-5.6-sol",
        "default_reasoning_effort": "high",
        "operation_overrides": {"job_relevance": {"reasoning_effort": "low"}},
    })
    snapshot = resolve_runtime_snapshot(settings, initial, preference_revision=7, persisted_override_provider="openai")
    assert snapshot.operation(SemanticOperation.JOB_RELEVANCE).model == "gpt-5.6-sol"
    assert snapshot.operation(SemanticOperation.JOB_RELEVANCE).reasoning_effort is ReasoningEffort.LOW
    assert snapshot.operation(SemanticOperation.JOB_ARCHETYPE).reasoning_effort is ReasoningEffort.HIGH
    changed = initial.model_copy(update={"default_model": "gpt-5.6-terra"})
    changed_snapshot = resolve_runtime_snapshot(settings, changed, preference_revision=8, persisted_override_provider="openai")
    assert changed_snapshot.operation(SemanticOperation.JOB_RELEVANCE).model == "gpt-5.6-terra"
    assert changed_snapshot.operation(SemanticOperation.JOB_RELEVANCE).reasoning_effort is ReasoningEffort.LOW


def test_legacy_catalog_compatibility_is_not_user_selectability() -> None:
    assert MODEL_CAPABILITIES.get("openai", "gpt-4.1-2025-04-14").structured_output
    assert all(item.id in {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"} for item in MODEL_CAPABILITIES.selectable_openai)
    with pytest.raises(RuntimePreferenceError, match="user-selectable"):
        validate_preferences(_base_settings(), UserAiPreferences(default_model="gpt-4o"))
    assert resolve_runtime_snapshot(_base_settings(job_relevance_model="gpt-4o")).operation("job_relevance").model == "gpt-4o"


def test_provider_mismatch_retains_preferences_and_returning_provider_reactivates(db_session) -> None:
    _user(db_session, "provider-user")
    openai = _base_settings()
    service = AiSettingsService(db_session, settings=openai)
    service.replace("provider-user", UserAiSettingsReplace(expected_revision=0, default_model="gpt-5.6-sol"))
    record = db_session.get(UserAiSettings, "provider-user")
    assert record is not None
    original_json = record.preferences_json

    ollama = _base_settings(default_llm_provider="ollama", ollama_base_url="http://localhost:11434", job_relevance_model="local-relevance")
    inactive = AiSettingsService(db_session, settings=ollama).read("provider-user")
    assert inactive.preference_activity is PreferenceActivity.INACTIVE_PROVIDER_MISMATCH
    assert inactive.persisted_override_provider == "openai"
    assert inactive.effective[SemanticOperation.JOB_RELEVANCE].model == "local-relevance"
    assert db_session.get(UserAiSettings, "provider-user").preferences_json == original_json

    active = AiSettingsService(db_session, settings=openai).read("provider-user")
    assert active.preference_activity is PreferenceActivity.ACTIVE
    assert active.effective[SemanticOperation.JOB_RELEVANCE].model == "gpt-5.6-sol"


def test_catalog_invalid_preferences_are_retained_inactive_and_fall_back(db_session, monkeypatch) -> None:
    _user(db_session, "stale-user")
    settings = _base_settings(
        cv_semantic_extraction_model="gpt-5.6-terra",
        candidate_adviser_model="gpt-5.6-terra",
        job_extraction_model="gpt-5.6-terra",
        requirement_matching_model="gpt-5.6-terra",
        career_alignment_model="gpt-5.6-terra",
        job_relevance_model="gpt-5.6-terra",
        job_archetype_model="gpt-5.6-terra",
        agentic_discovery_model="gpt-5.6-terra",
        application_drafting_model="gpt-5.6-terra",
    )
    service = AiSettingsService(db_session, settings=settings)
    service.replace("stale-user", UserAiSettingsReplace(expected_revision=0, default_model="gpt-5.6-luna"))
    row = db_session.get(UserAiSettings, "stale-user")
    original = row.preferences_json
    original_get = MODEL_CAPABILITIES.get

    def changed_catalog(provider: str, model: str):
        if model == "gpt-5.6-luna":
            return None
        return original_get(provider, model)

    monkeypatch.setattr(MODEL_CAPABILITIES, "get", changed_catalog)
    read = service.read("stale-user")
    assert read.preference_activity is PreferenceActivity.INACTIVE_INVALID
    assert read.effective[SemanticOperation.JOB_RELEVANCE].model == "gpt-5.6-terra"
    assert row.preferences_json == original
    assert service.snapshot_for_user("stale-user").operation("job_relevance").model == "gpt-5.6-terra"


def test_fingerprint_projections_ignore_preference_revision_and_unrelated_operations() -> None:
    settings = _base_settings()
    base = resolve_runtime_snapshot(settings, preference_revision=2)
    next_revision = resolve_runtime_snapshot(settings, preference_revision=9)
    assert base.fingerprint_projection(JOB_EVALUATION_OPERATIONS) == next_revision.fingerprint_projection(JOB_EVALUATION_OPERATIONS)
    assert base.fingerprint_projection(PREPARATION_OPERATIONS) == next_revision.fingerprint_projection(PREPARATION_OPERATIONS)

    adviser_changed = UserAiPreferences(operation_overrides={"candidate_adviser": {"model": "gpt-5.6-sol"}})
    adviser = resolve_runtime_snapshot(settings, adviser_changed, preference_revision=3, persisted_override_provider="openai")
    assert base.fingerprint_projection(JOB_EVALUATION_OPERATIONS) == adviser.fingerprint_projection(JOB_EVALUATION_OPERATIONS)
    assert base.fingerprint_projection(PREPARATION_OPERATIONS) == adviser.fingerprint_projection(PREPARATION_OPERATIONS)

    drafting_changed = UserAiPreferences(operation_overrides={"application_drafting": {"reasoning_effort": "high"}})
    drafting = resolve_runtime_snapshot(settings, drafting_changed, preference_revision=4, persisted_override_provider="openai")
    assert base.fingerprint_projection(JOB_EVALUATION_OPERATIONS) == drafting.fingerprint_projection(JOB_EVALUATION_OPERATIONS)
    assert base.fingerprint_projection(PREPARATION_OPERATIONS) != drafting.fingerprint_projection(PREPARATION_OPERATIONS)

    relevance_changed = UserAiPreferences(operation_overrides={"job_relevance": {"model": "gpt-5.6-sol"}})
    relevance = resolve_runtime_snapshot(settings, relevance_changed, preference_revision=4, persisted_override_provider="openai")
    assert base.fingerprint_projection(JOB_EVALUATION_OPERATIONS) != relevance.fingerprint_projection(JOB_EVALUATION_OPERATIONS)

    relevance_effort_changed = UserAiPreferences(operation_overrides={"job_relevance": {"reasoning_effort": "high"}})
    relevance_effort = resolve_runtime_snapshot(settings, relevance_effort_changed, preference_revision=4, persisted_override_provider="openai")
    assert base.fingerprint_projection(JOB_EVALUATION_OPERATIONS) != relevance_effort.fingerprint_projection(JOB_EVALUATION_OPERATIONS)

    drafting_model_changed = UserAiPreferences(operation_overrides={"application_drafting": {"model": "gpt-5.6-sol"}})
    drafting_model = resolve_runtime_snapshot(settings, drafting_model_changed, preference_revision=5, persisted_override_provider="openai")
    assert base.fingerprint_projection(JOB_EVALUATION_OPERATIONS) == drafting_model.fingerprint_projection(JOB_EVALUATION_OPERATIONS)
    assert base.fingerprint_projection(PREPARATION_OPERATIONS) != drafting_model.fingerprint_projection(PREPARATION_OPERATIONS)


def test_fingerprint_projection_ignores_non_material_capability_metadata() -> None:
    snapshot = resolve_runtime_snapshot(_base_settings())
    capability_changed = replace(
        snapshot,
        operations=tuple(
            (operation, replace(value, capability_id=f"changed-catalog-entry:{value.capability_id}"))
            for operation, value in snapshot.operations
        ),
    )

    assert snapshot.fingerprint_projection(JOB_EVALUATION_OPERATIONS) == capability_changed.fingerprint_projection(JOB_EVALUATION_OPERATIONS)
    assert snapshot.fingerprint_projection(PREPARATION_OPERATIONS) == capability_changed.fingerprint_projection(PREPARATION_OPERATIONS)


def test_user_effective_semantic_builders_are_not_shared_and_use_snapshot_models(monkeypatch) -> None:
    from app.api import deps

    settings = _base_settings(openai_api_key="test-server-key")
    one = resolve_runtime_snapshot(settings, UserAiPreferences(default_model="gpt-5.6-luna"), preference_revision=1, persisted_override_provider="openai")
    two = resolve_runtime_snapshot(settings, UserAiPreferences(default_model="gpt-5.6-sol"), preference_revision=1, persisted_override_provider="openai")
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    first = deps._build_user_job_ranking_service(settings, one)
    second = deps._build_user_job_ranking_service(settings, two)
    assert first is not second
    assert first._relevance_agent._model == "gpt-5.6-luna"
    assert second._relevance_agent._model == "gpt-5.6-sol"
    assert first._career_analysis_graph is not second._career_analysis_graph


def test_fingerprint_services_use_purpose_specific_snapshot_projections(db_session) -> None:
    base = resolve_runtime_snapshot(_base_settings())
    app_only = resolve_runtime_snapshot(
        _base_settings(),
        UserAiPreferences(operation_overrides={"application_drafting": {"model": "gpt-5.6-sol"}}),
        persisted_override_provider="openai",
    )
    relevance_only = resolve_runtime_snapshot(
        _base_settings(),
        UserAiPreferences(operation_overrides={"job_relevance": {"reasoning_effort": "high"}}),
        persisted_override_provider="openai",
    )
    unrelated = resolve_runtime_snapshot(
        _base_settings(),
        UserAiPreferences(operation_overrides={"candidate_adviser": {"model": "gpt-5.6-sol"}}),
        persisted_override_provider="openai",
    )
    discovery_base = UserJobDiscoveryService(db_session, runtime_snapshot=base).evaluation_contract_fingerprint()
    assert UserJobDiscoveryService(db_session, runtime_snapshot=app_only).evaluation_contract_fingerprint() == discovery_base
    assert UserJobDiscoveryService(db_session, runtime_snapshot=unrelated).evaluation_contract_fingerprint() == discovery_base
    assert UserJobDiscoveryService(db_session, runtime_snapshot=relevance_only).evaluation_contract_fingerprint() != discovery_base

    def preparation(snapshot):
        return ApplicationPreparationService(
            db_session,
            graph=None,
            drafting_agent=None,
            user_discovery=None,
            settings=_base_settings(),
            runtime_snapshot=snapshot,
        )._contract_fingerprint()

    prep_base = preparation(base)
    assert preparation(app_only) != prep_base
    assert preparation(unrelated) == prep_base
    assert preparation(relevance_only) == prep_base


def test_credentials_are_not_part_of_preference_documents() -> None:
    prefs = UserAiPreferences(default_model="gpt-5.6-luna")
    assert "api_key" not in json.dumps(prefs.model_dump(mode="json"))


def test_authenticated_ai_settings_api_is_credential_free_and_provider_is_not_browser_controlled(client, monkeypatch) -> None:
    settings = _base_settings(openai_api_key=None)
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    credentials = {"email": "ai-settings@example.com", "password": "Strong-test-password-92!"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    models = client.get("/api/v1/ai/models", headers=headers)
    read = client.get("/api/v1/ai/settings", headers=headers)
    assert models.status_code == 200
    assert {item["id"] for item in models.json()["models"]} == {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"}
    assert "api_key" not in models.text.casefold()
    assert read.status_code == 200 and read.json()["revision"] == 0
    assert "api_key" not in read.text.casefold()

    write = client.put(
        "/api/v1/ai/settings",
        headers=headers,
        json={"expected_revision": 0, "default_model": "gpt-5.6-luna", "default_reasoning_effort": "high"},
    )
    assert write.status_code == 200 and write.json()["revision"] == 1
    assert write.json()["persisted_override_provider"] == "openai"
    assert write.json()["effective"]["job_relevance"]["reasoning_effort"] == "high"
    assert client.put("/api/v1/ai/settings", headers=headers, json={"expected_revision": 0}).status_code == 409
    assert client.put("/api/v1/ai/settings", headers=headers, json={"expected_revision": 1, "provider": "ollama"}).status_code == 422
    assert client.get("/api/v1/ai/settings").status_code == 401
    assert client.get("/api/v1/users/runtime-user/ai/settings", headers=headers).status_code == 404


def test_ollama_catalog_and_settings_truthfully_disable_user_overrides(client, monkeypatch) -> None:
    settings = _base_settings(default_llm_provider="ollama", ollama_base_url="http://localhost:11434", job_relevance_model="local-model")
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    credentials = {"email": "ollama-settings@example.com", "password": "Strong-test-password-92!"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    catalog = client.get("/api/v1/ai/models", headers=headers)
    settings_read = client.get("/api/v1/ai/settings", headers=headers)
    assert catalog.status_code == 200 and catalog.json() == {"provider": "ollama", "user_overrides_supported": False, "models": []}
    assert settings_read.status_code == 200
    assert settings_read.json()["user_overrides_supported"] is False
    assert settings_read.json()["effective"]["job_relevance"]["model"] == "local-model"
    assert client.put("/api/v1/ai/settings", headers=headers, json={"expected_revision": 0, "default_model": "gpt-5.6-luna"}).status_code == 422


def test_provider_free_current_opportunity_and_discovery_history_reads_need_no_key(client, monkeypatch) -> None:
    settings = _base_settings(openai_api_key=None)
    monkeypatch.setattr("app.services.ai_settings_service.get_settings", lambda: settings)
    credentials = {"email": "provider-free-reads@example.com", "password": "Strong-test-password-92!"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    opportunities = client.get("/api/v1/jobs/opportunities", headers=headers)
    history = client.get("/api/v1/jobs/discovery-runs", headers=headers)
    assert opportunities.status_code == 200
    assert history.status_code == 200
