from functools import lru_cache
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Career Agent API"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./career_agent.db"

    jwt_secret_key: str = Field(
        default="development-only-change-me",
        min_length=16,
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    openai_api_key: str | None = None
    openai_job_extraction_model: str = "gpt-5.6-luna"
    openai_requirement_matching_model: str = "gpt-5.6-luna"
    openai_career_alignment_model: str = "gpt-5.6-luna"
    openai_job_relevance_model: str = "gpt-5.6-luna"
    openai_job_archetype_model: str = "gpt-5.6-luna"
    openai_agentic_discovery_model: str = "gpt-5.6-luna"
    openai_web_search_model: str = "gpt-5.6-luna"

    langsmith_tracing: bool | None = None
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None
    langsmith_endpoint: str | None = None

    greenhouse_board_tokens: str = ""
    ashby_board_tokens: str = ""
    lever_site_tokens: str = ""
    smartrecruiters_company_ids: str = ""
    recruitee_company_tokens: str = ""
    brave_search_api_key: str | None = None
    agentic_search_provider: str = "openai"

    @staticmethod
    def configured_tokens(value: str) -> list[str]:
        return [token.strip() for token in value.split(",") if token.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def configure_langsmith_environment(settings: Settings) -> None:
    """Expose configured LangSmith settings without overriding process config."""
    configured = {
        "LANGSMITH_TRACING": (
            str(settings.langsmith_tracing).lower()
            if "langsmith_tracing" in settings.model_fields_set
            else None
        ),
        "LANGSMITH_API_KEY": settings.langsmith_api_key,
        "LANGSMITH_PROJECT": settings.langsmith_project,
        "LANGSMITH_ENDPOINT": settings.langsmith_endpoint,
    }
    for name, value in configured.items():
        if value:
            os.environ.setdefault(name, value)
