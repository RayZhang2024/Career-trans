from pydantic import BaseModel, ConfigDict


class LLMConfigurationRead(BaseModel):
    """Safe effective semantic configuration; intentionally excludes credential values."""

    model_config = ConfigDict(extra="forbid")

    default_llm_provider: str
    effective_llm_base_url: str | None
    ollama_base_url: str
    cv_semantic_extraction_model: str
    candidate_adviser_model: str
    application_drafting_model: str
    job_extraction_model: str
    requirement_matching_model: str
    career_alignment_model: str
    job_relevance_model: str
    job_archetype_model: str
    agentic_discovery_model: str
    agentic_search_provider: str
    openai_web_search_model: str
    openai_api_key_configured: bool
    requirement_matching_structured_output_capability: str


class LLMConfigurationCheck(LLMConfigurationRead):
    ready: bool
