from app.schemas.discovery import JobListing, JobSearchQuery
from app.services.job_presemantic_selection_service import JobPresemanticSelectionService


def _job(
    *,
    title: str,
    company: str,
    url: str,
    location: str | None = "London, United Kingdom",
    work_arrangement: str | None = None,
    description: str | None = "Public vacancy detail.",
) -> JobListing:
    return JobListing(
        source="test",
        source_token=company.casefold().replace(" ", "-"),
        external_id=url.rsplit("/", 1)[-1],
        title=title,
        company=company,
        location=location,
        work_arrangement=work_arrangement,
        url=url,
        description=description,
    )


def _query(**overrides: object) -> JobSearchQuery:
    values: dict[str, object] = {
        "keywords": ["Forward Deployed Engineer", "Applied AI Engineer"],
        "locations": ["United Kingdom", "London"],
    }
    values.update(overrides)
    return JobSearchQuery.model_validate(values)


def test_presemantic_selection_is_independent_of_acquisition_order_and_prioritizes_query_titles() -> None:
    early_unrelated = [
        _job(
            title="Specialist Compliance Role",
            company=f"Early {index}",
            url=f"https://jobs.example.test/early/{index}",
        )
        for index in range(8)
    ]
    targets = [
        _job(title="Forward Deployed Engineer", company="Diligent", url="https://jobs.example.test/diligent/fde"),
        _job(title="Applied AI Engineer", company="Scale", url="https://jobs.example.test/scale/applied-ai"),
        _job(title="Forward Deployed AI Engineer", company="Smartsheet", url="https://jobs.example.test/smartsheet/fde"),
    ]
    selector = JobPresemanticSelectionService()

    first = selector.select_for_hunt(early_unrelated + targets, query=_query(), limit=3)
    second = selector.select_for_hunt(list(reversed(early_unrelated + targets)), query=_query(), limit=3)

    expected = {job.url for job in targets}
    assert {job.url for job in first.selected} == expected
    assert [job.url for job in first.selected] == [job.url for job in second.selected]
    assert len(first.outside_budget) == len(early_unrelated)


def test_presemantic_selection_keeps_adjacent_titles_eligible_without_a_hard_keyword_filter() -> None:
    direct = _job(title="Applied AI Engineer", company="Direct", url="https://jobs.example.test/direct")
    adjacent = _job(
        title="Client Deployment Specialist",
        company="Adjacent",
        url="https://jobs.example.test/adjacent",
        description="Applied AI delivery for customer teams.",
    )
    unrelated = _job(title="Zebra Operations Lead", company="Other", url="https://jobs.example.test/other")

    result = JobPresemanticSelectionService().select_for_hunt([unrelated, adjacent, direct], query=_query(), limit=2)

    assert [job.url for job in result.selected] == [direct.url, adjacent.url]
    assert adjacent in result.eligible


def test_search_theme_affinity_matches_complete_title_tokens_and_phrases() -> None:
    ai_engineer = _job(title="AI Engineer", company="AI", url="https://jobs.example.test/ai")
    paid_media = _job(title="Paid Media Manager", company="Paid", url="https://jobs.example.test/paid")
    fde = _job(title="Forward Deployed Engineer", company="FDE", url="https://jobs.example.test/fde")
    applied_ai = _job(title="Applied AI Engineer", company="Applied", url="https://jobs.example.test/applied")

    assert JobPresemanticSelectionService.search_theme_affinity(ai_engineer, ["AI"]) >= 100
    assert JobPresemanticSelectionService.search_theme_affinity(paid_media, ["AI"]) == 0
    assert JobPresemanticSelectionService.search_theme_affinity(fde, ["Forward Deployed Engineer"]) >= 100
    assert JobPresemanticSelectionService.search_theme_affinity(applied_ai, ["Applied AI Engineer"]) >= 100


def test_uk_location_rejects_us_only_remote_before_semantic_selection() -> None:
    us_remote = _job(
        title="Forward Deployed Engineer",
        company="US Remote",
        url="https://jobs.example.test/us-remote",
        location="Remote-Friendly, United States; San Francisco, CA; New York City, NY",
        work_arrangement="Remote",
    )
    uk_remote = _job(
        title="Forward Deployed Engineer",
        company="UK Remote",
        url="https://jobs.example.test/uk-remote",
        location="Remote - United Kingdom",
        work_arrangement="Remote",
    )

    result = JobPresemanticSelectionService().select_for_hunt([us_remote, uk_remote], query=_query(remote_ok=True), limit=10)
    default_result = JobPresemanticSelectionService().select_for_hunt([us_remote, uk_remote], query=_query(), limit=10)

    assert result.selected == [uk_remote]
    assert result.geography_filtered == [us_remote]
    assert default_result.selected == [uk_remote]
    assert default_result.geography_filtered == [us_remote]


