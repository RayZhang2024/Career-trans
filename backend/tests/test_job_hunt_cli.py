from types import SimpleNamespace

from app import cli
from app.cli_http import CareerTransApiError


def _job(*, source: str, url: str, title: str = "Applied AI Engineer") -> dict:
    return {
        "source": source,
        "source_token": "source",
        "title": title,
        "company": "Example Systems",
        "location": "London",
        "url": url,
        "description": "Public vacancy detail.",
    }


def _ats(*items: tuple[dict, str]) -> dict:
    return {
        "source_diagnostics": [{"succeeded": True}],
        "raw_count": len(items),
        "lifecycle_counts": {
            "new": sum(state == "new" for _, state in items),
            "updated": sum(state == "updated" for _, state in items),
            "unchanged": sum(state == "unchanged" for _, state in items),
            "inactive": 0,
        },
        "lifecycle_jobs": [{"job": job, "state": state} for job, state in items],
    }


class _Client:
    def __init__(self, *_args) -> None:
        self.calls: list[tuple] = []
        self.ats = _ats()
        self.external: dict = {
            "accepted_jobs": [],
            "lifecycle_counts": {"new": 0, "updated": 0, "unchanged": 0, "inactive": 0},
            "lifecycle_jobs": [],
        }
        self.rank_error: Exception | None = None
        self.ranking: dict = {
            "discovered_count": 0,
            "finalist_count": 0,
            "analysed_count": 0,
            "results": [],
        }

    def discover_known_ats_sources(self, request):
        self.calls.append(("ats", request))
        return self.ats

    def get_external_discovery_search_context(self, query):
        self.calls.append(("context", query))
        return {"search_profile": {}, "query": query, "runtime_guidance": "factual only"}

    def import_discovered_jobs(self, **kwargs):
        self.calls.append(("import", kwargs))
        return self.external

    def rank_jobs_for_current_user(self, jobs, *, max_full_analyses=None):
        self.calls.append(("rank", jobs, max_full_analyses))
        if self.rank_error:
            raise self.rank_error
        return self.ranking | {
            "discovered_count": len(jobs),
            "finalist_count": self.ranking.get("finalist_count", 1),
            "analysed_count": self.ranking.get("analysed_count", 1),
        }


def _runner(monkeypatch, jobs: list[dict]) -> None:
    class Runner:
        def discover(self, _context):
            return [SimpleNamespace(model_dump=lambda mode: job) for job in jobs]

    monkeypatch.setattr(cli, "CodexExternalDiscoveryRunner", Runner)


def _run(
    monkeypatch,
    capsys,
    client: _Client,
    *,
    no_external: bool = False,
    locations: list[str] | None = None,
    max_rank: int | None = None,
    max_full_analyses: int | None = None,
) -> int:
    monkeypatch.setattr(cli, "CareerTransApiClient", lambda *_args: client)
    arguments = ["--token", "token", "jobs", "hunt", "--keyword", "AI Engineer"]
    for location in locations or []:
        arguments.extend(["--location", location])
    if max_rank is not None:
        arguments.extend(["--max-rank", str(max_rank)])
    if max_full_analyses is not None:
        arguments.extend(["--max-full-analyses", str(max_full_analyses)])
    if no_external:
        arguments.append("--no-external")
    return cli.main(arguments)


def test_hunt_reuses_ats_and_external_paths_and_ranks_only_new_updated(monkeypatch, capsys) -> None:
    client = _Client()
    ats_new = _job(source="greenhouse", url="https://jobs.example.test/ats")
    unchanged = _job(source="lever", url="https://jobs.example.test/unchanged")
    external_updated = _job(source="agent_runtime", url="https://jobs.example.test/external", title="Solutions Engineer")
    client.ats = _ats((ats_new, "new"), (unchanged, "unchanged"))
    client.external = {
        "accepted_jobs": [external_updated],
        "lifecycle_counts": {"new": 0, "updated": 1, "unchanged": 0, "inactive": 0},
        "lifecycle_jobs": [{"job": external_updated, "state": "updated"}],
    }
    _runner(monkeypatch, [external_updated])

    assert _run(monkeypatch, capsys, client) == 0
    ranked = next(call[1] for call in client.calls if call[0] == "rank")
    assert [job["url"] for job in ranked] == [ats_new["url"], external_updated["url"]]
    assert any(call[0] == "ats" for call in client.calls)
    assert any(call[0] == "context" for call in client.calls)
    assert any(call[0] == "import" for call in client.calls)


def test_hunt_default_keeps_semantic_budget_at_ten_and_deep_analysis_at_five(monkeypatch, capsys) -> None:
    client = _Client()
    jobs = [
        _job(
            source="greenhouse",
            url=f"https://jobs.example.test/{index}",
            title=f"Applied AI Engineer {index}",
        )
        for index in range(12)
    ]
    client.ats = _ats(*[(job, "new") for job in jobs])

    assert _run(monkeypatch, capsys, client, no_external=True) == 0

    rank_call = next(call for call in client.calls if call[0] == "rank")
    assert len(rank_call[1]) == 10
    assert rank_call[2] == 5


def test_hunt_propagates_explicit_deep_analysis_budget_without_exceeding_submission(monkeypatch, capsys) -> None:
    client = _Client()
    jobs = [
        _job(
            source="greenhouse",
            url=f"https://jobs.example.test/{index}",
            title=f"Applied AI Engineer {index}",
        )
        for index in range(3)
    ]
    client.ats = _ats(*[(job, "new") for job in jobs])

    assert _run(
        monkeypatch,
        capsys,
        client,
        no_external=True,
        max_rank=3,
        max_full_analyses=7,
    ) == 0

    rank_call = next(call for call in client.calls if call[0] == "rank")
    assert len(rank_call[1]) == 3
    assert rank_call[2] == 3


