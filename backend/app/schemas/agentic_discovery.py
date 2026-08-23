from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate import CandidateContext
from app.schemas.discovery import JobListing, JobSearchQuery
from app.schemas.discovery_pipeline import DiscoveryLifecycleCounts


class SearchStrategy(BaseModel):
    """A bounded, auditable web-search instruction—not a claim that a job exists."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=400)
    archetype: str | None = None
    intent: str = Field(min_length=1, max_length=120)
    rationale: str | None = Field(default=None, max_length=280)
    priority: int = Field(default=0, ge=0, le=100)


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    snippet: str = ""
    url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    rank: int = Field(ge=1)


class PageContent(BaseModel):
    """Bounded public page evidence retained only during discovery execution."""

    model_config = ConfigDict(extra="forbid")

    requested_url: str = Field(min_length=1)
    final_url: str = Field(min_length=1)
    html: str = ""
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExtractedVacancy(BaseModel):
    """Supported facts extracted from one selected public page."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    posted_at: datetime | None = None
    employment_type: str | None = None
    work_arrangement: str | None = None


class AgenticDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_context: CandidateContext
    query: JobSearchQuery
    country: str = Field(default="gb", min_length=2, max_length=2)
    max_search_queries: int = Field(default=6, ge=1, le=100)
    max_search_results_per_query: int = Field(default=10, ge=1, le=50)
    max_pages_to_open: int = Field(default=12, ge=1, le=100)
    max_discovered_jobs: int = Field(default=20, ge=1, le=100)


class AgenticDiscoveryDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_strategies_generated: int = 0
    search_queries_executed: int = 0
    search_results_raw: int = 0
    search_results_unique: int = 0
    deterministic_filtered_count: int = 0
    pages_selected: int = 0
    pages_opened: int = 0
    page_fetch_failures: int = 0
    extraction_successes: int = 0
    extraction_failures: int = 0
    normalized_jobs: int = 0
    deduplicated_jobs: int = 0
    unique_employers: int = 0
    source_counts: dict[str, int] = Field(default_factory=dict)
    domain_counts: dict[str, int] = Field(default_factory=dict)
    search_errors: dict[str, str] = Field(default_factory=dict)
    page_errors: dict[str, str] = Field(default_factory=dict)


class AgenticDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategies: list[SearchStrategy] = Field(default_factory=list)
    listings: list[JobListing] = Field(default_factory=list)
    job_states: dict[str, str] = Field(default_factory=dict)
    lifecycle_counts: DiscoveryLifecycleCounts = Field(default_factory=DiscoveryLifecycleCounts)
    diagnostics: AgenticDiscoveryDiagnostics
