import json
from datetime import datetime, timezone

import pytest

from app.providers.jobs.workday import WorkdayJobDetailExtractor
from app.schemas.agentic_discovery import PageContent


WORKDAY_URL = (
    "https://example.wd12.myworkdayjobs.com/en-US/ExternalCareerSite/"
    "job/London/Applied-AI-Engineer_R-123"
)
DETAIL_URL = (
    "https://example.wd12.myworkdayjobs.com/wday/cxs/example/ExternalCareerSite/"
    "job/London/Applied-AI-Engineer_R-123"
)


class FakeFetcher:
    def __init__(self, pages: dict[str, PageContent | Exception]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def fetch(self, url: str) -> PageContent:
        self.urls.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value


def _shell(*, url: str = WORKDAY_URL, tenant: str = "example", site: str = "ExternalCareerSite") -> PageContent:
    return PageContent(
        requested_url=url,
        final_url=url,
        html=f'<script>window.workday = {{ tenant: "{tenant}", siteId: "{site}" }};</script>',
    )


def _detail(payload: object) -> PageContent:
    return PageContent(requested_url=DETAIL_URL, final_url=DETAIL_URL, html=json.dumps(payload))


def test_workday_cxs_detail_recovers_source_grounded_vacancy_fields() -> None:
    fetcher = FakeFetcher(
        {
            DETAIL_URL: _detail(
                {
                    "hiringOrganization": {"name": "Example Systems"},
                    "jobPostingInfo": {
                        "title": "Applied AI Engineer",
                        "location": "London, United Kingdom",
                        "jobDescription": "<h2>What we're looking for</h2><p>Strong Python skills.</p><ul><li>Build reliable systems.</li></ul>",
                        "startDate": "2026-08-20",
                        "timeType": "Full time",
                        "remoteType": "HYBRID",
                    },
                }
            )
        }
    )

    vacancy = WorkdayJobDetailExtractor(fetcher).extract(_shell())

    assert fetcher.urls == [DETAIL_URL]
    assert vacancy is not None
    assert vacancy.title == "Applied AI Engineer"
    assert vacancy.company == "Example Systems"
    assert vacancy.location == "London, United Kingdom"
    assert vacancy.description == "What we're looking for\nStrong Python skills.\nBuild reliable systems."
    assert vacancy.posted_at == datetime(2026, 8, 20, tzinfo=timezone.utc)
    assert vacancy.employment_type == "Full time"
    assert vacancy.work_arrangement == "HYBRID"


@pytest.mark.parametrize(
    "page",
    [
        _shell(url="https://jobs.example.test/roles/1"),
        _shell(tenant="different"),
        PageContent(requested_url=WORKDAY_URL, final_url=WORKDAY_URL, html="<html>no Workday config</html>"),
    ],
)
def test_unsupported_or_untrusted_workday_shapes_do_not_fetch_derived_detail(page: PageContent) -> None:
    fetcher = FakeFetcher({})

    assert WorkdayJobDetailExtractor(fetcher).extract(page) is None
    assert fetcher.urls == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"jobPostingInfo": {}},
        {"jobPostingInfo": {"jobDescription": ""}},
        {"jobPostingInfo": {"jobDescription": "<p>Detail</p>"}},
        {"jobPostingInfo": {"jobDescription": "<p>Detail</p>"}, "hiringOrganization": []},
    ],
)
def test_malformed_or_partial_workday_payload_fails_safely_without_inventing_fields(payload: object) -> None:
    fetcher = FakeFetcher({DETAIL_URL: _detail(payload)})

    vacancy = WorkdayJobDetailExtractor(fetcher).extract(_shell())

    if payload == {"jobPostingInfo": {"jobDescription": "<p>Detail</p>"}}:
        assert vacancy is not None
        assert vacancy.title is None
        assert vacancy.company is None
        assert vacancy.location is None
        assert vacancy.posted_at is None
        assert vacancy.employment_type is None
        assert vacancy.work_arrangement is None
    elif payload == {"jobPostingInfo": {"jobDescription": "<p>Detail</p>"}, "hiringOrganization": []}:
        assert vacancy is not None
        assert vacancy.company is None
    else:
        assert vacancy is None


def test_workday_detail_fetch_failure_is_isolated() -> None:
    fetcher = FakeFetcher({DETAIL_URL: ValueError("public endpoint unavailable")})

    assert WorkdayJobDetailExtractor(fetcher).extract(_shell()) is None
