"""Factory for traced production OpenAI clients."""

from langsmith.wrappers import wrap_openai
from openai import OpenAI


def create_traced_openai_client(*, api_key: str, trace_name: str) -> OpenAI:
    """Create an OpenAI Responses client with LangSmith-compatible tracing."""
    return wrap_openai(OpenAI(api_key=api_key), chat_name=trace_name)
