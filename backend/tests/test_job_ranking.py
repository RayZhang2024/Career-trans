import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.agents.job_archetype import OpenAIJobArchetypeAgent
from app.agents.job_relevance import OpenAIJobRelevanceAgent
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.discovery import JobListing
from app.schemas.job_ranking import (
    JobArchetype,
    JobArchetypeAssessment,
    JobRankingRequest,
    JobRelevanceAssessment,
    PostingLegitimacy,
)
from app.schemas.recommendation import Recommendation, RecommendationAssessment
from app.services.job_ranking_gate_service import JobRankingGateService
from app.services.job_ranking_service import JobRankingService
from app.services.posting_legitimacy_service import PostingLegitimacyService


def job(title: str, *, description: str | None = "Useful role description", url: str = "https://jobs.example.test/1", posted_at: datetime | None = None) -> JobListing:
    return JobListing(source="test", title=title, company="Example", url=url, description=description, posted_at=posted_at)


def recommendation(value: Recommendation, fit: float, alignment: float) -> RecommendationAssessment:
    return RecommendationAssessment(recommendation=value, fit_score=fit, career_alignment_score=alignment, career_alignment_confidence=AlignmentConfidence.HIGH, rule_id="synthetic", reasoning="Synthetic assessment.")


class FakeGraph:
    def __init__(self, values: dict[str, tuple[Recommendation, float, float]], failing_titles: set[str] | None = None) -> None:
        self._values = values
        self._failing_titles = failing_titles or set()

    def invoke(self, *, job_text: str, candidate_context: CandidateContext) -> dict[str, object]:
        if job_text in self._failing_titles:
            raise RuntimeError("synthetic failure")
        value, fit, alignment = self._values[job_text]
        return {
            "fit_assessment": FitAssessment(fit_score=fit, essential_score=fit),
            "career_assessment": CareerAssessment(career_alignment_score=alignment, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="Synthetic assessment."),
            "recommendation_assessment": recommendation(value, fit, alignment),
        }


def test_hard_gate_rejects_clear_invalid_jobs_and_preserves_ambiguity() -> None:
    valid = job("Valid", description="A role with enough text", url="https://jobs.example.test/valid")
    missing_description = job("Missing", description=None, url="https://jobs.example.test/missing")
    invalid_url = job("Invalid", url="not-a-url")

    survivors, rejected = JobRankingGateService().gate([valid, missing_description, invalid_url])

    assert survivors == [(0, valid)]
    assert rejected == 2


def test_openai_relevance_and_archetype_agents_validate_structured_output() -> None:
    class FakeClient:
        def __init__(self, output: dict[str, object]) -> None:
            self.responses = SimpleNamespace(create=lambda **_: SimpleNamespace(output_text=json.dumps(output)))

    candidate = CandidateContext(career_strategy_text="Build AI solutions.")
    listing = job("AI Solutions Engineer")
    relevance = OpenAIJobRelevanceAgent(api_key="", model="test", client=FakeClient({"relevant": True, "score": 0.8, "reasoning": "Relevant."})).assess(listing, candidate)
    archetype = OpenAIJobArchetypeAgent(api_key="", model="test", client=FakeClient({"archetype": "ai_solutions_architect", "reasoning": "Customer-facing solution role."})).classify(listing)

    assert relevance.score == 0.8
    assert archetype.archetype is JobArchetype.AI_SOLUTIONS_ARCHITECT


def test_ranking_caps_finalists_tolerates_failure_and_sorts_deterministically() -> None:
    first = job("First", description="First", url="https://jobs.example.test/first")
    failed = job("Failed", description="Failed", url="https://jobs.example.test/failed")
    apply = job("Apply", description="Apply", url="https://jobs.example.test/apply")
    consider = job("Consider", description="Consider", url="https://jobs.example.test/consider")
    relevance_scores = {"First": 0.95, "Failed": 0.9, "Apply": 0.85, "Consider": 0.8}

    class FakeRelevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(relevant=True, score=relevance_scores[listing.title], reasoning="Relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    graph = FakeGraph(
        {"First": (Recommendation.CONSIDER, 99, 99), "Apply": (Recommendation.APPLY, 70, 70), "Consider": (Recommendation.CONSIDER, 90, 80)},
        failing_titles={"Failed"},
    )
    service = JobRankingService(relevance_agent=FakeRelevance(), archetype_agent=FakeArchetype(), career_analysis_graph=graph)  # type: ignore[arg-type]
    result = service.rank(JobRankingRequest(jobs=[first, failed, apply, consider], candidate_context=CandidateContext(), max_semantic_candidates=4, max_full_analyses=4))

    assert result.finalist_count == 4
    assert result.analysed_count == 3
    assert [item.job.title for item in result.results] == ["Apply", "First", "Consider"]
    assert [item.rank for item in result.results] == [1, 2, 3]
    assert result.failures[0].stage == "career_analysis"


def test_legitimacy_is_separate_from_assessment_scores() -> None:
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    recent = PostingLegitimacyService().assess(job("Recent", posted_at=now - timedelta(days=5)), now)
    old = PostingLegitimacyService().assess(job("Old", posted_at=now - timedelta(days=200)), now)

    assert recent.legitimacy is PostingLegitimacy.HIGH_CONFIDENCE
    assert old.legitimacy is PostingLegitimacy.PROCEED_WITH_CAUTION
