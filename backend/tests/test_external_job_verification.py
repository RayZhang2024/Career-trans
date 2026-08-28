import json

from sqlalchemy import select

from app.models.discovered_job import DiscoveredJob
from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.workday import WorkdayJobDetailExtractor
from app.schemas.agentic_discovery import PageContent
from app.schemas.discovery import JobListing, JobProvenance, JobVerificationStatus
from app.schemas.external_discovery import ExternalDiscoveredJob, ExternalDiscoveryImportRequest
from app.schemas.job_ranking import JobRankingRequest
from app.schemas.job import JobProfile, JobRequirement
from app.schemas.assessment import FitAssessment
from app.schemas.career_assessment import AlignmentConfidence, CareerAssessment
from app.schemas.recommendation import Recommendation, RecommendationAssessment
from app.services.discovered_job_state_store import SqlAlchemyDiscoveredJobStateStore
from app.services.external_discovery_import_service import ExternalDiscoveryImportService
from app.services.external_job_verification_service import ExternalJobVerificationService
from app.services.job_ranking_service import JobRankingService


DETAIL = " ".join(
    [
        "Required qualifications include Python software engineering customer delivery cloud systems stakeholder communication and production deployment.",
    ]
    * 8
)


def _lead(url: str, *, description: str = "Short discovery summary.") -> JobListing:
    return JobListing(
        source="agent_runtime",
        source_token="codex",
        title="Forward Deployed Engineer",
        company="Example Systems",
        location="London",
        url=url,
        description=description,
        provenance=JobProvenance(runtime="codex", source_ref="search-result", discovered_via="web"),
    )


def _greenhouse_job(job_id: str = "123") -> dict[str, object]:
    return {
        "id": job_id,
        "title": "Forward Deployed Engineer",
        "absolute_url": f"https://job-boards.greenhouse.io/example/jobs/{job_id}?gh_src=tracking",
        "content": DETAIL,
        "location": {"name": "London"},
    }


def _lever_job(job_id: str = "abc") -> dict[str, object]:
    return {
        "id": job_id,
        "text": "Forward Deployed Engineer",
        "hostedUrl": f"https://jobs.lever.co/example/{job_id}",
        "descriptionPlain": DETAIL,
        "categories": {"location": "London", "commitment": "Full time"},
    }


def _ashby_job(job_id: str = "uuid-1") -> dict[str, object]:
    return {
        "id": job_id,
        "title": "Forward Deployed Engineer",
        "jobUrl": f"https://jobs.ashbyhq.com/example/{job_id}",
        "descriptionHtml": DETAIL,
        "location": "London",
    }


def _verifier(payloads: dict[str, object]) -> ExternalJobVerificationService:
    return ExternalJobVerificationService(fetch_json=lambda url: payloads[url])


def test_greenhouse_lever_and_ashby_urls_resolve_current_identity_detail_and_canonical_url() -> None:
    greenhouse_lead = _lead("https://job-boards.greenhouse.io/example/jobs/123?utm_source=search")
    lever_lead = _lead("https://jobs.lever.co/example/abc?tracking=search")
    ashby_lead = _lead("https://jobs.ashbyhq.com/example/uuid-1?source=search")
    verifier = _verifier(
        {
            GreenhouseJobSource.jobs_url("example"): {"jobs": [_greenhouse_job()]},
            LeverJobSource.jobs_url("example"): [_lever_job()],
            AshbyJobSource.jobs_url("example"): {"jobs": [_ashby_job()]},
        }
    )

    greenhouse, lever, ashby = [verifier.verify(lead) for lead in (greenhouse_lead, lever_lead, ashby_lead)]

    assert all(result.actionable for result in (greenhouse, lever, ashby))
    assert (greenhouse.listing.source, greenhouse.listing.external_id) == ("greenhouse", "123")
    assert (lever.listing.source, lever.listing.external_id) == ("lever", "abc")
    assert (ashby.listing.source, ashby.listing.external_id) == ("ashby", "uuid-1")
    assert all(result.listing.verification_status is JobVerificationStatus.VERIFIED for result in (greenhouse, lever, ashby))
    assert greenhouse.listing.url == "https://job-boards.greenhouse.io/example/jobs/123"
    assert lever.listing.url == "https://jobs.lever.co/example/abc"
    assert ashby.listing.url == "https://jobs.ashbyhq.com/example/uuid-1"
    assert all(result.listing.description == DETAIL for result in (greenhouse, lever, ashby))


