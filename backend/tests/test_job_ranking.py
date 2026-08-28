import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.agents.career_alignment import OpenAICareerAlignmentAgent
from app.agents.job_archetype import OpenAIJobArchetypeAgent
from app.agents.job_relevance import OpenAIJobRelevanceAgent
from app.agents.job_extraction import JobExtractionError
from app.schemas.assessment import FitAssessment
from app.schemas.candidate import CandidateContext, CareerEvidence
from app.schemas.career_assessment import (
    AlignmentConfidence,
    CareerAlignmentDimension,
    CareerAssessment,
)
from app.schemas.discovery import JobListing, JobProvenance
from app.schemas.job import JobProfile, JobRequirement, RequirementImportance
from app.schemas.job_ranking import (
    JobArchetype,
    JobArchetypeAssessment,
    JobRankingRequest,
    JobRelevanceAssessment,
    PostingLegitimacy,
)
from app.schemas.matching import MatchType, RequirementMatch
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

    def invoke(self, *, job_text: str, candidate_context: CandidateContext, **_: object) -> dict[str, object]:
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

    assert survivors == [(0, valid), (1, missing_description)]
    assert rejected == 1


def test_hard_gate_keeps_unusually_titled_job_for_semantic_relevance() -> None:
    unusual = job(
        "Technical Deployment Lead",
        description="Deploy AI systems with enterprise customers.",
        url="https://jobs.example.test/deployment-lead",
    )

    survivors, rejected = JobRankingGateService().gate([unusual])

    assert survivors == [(0, unusual)]
    assert rejected == 0


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


def test_openai_agents_send_compact_stage_profiles() -> None:
    class FakeClient:
        def __init__(self, output: dict[str, object]) -> None:
            self.calls: list[dict[str, object]] = []
            self.responses = SimpleNamespace(create=self.create)
            self._output = output

        def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(self._output))

    candidate = CandidateContext(
        profile_text="P" * 1_500,
        skills_text="Python, SQL",
        career_strategy_text="Build applied AI systems.",
        job_search_criteria_text="Hybrid technical roles.",
        evidence=[
            CareerEvidence(
                evidence_id="E1",
                title="Python delivery",
                text="Built Python systems.",
                skills=["Python"],
            )
        ],
    )
    listing = job("AI Solutions Engineer", description="Python AI delivery role")
    relevance_client = FakeClient(
        {"relevant": True, "score": 0.8, "reasoning": "Relevant."}
    )
    OpenAIJobRelevanceAgent(
        api_key="", model="test", client=relevance_client
    ).assess(listing, candidate)
    relevance_input = relevance_client.calls[0]["input"]
    relevance_payload = json.loads(relevance_input[1]["content"].split("INPUT:\n", 1)[1])

    assert set(relevance_payload["candidate"]) == {
        "profile_summary",
        "skills",
        "career_strategy_text",
        "job_search_criteria_text",
    }
    assert len(relevance_payload["candidate"]["profile_summary"]) == 1_200
    assert relevance_payload["candidate"]["skills"] == ["Python", "SQL"]

    career_client = FakeClient(
        {
            "confidence": "medium",
            "dimensions": [
                {
                    "dimension": dimension.value,
                    "score": 0.5,
                    "reasoning": "Synthetic.",
                }
                for dimension in CareerAlignmentDimension
            ],
            "strategic_strengths": [],
            "strategic_tradeoffs": [],
            "reasoning": "Synthetic.",
        }
    )
    OpenAICareerAlignmentAgent(
        api_key="", model="test", client=career_client
    ).assess(JobProfile(title=listing.title), candidate, FitAssessment(fit_score=70, essential_score=70))
    career_input = career_client.calls[0]["input"]
    career_payload = json.loads(career_input[1]["content"].split("INPUT:\n", 1)[1])

    assert set(career_payload["candidate_context"]) == {
        "profile_summary",
        "career_strategy_text",
        "job_search_criteria_text",
        "eligibility",
    }
    assert "evidence" not in career_payload["candidate_context"]


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
    assert [item.job.title for item in result.semantic_screening] == [
        "First",
        "Failed",
        "Apply",
        "Consider",
    ]
    assert all(item.relevance is not None for item in result.semantic_screening)


