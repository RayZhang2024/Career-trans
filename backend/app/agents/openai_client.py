"""Factory for traced production OpenAI clients."""

from langsmith.wrappers import wrap_openai
from openai import OpenAI


def create_traced_openai_client(
    *, api_key: str, trace_name: str, base_url: str | None = None,
    timeout: float | None = None, max_retries: int | None = None,
) -> OpenAI:
    """Create an OpenAI Responses client with LangSmith-compatible tracing."""
    options: dict[str, object] = {"api_key": api_key, "base_url": base_url}
    if timeout is not None:
        options["timeout"] = timeout
    if max_retries is not None:
        options["max_retries"] = max_retries
    return wrap_openai(OpenAI(**options), chat_name=trace_name)