def test_hunt_default_renders_the_default_deep_analysis_shortlist(monkeypatch, capsys) -> None:
    client = _Client()
    jobs = [
        _job(
            source="greenhouse",
            url=f"https://jobs.example.test/{index}",
            title=f"Applied AI Engineer {index}",
        )
        for index in range(6)
    ]
    client.ats = _ats(*[(job, "new") for job in jobs])
    client.ranking = {
        "finalist_count": 6,
        "analysed_count": 6,
        "results": [
            {
                "rank": index,
                "job": {"title": f"Ranked role {index}", "company": "Example"},
                "recommendation_assessment": {"recommendation": "consider"},
            }
            for index in range(1, 7)
        ],
    }

    assert _run(monkeypatch, capsys, client, no_external=True) == 0
    output = capsys.readouterr().out

    assert "Ranked role 5" in output
    assert "Ranked role 6" not in output


def test_hunt_propagates_locations_to_ats_and_external_discovery(monkeypatch, capsys) -> None:
    client = _Client()
    job = _job(source="greenhouse", url="https://jobs.example.test/role")
    client.ats = _ats((job, "new"))
    _runner(monkeypatch, [])

    assert _run(monkeypatch, capsys, client, locations=["London", "Cambridge"]) == 0
    ats_request = next(call[1] for call in client.calls if call[0] == "ats")
    external_query = next(call[1] for call in client.calls if call[0] == "context")
    assert ats_request["locations"] == ["London", "Cambridge"]
    assert ats_request["keywords"] == ["AI Engineer"]
    assert external_query["locations"] == ["London", "Cambridge"]


def test_hunt_skips_semantic_ranking_when_no_actionable_jobs(monkeypatch, capsys) -> None:
    client = _Client()
    client.ats = _ats((_job(source="greenhouse", url="https://jobs.example.test/old"), "unchanged"))

    assert _run(monkeypatch, capsys, client, no_external=True) == 0
    assert not any(call[0] == "rank" for call in client.calls)
    assert "semantic ranking skipped" in capsys.readouterr().out


def test_hunt_deduplicates_strong_cross_channel_identity_before_ranking(monkeypatch, capsys) -> None:
    client = _Client()
    ats_job = _job(source="greenhouse", url="https://jobs.example.test/role?tracking=1")
    codex_job = _job(source="agent_runtime", url="https://jobs.example.test/role/")
    client.ats = _ats((ats_job, "new"))
    client.external = {
        "accepted_jobs": [codex_job],
        "lifecycle_counts": {"new": 1, "updated": 0, "unchanged": 0, "inactive": 0},
        "lifecycle_jobs": [{"job": codex_job, "state": "new"}],
    }
    _runner(monkeypatch, [codex_job])

    assert _run(monkeypatch, capsys, client) == 0
    ranked = next(call[1] for call in client.calls if call[0] == "rank")
    assert len(ranked) == 1
    assert "deduplicated=1" in capsys.readouterr().out


def test_hunt_isolates_codex_and_ranking_failures(monkeypatch, capsys) -> None:
    client = _Client()
    ats_job = _job(source="greenhouse", url="https://jobs.example.test/ats")
    client.ats = _ats((ats_job, "new"))

    class BrokenRunner:
        def discover(self, _context):
            raise cli.CodexExternalDiscoveryError("local runtime unavailable")

    monkeypatch.setattr(cli, "CodexExternalDiscoveryRunner", BrokenRunner)
    client.rank_error = CareerTransApiError(502, "ranking unavailable")

    assert _run(monkeypatch, capsys, client) == 2
    output = capsys.readouterr().out
    assert "Codex acquisition failed" in output
    assert "Ranking failed" in output
    assert any(call[0] == "rank" for call in client.calls)


def test_hunt_ats_failure_does_not_prevent_codex_acquisition(monkeypatch, capsys) -> None:
    client = _Client()
    external = _job(source="agent_runtime", url="https://jobs.example.test/codex")
    client.external = {
        "accepted_jobs": [external],
        "lifecycle_counts": {"new": 1, "updated": 0, "unchanged": 0, "inactive": 0},
        "lifecycle_jobs": [{"job": external, "state": "new"}],
    }

    def failed_ats(_request):
        raise CareerTransApiError(502, "ATS unavailable")

    client.discover_known_ats_sources = failed_ats
    _runner(monkeypatch, [external])

    assert _run(monkeypatch, capsys, client) == 0
    assert any(call[0] == "rank" for call in client.calls)
    assert "ATS acquisition failed" in capsys.readouterr().out


def test_hunt_aggregates_safe_ats_failure_kinds(monkeypatch, capsys) -> None:
    client = _Client()
    client.ats = {
        "source_diagnostics": [
            {"succeeded": False, "failure_kind": "connection_failure"},
            {"succeeded": False, "failure_kind": "connection_failure"},
            {"succeeded": False, "failure_kind": "timeout"},
            {"succeeded": True},
        ],
        "raw_count": 0,
        "lifecycle_counts": {"new": 0, "updated": 0, "unchanged": 0, "inactive": 0},
        "lifecycle_jobs": [],
    }

    assert _run(monkeypatch, capsys, client, no_external=True) == 0
    output = capsys.readouterr().out
    assert "failures=3 (connection_failure=2 timeout=1)." in output
    assert "secret" not in output.casefold()
