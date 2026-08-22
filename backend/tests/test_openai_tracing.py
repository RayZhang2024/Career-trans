from types import SimpleNamespace

from app.agents.job_extraction import OpenAIJobExtractor
from app.agents.openai_client import create_traced_openai_client


def test_traced_client_wraps_responses_client_with_stage_name(monkeypatch) -> None:
    created = object()
    wrapped = object()
    observed: dict[str, object] = {}

    monkeypatch.setattr("app.agents.openai_client.OpenAI", lambda **_: created)

    def fake_wrap(client: object, *, chat_name: str) -> object:
        observed["client"] = client
        observed["chat_name"] = chat_name
        return wrapped

    monkeypatch.setattr("app.agents.openai_client.wrap_openai", fake_wrap)

    result = create_traced_openai_client(
        api_key="test-key",
        trace_name="job_extraction",
    )

    assert result is wrapped
    assert observed == {"client": created, "chat_name": "job_extraction"}


def test_injected_fake_client_bypasses_tracing_factory(monkeypatch) -> None:
    fake_client = SimpleNamespace()

    def fail_if_called(**_: object) -> object:
        raise AssertionError("Injected clients must not be wrapped.")

    monkeypatch.setattr(
        "app.agents.job_extraction.create_traced_openai_client",
        fail_if_called,
    )

    extractor = OpenAIJobExtractor(
        api_key="",
        model="test",
        client=fake_client,
    )

    assert extractor._client is fake_client
