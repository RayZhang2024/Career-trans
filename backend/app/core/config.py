from functools import lru_cache
import os
import re

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas.ai_settings import ReasoningEffort


_DEFAULT_CODEX_EXTERNAL_DISCOVERY_MODEL = "gpt-5.6-luna"
_DEFAULT_LOCAL_CODEX_SEARCH_MODEL = "gpt-5.6-luna"
_CODEX_MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "cv_semantic_extraction_reasoning_effort",
        "candidate_adviser_reasoning_effort",
        "job_extraction_reasoning_effort",
        "requirement_matching_reasoning_effort",
        "career_alignment_reasoning_effort",
        "job_relevance_reasoning_effort",
        "job_archetype_reasoning_effort",
        "agentic_discovery_reasoning_effort",
        "application_drafting_reasoning_effort",
        mode="before",
    )
    @classmethod
    def _blank_reasoning_effort_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    app_name: str = "Career Agent API"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./career_agent.db"

    jwt_secret_key: str = Field(
        default="development-only-change-me",
        min_length=16,
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    openai_api_key: str | None = None
    default_llm_provider: str = "openai"
    llm_base_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    job_extraction_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("JOB_EXTRACTION_MODEL", "OPENAI_JOB_EXTRACTION_MODEL", "job_extraction_model", "openai_job_extraction_model"),
    )
    cv_semantic_extraction_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("CV_SEMANTIC_EXTRACTION_MODEL", "OPENAI_CV_SEMANTIC_EXTRACTION_MODEL", "cv_semantic_extraction_model", "openai_cv_semantic_extraction_model"),
    )
    candidate_adviser_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("CANDIDATE_ADVISER_MODEL", "candidate_adviser_model"),
    )
    cv_semantic_extraction_reasoning_effort: ReasoningEffort | None = None
    candidate_adviser_reasoning_effort: ReasoningEffort | None = None
    job_extraction_reasoning_effort: ReasoningEffort | None = None
    requirement_matching_reasoning_effort: ReasoningEffort | None = None
    career_alignment_reasoning_effort: ReasoningEffort | None = None
    job_relevance_reasoning_effort: ReasoningEffort | None = None
    job_archetype_reasoning_effort: ReasoningEffort | None = None
    agentic_discovery_reasoning_effort: ReasoningEffort | None = None
    application_drafting_reasoning_effort: ReasoningEffort | None = None
    application_drafting_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("APPLICATION_DRAFTING_MODEL", "application_drafting_model"),
    )
    requirement_matching_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("REQUIREMENT_MATCHING_MODEL", "OPENAI_REQUIREMENT_MATCHING_MODEL", "requirement_matching_model", "openai_requirement_matching_model"),
    )
    career_alignment_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("CAREER_ALIGNMENT_MODEL", "OPENAI_CAREER_ALIGNMENT_MODEL", "career_alignment_model", "openai_career_alignment_model"),
    )
    job_relevance_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("JOB_RELEVANCE_MODEL", "OPENAI_JOB_RELEVANCE_MODEL", "job_relevance_model", "openai_job_relevance_model"),
    )
    job_archetype_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("JOB_ARCHETYPE_MODEL", "OPENAI_JOB_ARCHETYPE_MODEL", "job_archetype_model", "openai_job_archetype_model"),
    )
    agentic_discovery_model: str = Field(
        default="gpt-5.6-luna",
        validation_alias=AliasChoices("AGENTIC_DISCOVERY_MODEL", "OPENAI_AGENTIC_DISCOVERY_MODEL", "agentic_discovery_model", "openai_agentic_discovery_model"),
    )
    codex_external_discovery_model: str = Field(
        default=_DEFAULT_CODEX_EXTERNAL_DISCOVERY_MODEL,
        validation_alias=AliasChoices(
            "CODEX_EXTERNAL_DISCOVERY_MODEL",
            "codex_external_discovery_model",
        ),
    )
    local_codex_discovery_enabled: bool = False
    local_codex_search_model: str = _DEFAULT_LOCAL_CODEX_SEARCH_MODEL
    local_codex_scheduled_discovery_capability: str = "unverified"
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
    tavily_api_key: str | None = None
    tavily_credential_encryption_key: str | None = None
    # Paid OpenAI web search is opt-in. Semantic OpenAI operations remain independent.
    agentic_search_provider: str = "disabled"

    @staticmethod
    def configured_tokens(value: str) -> list[str]:
        return [token.strip() for token in value.split(",") if token.strip()]

    @field_validator("codex_external_discovery_model", mode="before")
    @classmethod
    def _normalize_codex_external_discovery_model(cls, value: object) -> object:
        if value is None:
            return _DEFAULT_CODEX_EXTERNAL_DISCOVERY_MODEL
        if not isinstance(value, str):
            return value
        model = value.strip()
        if not model:
            return _DEFAULT_CODEX_EXTERNAL_DISCOVERY_MODEL
        if not _CODEX_MODEL_IDENTIFIER.fullmatch(model):
            raise ValueError(
                "CODEX_EXTERNAL_DISCOVERY_MODEL must be a non-empty Codex model identifier."
            )
        return model

    @field_validator("local_codex_search_model", mode="before")
    @classmethod
    def _normalize_local_codex_search_model(cls, value: object) -> object:
        if value is None:
            return _DEFAULT_LOCAL_CODEX_SEARCH_MODEL
        if not isinstance(value, str):
            return value
        model = value.strip()
        if not model:
            return _DEFAULT_LOCAL_CODEX_SEARCH_MODEL
        if not _CODEX_MODEL_IDENTIFIER.fullmatch(model):
            raise ValueError(
                "LOCAL_CODEX_SEARCH_MODEL must be a non-empty Codex model identifier."
            )
        return model

    @field_validator("local_codex_scheduled_discovery_capability", mode="before")
    @classmethod
    def _validate_local_codex_scheduled_capability(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().casefold()
        if normalized not in {"supported", "unsupported", "unverified"}:
            raise ValueError(
                "LOCAL_CODEX_SCHEDULED_DISCOVERY_CAPABILITY must be supported, unsupported, or unverified."
            )
        return normalized

    @property
    def openai_job_extraction_model(self) -> str:
        return self.job_extraction_model

    @property
    def openai_requirement_matching_model(self) -> str:
        return self.requirement_matching_model

    @property
    def openai_career_alignment_model(self) -> str:
        return self.career_alignment_model

    @property
    def openai_job_relevance_model(self) -> str:
        return self.job_relevance_model

    @property
    def openai_job_archetype_model(self) -> str:
        return self.job_archetype_model

    @property
    def openai_agentic_discovery_model(self) -> str:
        return self.agentic_discovery_model

    @property
    def effective_llm_base_url(self) -> str | None:
        if self.llm_base_url:
            return self.llm_base_url
        return self.ollama_base_url if self.default_llm_provider.casefold().strip() == "ollama" else None


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
