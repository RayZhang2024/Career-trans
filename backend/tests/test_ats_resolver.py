from io import BytesIO
from urllib.error import HTTPError

from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.probes.ashby import AshbyJobSourceProbe
from app.providers.jobs.probes.base import derive_slug
from app.providers.jobs.probes.greenhouse import GreenhouseJobSourceProbe
from app.providers.jobs.probes.lever import LeverJobSourceProbe
from app.schemas.discovery import JobSearchQuery
from app.schemas.job_sources import CompanyTarget, ResolvedJobSource
from app.services.ats_resolver_service import AtsResolverService
from app.services.job_source_factory import create_job_source


def test_slug_derivation_is_deterministic() -> None:
    assert derive_slug("LangChain") == "langchain"
    assert derive_slug("Example AI Ltd") == "example-ai-ltd"


def test_unsafe_slug_is_rejected_before_a_probe_constructs_a_url() -> None:
    calls: list[str] = []
    probe = GreenhouseJobSourceProbe(fetch_json=lambda url: calls.append(url) or {"jobs": []})

    result = AtsResolverService([probe]).resolve(
        [CompanyTarget(name="Example", slug="../../private-host")]
    )

    assert calls == []
    assert result.results[0].attempted_providers == []
    assert result.results[0].error == "ATS slug contains unsafe characters."


def test_greenhouse_probe_resolves_valid_board() -> None:
    probe = GreenhouseJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": 1}]})

    result = probe.probe(CompanyTarget(name="Example"), "example")

    assert result == ResolvedJobSource(
        company="Example",
        provider="greenhouse",
        source_token="example",
        careers_url="https://job-boards.greenhouse.io/example",
    )


def test_ashby_probe_resolves_valid_board_and_factory_creates_source() -> None:
    probe = AshbyJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": "job-1"}]})

    resolved = probe.probe(CompanyTarget(name="Example"), "example")

    assert resolved is not None
    assert resolved.provider == "ashby"
    assert resolved.careers_url == "https://jobs.ashbyhq.com/example"
    assert isinstance(create_job_source(resolved), AshbyJobSource)


def test_resolved_sources_preserve_company_in_discovered_listings() -> None:
    query = JobSearchQuery(keywords=["Engineer"])
    cases = [
        (
            ResolvedJobSource(
                company="Example Greenhouse",
                provider="greenhouse",
                source_token="example-greenhouse",
                careers_url="https://job-boards.greenhouse.io/example-greenhouse",
            ),
            {
                "jobs": [
                    {
                        "id": 1,
                        "title": "Engineer",
                        "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
                    }
                ]
            },
        ),
        (
            ResolvedJobSource(
                company="Example Ashby",
                provider="ashby",
                source_token="example-ashby",
                careers_url="https://jobs.ashbyhq.com/example-ashby",
            ),
            {
                "jobs": [
                    {
                        "id": "1",
                        "title": "Engineer",
                        "jobUrl": "https://jobs.ashbyhq.com/example/job/1",
                    }
                ]
            },
        ),
        (
            ResolvedJobSource(
                company="Example Lever",
                provider="lever",
                source_token="example-lever",
                careers_url="https://jobs.lever.co/example-lever",
            ),
            [
                {
                    "id": "1",
                    "text": "Engineer",
                    "hostedUrl": "https://jobs.lever.co/example/1",
                }
            ],
        ),
    ]

    for resolved, payload in cases:
        source = create_job_source(resolved)
        setattr(source, "_fetch_json", lambda _: payload)

        listings = source.search(query)

        assert [listing.company for listing in listings] == [resolved.company]


def test_lever_probe_resolves_valid_board() -> None:
    probe = LeverJobSourceProbe(fetch_json=lambda _: [{"id": "job-1"}])

    result = probe.probe(CompanyTarget(name="Example"), "example")

    assert result is not None
    assert result.provider == "lever"
    assert result.careers_url == "https://jobs.lever.co/example"


def test_provider_not_found_falls_through_to_next_provider() -> None:
    def greenhouse_miss(_: str) -> dict[str, object]:
        raise HTTPError("https://example.test", 404, "Not Found", None, BytesIO())

    greenhouse = GreenhouseJobSourceProbe(fetch_json=greenhouse_miss)
    ashby = AshbyJobSourceProbe(fetch_json=lambda _: {"jobs": [{"id": "job-1"}]})

    result = AtsResolverService([greenhouse, ashby]).resolve([CompanyTarget(name="Example")])

    assert result.results[0].resolved is not None
    assert result.results[0].resolved.provider == "ashby"
    assert result.results[0].attempted_providers == ["greenhouse", "ashby"]


def test_first_success_wins_and_company_failures_do_not_abort_batch() -> None:
    class FirstProbe:
        name = "greenhouse"

        def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None:
            if company.name == "Broken":
                raise RuntimeError("unexpected")
            return ResolvedJobSource(
                company=company.name,
                provider=self.name,
                source_token=slug,
                careers_url=f"https://job-boards.greenhouse.io/{slug}",
            )

    class SecondProbe:
        name = "ashby"

        def probe(self, company: CompanyTarget, slug: str) -> ResolvedJobSource | None:
            if company.name == "Broken":
                return None
            return ResolvedJobSource(
                company=company.name,
                provider=self.name,
                source_token=slug,
                careers_url=f"https://jobs.ashbyhq.com/{slug}",
            )

    result = AtsResolverService([FirstProbe(), SecondProbe()]).resolve(
        [CompanyTarget(name="Broken"), CompanyTarget(name="Working")]
    )

    assert result.results[0].resolved is None
    assert result.results[0].attempted_providers == ["greenhouse", "ashby"]
    assert result.results[0].error == "greenhouse: probe failed"
    assert result.results[1].resolved is not None
    assert result.results[1].resolved.provider == "greenhouse"
    assert result.results[1].attempted_providers == ["greenhouse"]
