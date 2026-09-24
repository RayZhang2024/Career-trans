import importlib
import json
import sys
from pathlib import Path


def test_studio_config_points_to_a_compiled_career_graph(
    monkeypatch,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    config = json.loads((backend_dir / "langgraph.json").read_text())

    assert config["graphs"]["career_analysis"] == (
        "./app/workflows/studio.py:graph"
    )
    assert config["env"] == ".env"

    monkeypatch.setenv("OPENAI_API_KEY", "studio-smoke-test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    sys.modules.pop("app.workflows.studio", None)
    try:
        studio = importlib.import_module("app.workflows.studio")

        assert {
            "extract_job",
            "match_requirements",
            "assess_fit",
            "assess_career_alignment",
            "build_recommendation",
        }.issubset(studio.graph.get_graph().nodes)
    finally:
        get_settings.cache_clear()
        sys.modules.pop("app.workflows.studio", None)
