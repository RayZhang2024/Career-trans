from pathlib import Path

import app.cli as cli
from app.core.config import Settings
from app.schemas.external_discovery import ExternalDiscoveredJob


def test_broad_discovery_has_no_adzuna_or_paid_search_api_configuration() -> None:
    assert "adzuna_app_id" not in Settings.model_fields
    assert "adzuna_app_key" not in Settings.model_fields
    template = Path(".env.example").read_text(encoding="utf-8").casefold()
    assert "adzuna" not in template
    assert "adzuna_app" not in template


def test_cli_discover_broad_reuses_codex_runner_and_shared_import_without_ranking(monkeypatch, capsys) -> None:
    clients = []

    class _Client:
        def __init__(self, _base_url, _token) -> None:
            self.calls = []
            clients.append(self)

        def get_broad_discovery_context(self, query):
            self.calls.append(("broad_context", query))
            return {
                "search_profile": {"profile_summary": "Technical candidate", "skills": ["Python"]},
                "query": query,
                "runtime_guidance": "factual jobs only",
            }

        def import_discovered_jobs(self, **kwargs):
            self.calls.append(("import_discovered", kwargs))
            return {
                "accepted_jobs": kwargs["jobs"],
                "rejected_count": 0,
                "deduplicated_count": 0,
                "bounded_out_count": 0,
                "lifecycle_counts": {"new": 1, "updated": 0, "unchanged": 0, "inactive": 0},
            }

    class _Runner:
        def discover(self, context):
            assert context.query.keywords == ["AI Engineer"]
            return [
                ExternalDiscoveredJob(
                    title="Applied AI Engineer",
                    company="Example Labs",
                    location="London",
                    url="https://jobs.example.test/1",
                    provenance={"source_ref": "public-search", "discovered_via": "web"},
                )
            ]

    monkeypatch.setattr(cli, "CareerTransApiClient", _Client)
    monkeypatch.setattr(cli, "CodexExternalDiscoveryRunner", _Runner)
    assert cli.main(
        ["--token", "token", "jobs", "discover-broad", "--keyword", "AI Engineer", "--location", "London"]
    ) == 0

    assert clients[0].calls[0] == (
        "broad_context",
        {"keywords": ["AI Engineer"], "locations": ["London"], "max_results": 20},
    )
    assert clients[0].calls[1][0] == "import_discovered"
    assert clients[0].calls[1][1]["runtime"] == "codex"
    assert "Broad scan: raw=1 normalized=1" in capsys.readouterr().out