def test_geography_matching_uses_complete_location_tokens_and_phrases() -> None:
    selector = JobPresemanticSelectionService()
    ukraine = _job(title="Applied AI Engineer", company="Ukraine", url="https://jobs.example.test/ukraine", location="Kyiv, Ukraine")
    australia = _job(title="Applied AI Engineer", company="Australia", url="https://jobs.example.test/australia", location="Sydney, Australia")
    remote_uk = _job(title="Applied AI Engineer", company="Remote UK", url="https://jobs.example.test/remote-uk", location="Remote - UK")
    london_uk = _job(title="Applied AI Engineer", company="London UK", url="https://jobs.example.test/london-uk", location="London, United Kingdom")

    assert selector.select_for_hunt([ukraine], query=_query(locations=["UK"]), limit=1).selected == []
    assert selector.select_for_hunt([australia], query=_query(locations=["US"]), limit=1).selected == []
    assert selector.select_for_hunt([remote_uk], query=_query(locations=["United Kingdom"]), limit=1).selected == [remote_uk]
    assert selector.select_for_hunt([london_uk], query=_query(locations=["United Kingdom"]), limit=1).selected == [london_uk]
    assert selector.select_for_hunt([london_uk], query=_query(locations=["London"]), limit=1).selected == [london_uk]
    assert selector.select_for_hunt([remote_uk], query=_query(locations=["London"]), limit=1).selected == []


def test_reviewed_uk_geography_mapping_and_unknown_remote_only() -> None:
    selector = JobPresemanticSelectionService()
    locations = ["London", "Oxford", "Cambridge", "Bristol", "Manchester", "Reading", "England", "Scotland", "Wales", "Northern Ireland"]
    listings = [_job(title="AI Engineer", company=value, url=f"https://jobs.example.test/{i}", location=value) for i, value in enumerate(locations)]

    result = selector.select_for_hunt(listings, query=_query(locations=["UK"]), limit=20)

    assert {job.url for job in result.selected} == {job.url for job in listings}
    assert selector.select_for_hunt([_job(title="AI", company="Remote", url="https://jobs.test/remote", location="Remote")], query=_query(locations=["UK"]), limit=1).unknown_geography
    assert selector.select_for_hunt([_job(title="AI", company="India", url="https://jobs.test/india", location="Bengaluru, India")], query=_query(locations=["UK"]), limit=1).incompatible_geography


def test_remote_policy_never_bypasses_geography_and_unknown_location_is_diagnostic() -> None:
    us_remote = _job(
        title="Applied AI Engineer",
        company="US Remote",
        url="https://jobs.example.test/us-only",
        location="Remote, United States",
        work_arrangement="Remote",
    )
    unknown = _job(
        title="Applied AI Engineer",
        company="Unknown",
        url="https://jobs.example.test/unknown",
        location=None,
    )
    selector = JobPresemanticSelectionService()

    constrained = selector.select_for_hunt([us_remote, unknown], query=_query(remote_ok=True), limit=10)
    unconstrained = selector.select_for_hunt([us_remote, unknown], query=_query(locations=[]), limit=10)

    assert constrained.selected == []
    assert constrained.geography_filtered == [us_remote, unknown]
    assert constrained.unknown_geography == [unknown]
    assert {job.url for job in unconstrained.selected} == {us_remote.url, unknown.url}


def test_remote_ok_false_remains_a_work_arrangement_constraint() -> None:
    uk_remote = _job(
        title="Applied AI Engineer",
        company="UK Remote",
        url="https://jobs.example.test/uk-remote",
        location="United Kingdom",
        work_arrangement="Remote",
    )

    result = JobPresemanticSelectionService().select_for_hunt([uk_remote], query=_query(remote_ok=False), limit=10)

    assert result.selected == []
    assert result.geography_filtered == []
    assert result.work_arrangement_filtered == [uk_remote]