def test_missing_ashby_uuid_is_unverified_and_keeps_bounded_lead_provenance() -> None:
    lead = _lead("https://jobs.ashbyhq.com/example/missing-id")
    result = _verifier({AshbyJobSource.jobs_url("example"): {"jobs": []}}).verify(lead)

    assert result.actionable is False
    assert result.reason == "provider_vacancy_not_current"
    assert result.listing.verification_status is JobVerificationStatus.UNVERIFIED
    assert result.listing.provenance == lead.provenance


class _Pages:
    def __init__(self, pages: dict[str, PageContent]) -> None:
        self.pages = pages

    def fetch(self, url: str) -> PageContent:
        return self.pages[url]


def test_recoverable_workday_uses_existing_structured_detail_path() -> None:
    url = "https://example.wd12.myworkdayjobs.com/en-US/ExternalCareerSite/job/London/Forward-Deployed-Engineer_R-123?tracking=search"
    canonical = url.split("?", 1)[0]
    detail_url = "https://example.wd12.myworkdayjobs.com/wday/cxs/example/ExternalCareerSite/job/London/Forward-Deployed-Engineer_R-123"
    shell = PageContent(
        requested_url=url,
        final_url=canonical,
        html='<script>window.workday = { tenant: "example", siteId: "ExternalCareerSite" };</script>',
    )
    detail = PageContent(
        requested_url=detail_url,
        final_url=detail_url,
        html=json.dumps(
            {
                "jobPostingInfo": {"title": "Forward Deployed Engineer", "location": "London", "jobDescription": f"<p>{DETAIL}</p>"},
                "hiringOrganization": {"name": "Example Systems"},
            }
        ),
    )
    pages = _Pages({url: shell, detail_url: detail})
    result = ExternalJobVerificationService(
        page_fetcher=pages,
        workday_detail_extractor=WorkdayJobDetailExtractor(pages),
    ).verify(_lead(url))

    assert result.actionable is True
    assert (result.listing.source, result.listing.source_token, result.listing.external_id) == ("workday", "example", "R-123")
    assert result.listing.description == DETAIL


def test_workday_detail_failure_is_unverified_not_inactive() -> None:
    url = "https://example.wd12.myworkdayjobs.com/en-US/ExternalCareerSite/job/London/Forward-Deployed-Engineer_R-123"
    result = ExternalJobVerificationService().verify(_lead(url))

    assert result.actionable is False
    assert result.reason == "provider_detail_unavailable"
    assert result.listing.verification_status is JobVerificationStatus.UNVERIFIED


def test_verified_detail_persists_as_actionable_and_short_later_summary_cannot_overwrite_it(db_session) -> None:
    url = "https://job-boards.greenhouse.io/example/jobs/123?tracking=search"
    verifier = _verifier({GreenhouseJobSource.jobs_url("example"): {"jobs": [_greenhouse_job()]}})
    service = ExternalDiscoveryImportService(
        session=db_session,
        state_store=SqlAlchemyDiscoveredJobStateStore(db_session),
        verifier=verifier,
    )
    request = ExternalDiscoveryImportRequest(
        runtime="codex",
        jobs=[ExternalDiscoveredJob(title="Forward Deployed Engineer", company="Example Systems", location="London", url=url, description="Short summary", provenance={"source_ref": "search-result", "discovered_via": "web"})],
        query={"keywords": ["Engineer"]},
    )

    first = service.import_jobs(request)
    second = service.import_jobs(request.model_copy(update={"jobs": [request.jobs[0].model_copy(update={"description": "Later short summary"})]}))
    record = db_session.scalar(select(DiscoveredJob))

    assert len(first.accepted_jobs) == 1
    assert first.unverified_leads == []
    assert second.lifecycle_counts.unchanged == 1
    assert record is not None
    assert record.description == DETAIL
    assert record.source == "greenhouse"
    assert record.external_id == "123"
    assert record.verification_status == "verified"