def test_semantic_cap_round_robins_across_companies() -> None:
    assessed: list[str] = []

    class FakeRelevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            assessed.append(listing.title)
            return JobRelevanceAssessment(relevant=False, score=0.0, reasoning="Not selected.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            raise AssertionError("Irrelevant jobs should not be classified.")

    jobs = [
        job("A1", url="https://jobs.example.test/a1").model_copy(update={"company": "First"}),
        job("A2", url="https://jobs.example.test/a2").model_copy(update={"company": "First"}),
        job("A3", url="https://jobs.example.test/a3").model_copy(update={"company": "First"}),
        job("B1", url="https://jobs.example.test/b1").model_copy(update={"company": "Later"}),
    ]
    service = JobRankingService(relevance_agent=FakeRelevance(), archetype_agent=FakeArchetype(), career_analysis_graph=FakeGraph({}))  # type: ignore[arg-type]

    result = service.rank(JobRankingRequest(jobs=jobs, candidate_context=CandidateContext(), max_semantic_candidates=2))

    assert assessed == ["A1", "B1"]
    assert result.relevance_screened_count == 2
    assert [item.job.title for item in result.semantic_screening] == ["A1", "B1"]


def test_semantic_cap_keeps_company_fairness_and_source_front_breadth() -> None:
    assessed: list[str] = []

    class FakeRelevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            assessed.append(listing.title)
            return JobRelevanceAssessment(relevant=False, score=0.0, reasoning="Not selected.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            raise AssertionError("Irrelevant jobs should not be classified.")

    first_front = JobProvenance(runtime="codex", discovered_via="applied-ai")
    adjacent_front = JobProvenance(runtime="codex", discovered_via="deployment")
    jobs = [
        job("A1", url="https://jobs.example.test/a1").model_copy(
            update={"company": "First", "provenance": first_front}
        ),
        job("A2", url="https://jobs.example.test/a2").model_copy(
            update={"company": "First", "provenance": first_front}
        ),
        job("A3", url="https://jobs.example.test/a3").model_copy(
            update={"company": "First", "provenance": adjacent_front}
        ),
        job("B1", url="https://jobs.example.test/b1").model_copy(
            update={"company": "Later", "provenance": first_front}
        ),
    ]
    service = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=FakeGraph({}),  # type: ignore[arg-type]
    )

    result = service.rank(
        JobRankingRequest(
            jobs=jobs,
            candidate_context=CandidateContext(),
            max_semantic_candidates=3,
        )
    )

    assert assessed == ["A1", "B1", "A3"]
    assert result.relevance_screened_count == 3


def test_career_analysis_failure_preserves_safe_root_exception_diagnostic() -> None:
    listing = job("Malformed extraction", description="Malformed", url="https://jobs.example.test/malformed")

    class FakeRelevance:
        def assess(self, _: JobListing, __: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(relevant=True, score=0.9, reasoning="Relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class FailingGraph:
        def invoke(self, **_: object) -> object:
            try:
                json.loads("{malformed")
            except json.JSONDecodeError as exc:
                raise JobExtractionError("Invalid structured job profile.") from exc

    result = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=FailingGraph(),  # type: ignore[arg-type]
    ).rank(
        JobRankingRequest(
            jobs=[listing], candidate_context=CandidateContext(), max_full_analyses=1
        )
    )

    assert result.analysed_count == 0
    assert result.failures[0].error == (
        "Career analysis failed: JobExtractionError (caused by JSONDecodeError)."
    )


def test_ranking_preserves_existing_graph_diagnostics_without_changing_scores() -> None:
    listing = job("Diagnosed", description="Diagnosed", url="https://jobs.example.test/diagnosed")
    requirement = JobRequirement(text="Python", importance=RequirementImportance.ESSENTIAL)
    match = RequirementMatch(
        requirement_index=0,
        requirement=requirement,
        match_type=MatchType.MISSING,
        score=0.0,
        evidence_ids=["evidence-1"],
        reasoning="No supported evidence.",
    )

    class FakeRelevance:
        def assess(self, _: JobListing, __: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(relevant=True, score=0.9, reasoning="Relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class DiagnosticGraph:
        def invoke(self, **_: object) -> dict[str, object]:
            fit = FitAssessment(fit_score=0.0, essential_score=0.0, hard_blockers=[0])
            career = CareerAssessment(career_alignment_score=50.0, confidence=AlignmentConfidence.MEDIUM, dimensions=[], reasoning="Neutral.")
            return {
                "job_profile": JobProfile(title="Diagnosed", requirements=[requirement]),
                "requirement_matches": [match],
                "fit_assessment": fit,
                "career_assessment": career,
                "recommendation_assessment": recommendation(Recommendation.SKIP, 0.0, 50.0),
            }

    result = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=DiagnosticGraph(),  # type: ignore[arg-type]
    ).rank(JobRankingRequest(jobs=[listing], candidate_context=CandidateContext()))

    opportunity = result.results[0]
    assert opportunity.fit_assessment.fit_score == 0.0
    assert opportunity.job_profile is not None
    assert opportunity.job_profile.requirements == [requirement]
    assert opportunity.requirement_matches == [match]


def test_missing_or_blank_description_is_unassessed_after_semantic_diagnostics() -> None:
    jobs = [
        job("Missing", description=None, url="https://jobs.example.test/missing"),
        job("Blank", description="  ", url="https://jobs.example.test/blank"),
    ]
    graph_calls = []

    class FakeRelevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(relevant=True, score=0.9, reasoning=f"{listing.title} is relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class FailingIfInvokedGraph:
        def invoke(self, **_: object) -> dict[str, object]:
            graph_calls.append(True)
            raise AssertionError("Incomplete descriptions must not enter deep analysis.")

    result = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=FailingIfInvokedGraph(),  # type: ignore[arg-type]
    ).rank(JobRankingRequest(jobs=jobs, candidate_context=CandidateContext(), max_full_analyses=2))

    assert result.results == []
    assert result.analysed_count == 0
    assert graph_calls == []
    assert [item.relevance.score for item in result.semantic_screening if item.relevance] == [0.9, 0.9]
    assert all(item.archetype is not None for item in result.semantic_screening)
    assert [failure.stage for failure in result.failures] == ["insufficient_job_detail", "insufficient_job_detail"]
    assert all("missing or blank job description" in failure.error for failure in result.failures)


def test_zero_requirement_extraction_is_unassessed_without_fit_assessment() -> None:
    listing = job("No requirements", description="Descriptive text", url="https://jobs.example.test/no-requirements")

    class FakeRelevance:
        def assess(self, _: JobListing, __: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(relevant=True, score=0.9, reasoning="Relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class EmptyExtractionGraph:
        def invoke(self, **_: object) -> dict[str, object]:
            return {"job_profile": JobProfile(title="No requirements", requirements=[])}

    result = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=EmptyExtractionGraph(),  # type: ignore[arg-type]
    ).rank(JobRankingRequest(jobs=[listing], candidate_context=CandidateContext()))

    assert result.results == []
    assert result.analysed_count == 0
    assert result.failures[0].stage == "insufficient_job_detail"
    assert "no extractable requirements" in result.failures[0].error


def test_zero_requirement_extractions_consume_the_deep_analysis_attempt_budget() -> None:
    listings = [
        job(
            f"Relevant {index}",
            description=f"Description {index}",
            url=f"https://jobs.example.test/relevant-{index}",
        )
        for index in range(3)
    ]
    graph_calls: list[str] = []

    class FakeRelevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(
                relevant=True,
                score=1.0 - (0.1 * int(listing.title.rsplit(" ", 1)[1])),
                reasoning="Relevant.",
            )

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(
                archetype=JobArchetype.OTHER,
                reasoning="Synthetic.",
            )

    class EmptyExtractionGraph:
        def invoke(self, *, job_text: str, **_: object) -> dict[str, object]:
            graph_calls.append(job_text)
            return {"job_profile": JobProfile(title=job_text, requirements=[])}

    result = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=EmptyExtractionGraph(),  # type: ignore[arg-type]
    ).rank(
        JobRankingRequest(
            jobs=listings,
            candidate_context=CandidateContext(),
            max_semantic_candidates=3,
            max_full_analyses=2,
        )
    )

    assert graph_calls == ["Description 0", "Description 1"]
    assert result.finalist_count == 2
    assert result.analysed_count == 0
    assert [failure.stage for failure in result.failures] == [
        "insufficient_job_detail",
        "insufficient_job_detail",
    ]


def test_incomplete_high_relevance_job_does_not_consume_deep_analysis_quota() -> None:
    incomplete = job("Incomplete", description=None, url="https://jobs.example.test/incomplete")
    complete = job("Complete", description="Complete role", url="https://jobs.example.test/complete")
    graph_calls: list[str] = []

    class FakeRelevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            score = 0.97 if listing.title == "Incomplete" else 0.90
            return JobRelevanceAssessment(relevant=True, score=score, reasoning="Relevant.")

    class FakeArchetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class CompleteOnlyGraph:
        def invoke(self, *, job_text: str, **_: object) -> dict[str, object]:
            graph_calls.append(job_text)
            assert job_text == "Complete role"
            fit = FitAssessment(fit_score=70.0, essential_score=70.0)
            career = CareerAssessment(career_alignment_score=70.0, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="Good.")
            return {
                "job_profile": JobProfile(title="Complete", requirements=[JobRequirement(text="Python")]),
                "requirement_matches": [],
                "fit_assessment": fit,
                "career_assessment": career,
                "recommendation_assessment": recommendation(Recommendation.CONSIDER, 70.0, 70.0),
            }

    result = JobRankingService(
        relevance_agent=FakeRelevance(),
        archetype_agent=FakeArchetype(),
        career_analysis_graph=CompleteOnlyGraph(),  # type: ignore[arg-type]
    ).rank(
        JobRankingRequest(
            jobs=[incomplete, complete],
            candidate_context=CandidateContext(),
            max_semantic_candidates=2,
            max_full_analyses=1,
        )
    )

    assert graph_calls == ["Complete role"]
    assert result.finalist_count == 1
    assert result.analysed_count == 1
    assert [item.job.title for item in result.results] == ["Complete"]
    assert [failure.stage for failure in result.failures] == ["insufficient_job_detail"]
    assert [item.job.title for item in result.semantic_screening] == ["Incomplete", "Complete"]
    assert all(item.archetype is not None for item in result.semantic_screening)


def test_ten_job_funnel_fixture_exercises_gate_screen_archetype_and_deep_caps() -> None:
    jobs = [
        job("Invalid URL 1", url="not-a-url"),
        job("Invalid URL 2", url="also-not-a-url"),
        job("Irrelevant 1", description="Adjacent role", url="https://jobs.example.test/i1"),
        job("Irrelevant 2", description="Adjacent role", url="https://jobs.example.test/i2"),
        job("Incomplete", description=None, url="https://jobs.example.test/incomplete"),
    ]
    jobs.extend(
        job(f"Relevant {index}", description=f"Complete role {index}", url=f"https://jobs.example.test/r{index}")
        for index in range(1, 6)
    )
    assessed: list[str] = []
    archetyped: list[str] = []
    analysed: list[str] = []

    class Relevance:
        def assess(self, listing: JobListing, _: CandidateContext) -> JobRelevanceAssessment:
            assessed.append(listing.title)
            relevant = not listing.title.startswith("Irrelevant")
            score = 0.95 - (0.02 * len(assessed)) if relevant else 0.1
            return JobRelevanceAssessment(relevant=relevant, score=score, reasoning="Synthetic.")

    class Archetype:
        def classify(self, listing: JobListing) -> JobArchetypeAssessment:
            archetyped.append(listing.title)
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class Graph:
        def invoke(self, *, job_text: str, **_: object) -> dict[str, object]:
            analysed.append(job_text)
            return {
                "job_profile": JobProfile(title=job_text, requirements=[JobRequirement(text="Python")]),
                "fit_assessment": FitAssessment(fit_score=70, essential_score=70),
                "career_assessment": CareerAssessment(
                    career_alignment_score=70,
                    confidence=AlignmentConfidence.HIGH,
                    dimensions=[],
                    reasoning="Synthetic.",
                ),
                "recommendation_assessment": recommendation(Recommendation.CONSIDER, 70, 70),
            }

    result = JobRankingService(
        relevance_agent=Relevance(),
        archetype_agent=Archetype(),
        career_analysis_graph=Graph(),  # type: ignore[arg-type]
    ).rank(
        JobRankingRequest(
            jobs=jobs,
            candidate_context=CandidateContext(),
            max_semantic_candidates=10,
            max_full_analyses=2,
        )
    )

    assert result.discovered_count == 10
    assert result.gated_out_count == 2
    assert result.relevance_screened_count == 8
    assert len(assessed) == 8
    assert [item.job.title for item in result.semantic_screening if item.archetype is not None] == archetyped
    assert len(archetyped) == 6
    assert analysed == ["Complete role 1", "Complete role 2"]
    assert result.finalist_count == 2
    assert result.analysed_count == 2
    assert any(failure.stage == "insufficient_job_detail" for failure in result.failures)


def test_ranking_passes_existing_listing_metadata_to_career_analysis_graph() -> None:
    listing = JobListing(
        source="agent_runtime",
        title="Known title",
        company="Known company",
        location="London",
        work_arrangement="Hybrid",
        employment_type="Permanent",
        url="https://jobs.example.test/known",
        description="Canonical description for semantic extraction.",
    )

    class Relevance:
        def assess(self, _: JobListing, __: CandidateContext) -> JobRelevanceAssessment:
            return JobRelevanceAssessment(relevant=True, score=0.9, reasoning="Relevant.")

    class Archetype:
        def classify(self, _: JobListing) -> JobArchetypeAssessment:
            return JobArchetypeAssessment(archetype=JobArchetype.OTHER, reasoning="Synthetic.")

    class RecordingGraph:
        def invoke(self, *, job_text: str, candidate_context: CandidateContext, job_listing: JobListing) -> dict[str, object]:
            assert job_text == "Canonical description for semantic extraction."
            assert job_listing is listing
            assert candidate_context == CandidateContext()
            fit = FitAssessment(fit_score=70.0, essential_score=70.0)
            career = CareerAssessment(career_alignment_score=70.0, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="Synthetic.")
            return {
                "job_profile": JobProfile(title="Known title", location="London", requirements=[JobRequirement(text="Python")]),
                "requirement_matches": [],
                "fit_assessment": fit,
                "career_assessment": career,
                "recommendation_assessment": recommendation(Recommendation.CONSIDER, 70.0, 70.0),
            }

    result = JobRankingService(
        relevance_agent=Relevance(),
        archetype_agent=Archetype(),
        career_analysis_graph=RecordingGraph(),  # type: ignore[arg-type]
    ).rank(JobRankingRequest(jobs=[listing], candidate_context=CandidateContext()))

    assert result.analysed_count == 1
    assert result.results[0].job_profile.location == "London"


def test_legitimacy_is_separate_from_assessment_scores() -> None:
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    recent = PostingLegitimacyService().assess(job("Recent", posted_at=now - timedelta(days=5)), now)
    old = PostingLegitimacyService().assess(job("Old", posted_at=now - timedelta(days=200)), now)

    assert recent.legitimacy is PostingLegitimacy.HIGH_CONFIDENCE
    assert old.legitimacy is PostingLegitimacy.PROCEED_WITH_CAUTION


def test_future_posting_date_is_unknown() -> None:
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)

    result = PostingLegitimacyService().assess(
        job("Future", posted_at=now + timedelta(days=1)),
        now,
    )

    assert result.legitimacy is PostingLegitimacy.UNKNOWN
