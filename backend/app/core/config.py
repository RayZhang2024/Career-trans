from functools import lru_cache

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

    greenhouse_board_tokens: str = ""
    lever_site_tokens: str = ""

    @staticmethod
    def configured_tokens(value: str) -> list[str]:
        return [token.strip() for token in value.split(",") if token.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
