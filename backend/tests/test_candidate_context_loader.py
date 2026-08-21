from pathlib import Path

from app.services.candidate_context_loader import MarkdownCandidateContextLoader


def test_loader_parses_demo_candidate_evidence() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    profile_dir = repo_root / "resources" / "examples" / "ray_demo"

    context = MarkdownCandidateContextLoader().load(profile_dir)

    assert context.source_name == "ray_demo"
    assert context.profile_text
    assert context.skills_text
    assert context.career_strategy_text
    assert context.job_search_criteria_text

    evidence_by_id = {item.evidence_id: item for item in context.evidence}
    assert "ISIS-NEAT-001" in evidence_by_id
    assert "ISIS-IND-001" in evidence_by_id
    assert "Python" in evidence_by_id["ISIS-NEAT-001"].skills