def test_verification_promotes_existing_url_lead_to_provider_identity_without_duplication(db_session) -> None:
    canonical_url = "https://job-boards.greenhouse.io/example/jobs/123"
    state_store = SqlAlchemyDiscoveredJobStateStore(db_session)
    state_store.persist([_lead(canonical_url)])
    verifier = _verifier({GreenhouseJobSource.jobs_url("example"): {"jobs": [_greenhouse_job()]}})
    service = ExternalDiscoveryImportService(session=db_session, state_store=state_store, verifier=verifier)
    response = service.import_jobs(
        ExternalDiscoveryImportRequest(
            runtime="codex",
            jobs=[ExternalDiscoveredJob(title="Forward Deployed Engineer", company="Example Systems", location="London", url=canonical_url, description="Short summary")],
            query={"keywords": ["Engineer"]},
        )
    )

    records = db_session.scalars(select(DiscoveredJob)).all()
    assert len(response.accepted_jobs) == 1
    assert len(records) == 1
    assert records[0].identity_key == "external:greenhouse:example:123"
    assert records[0].description == DETAIL


def test_unverified_lead_is_persisted_but_never_reaches_semantic_agents() -> None:
    class _NeverCalled:
        def assess(self, *_args, **_kwargs):
            raise AssertionError("semantic screening must not run for an unverified lead")

    ranking = JobRankingService(
        relevance_agent=_NeverCalled(),
        archetype_agent=_NeverCalled(),
        career_analysis_graph=_NeverCalled(),
    )
    lead = _lead("https://jobs.ashbyhq.com/example/missing-id").model_copy(
        update={"verification_status": JobVerificationStatus.UNVERIFIED}
    )

    response = ranking.rank(JobRankingRequest(jobs=[lead], candidate_context={"profile_text": "candidate"}))

    assert response.gated_out_count == 1
    assert response.relevance_screened_count == 0
    assert response.finalist_count == 0
    assert response.analysed_count == 0


def test_verified_provider_description_reaches_deep_analysis_unchanged() -> None:
    verified = _verifier(
        {GreenhouseJobSource.jobs_url("example"): {"jobs": [_greenhouse_job()]}}
    ).verify(_lead("https://job-boards.greenhouse.io/example/jobs/123")).listing

    class _Relevant:
        def assess(self, *_args, **_kwargs):
            from app.schemas.job_ranking import JobRelevanceAssessment

            return JobRelevanceAssessment(relevant=True, score=0.9, reasoning="Relevant.")

    class _Archetype:
        def classify(self, *_args, **_kwargs):
            from app.schemas.job_ranking import JobArchetype, JobArchetypeAssessment

            return JobArchetypeAssessment(archetype=JobArchetype.AI_FORWARD_DEPLOYED, reasoning="Role.")

    class _Graph:
        def invoke(self, *, job_text, **_kwargs):
            assert job_text == DETAIL
            return {
                "job_profile": JobProfile(title="Forward Deployed Engineer", requirements=[JobRequirement(text="Python")]),
                "fit_assessment": FitAssessment(fit_score=70.0),
                "career_assessment": CareerAssessment(career_alignment_score=70.0, confidence=AlignmentConfidence.HIGH, dimensions=[], reasoning="Aligned."),
                "recommendation_assessment": RecommendationAssessment(recommendation=Recommendation.CONSIDER, fit_score=70.0, career_alignment_score=70.0, career_alignment_confidence=AlignmentConfidence.HIGH, rule_id="synthetic", reasoning="Consider."),
            }

    response = JobRankingService(
        relevance_agent=_Relevant(), archetype_agent=_Archetype(), career_analysis_graph=_Graph()
    ).rank(JobRankingRequest(jobs=[verified], candidate_context={"profile_text": "candidate"}))

    assert response.analysed_count == 1
    assert response.results[0].job.description == DETAIL
