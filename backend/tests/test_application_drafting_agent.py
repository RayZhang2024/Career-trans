import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.agents.application_drafting import OpenAIApplicationDraftingAgent
from app.providers.llm import SemanticOutputError
from app.schemas.application_preparation import (
    ApplicationQuestionAnswerSet,
    CVWritingDraft,
    CoverLetterContent,
)


class _Responses:
    def __init__(self, output: str = "{}") -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output)


class _Client:
    def __init__(self, output: str = "{}") -> None:
        self.responses = _Responses(output)


def _context(**updates: object) -> dict[str, object]:
    context: dict[str, object] = {
        "sources": [
            {"source_type": "career_evidence", "source_ref": "E1", "text": "Python delivery"},
            {"source_type": "employment", "source_ref": "employment:0", "text": "Engineer"},
        ],
        "allowed_skills": ["Python", "SQL", "Azure"],
        "employment": [{"employment_index": 0}, {"employment_index": 2}],
        "projects": [{"project_index": 1}, {"project_index": 3}],
        "questions": ["Question one?", "Question two?"],
    }
    context.update(updates)
    return context


def _agent() -> OpenAIApplicationDraftingAgent:
    client = _Client()
    return OpenAIApplicationDraftingAgent(
        cv_client=client, cover_letter_client=client, answer_client=client, model="test-model"
    )


def _source_branches(schema: dict[str, object]) -> list[dict[str, object]]:
    return schema["$defs"]["ApplicationSourceRef"]["anyOf"]  # type: ignore[index]


def test_request_schema_permits_only_exact_source_pairs_for_every_output() -> None:
    agent = _agent()
    for output_type in (CVWritingDraft, CoverLetterContent, ApplicationQuestionAnswerSet):
        branches = _source_branches(agent._build_request_schema(output_type, _context(), "test"))
        assert len(branches) == 2
        pairs = {
            (
                branch["properties"]["source_type"]["enum"][0],
                branch["properties"]["source_ref"]["enum"][0],
            )
            for branch in branches
        }
        assert pairs == {("career_evidence", "E1"), ("employment", "employment:0")}
        assert ("career_evidence", "employment:0") not in pairs
        assert ("employment", "E1") not in pairs
        assert all(branch["additionalProperties"] is False for branch in branches)
        assert all(branch["required"] == ["source_type", "source_ref"] for branch in branches)


def test_cv_schema_constrains_skills_and_exposed_employment_project_indexes() -> None:
    schema = _agent()._build_request_schema(CVWritingDraft, _context(), "application_cv_drafting")
    assert schema["properties"]["key_skills"]["items"]["enum"] == ["Python", "SQL", "Azure"]  # type: ignore[index]
    assert "AWS" not in schema["properties"]["key_skills"]["items"]["enum"]  # type: ignore[index]
    assert schema["$defs"]["TailoredRoleDraft"]["properties"]["employment_index"]["enum"] == [0, 2]  # type: ignore[index]
    assert schema["$defs"]["TailoredProjectDraft"]["properties"]["project_index"]["enum"] == [1, 3]  # type: ignore[index]


def test_cv_schema_disables_unavailable_skills_roles_and_projects() -> None:
    schema = _agent()._build_request_schema(
        CVWritingDraft, _context(allowed_skills=[], employment=[], projects=[]), "application_cv_drafting"
    )
    assert schema["properties"]["key_skills"]["maxItems"] == 0  # type: ignore[index]
    assert schema["properties"]["role_drafts"]["maxItems"] == 0  # type: ignore[index]
    assert schema["properties"]["project_drafts"]["maxItems"] == 0  # type: ignore[index]


def test_answer_schema_constrains_exact_questions_and_cardinality() -> None:
    schema = _agent()._build_request_schema(
        ApplicationQuestionAnswerSet, _context(), "application_answer_drafting"
    )
    answers = schema["properties"]["answers"]  # type: ignore[index]
    assert answers["minItems"] == 2 and answers["maxItems"] == 2
    assert schema["$defs"]["ApplicationQuestionAnswer"]["properties"]["question"]["enum"] == ["Question one?", "Question two?"]  # type: ignore[index]


def test_empty_source_catalog_fails_before_provider_invocation() -> None:
    client = _Client()
    agent = OpenAIApplicationDraftingAgent(
        cv_client=client, cover_letter_client=client, answer_client=client, model="test-model"
    )
    with pytest.raises(SemanticOutputError, match="factual source"):
        agent.draft_cv(_context(sources=[]))
    assert client.responses.calls == []


def test_all_application_drafting_prompts_keep_refs_out_of_user_visible_prose() -> None:
    prompts = Path(__file__).resolve().parents[2] / "prompts"
    for name in (
        "application_cv_drafting.md",
        "application_cover_letter.md",
        "application_answer_drafting.md",
    ):
        text = (prompts / name).read_text(encoding="utf-8")
        assert "structured source_refs" in text
        assert "UUIDs" in text
