import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.candidate_cv_ingestion import CandidateEvidenceRecord
from app.schemas.cv_ingestion import CandidateCVData, CareerEvidenceDraft, EvidenceProvenance
from app.schemas.job import JobProfile, JobRequirement
from app.services.candidate_profile_compaction import top_evidence
from app.services.canonical_candidate_read_service import CanonicalCandidateReadService
from app.services.cv_ingestion_service import CVIngestionService


class _DetailedInterpreter:
    def interpret(self, documents):
        document = documents[0]
        segments = document.segments

        def evidence(
            title: str,
            text: str,
            skills: list[str],
            segment_index: int,
        ) -> CareerEvidenceDraft:
            return CareerEvidenceDraft(
                evidence_type="project",
                title=title,
                text=text,
                skills=skills,
                provenance=[
                    EvidenceProvenance(
                        document_sha256=document.provenance.document_sha256,
                        segment_ids=[segments[segment_index].segment_id],
                    )
                ],
            )

        return CandidateCVData(
            evidence=[
                evidence(
                    "AI platform delivery",
                    "Implemented an OpenAI API-backed FastAPI service with system prompts, "
                    "evaluation, pytest, Git/GitHub, and CI/CD.",
                    ["OpenAI API", "FastAPI", "system prompts", "evaluation", "pytest", "Git", "GitHub", "CI/CD"],
                    0,
                ),
                evidence(
                    "Retrieval implementation",
                    "Built RAG using embeddings and vector retrieval over structured and "
                    "unstructured document data.",
                    ["RAG", "embeddings", "vector retrieval", "structured data", "unstructured data"],
                    1,
                ),
                evidence(
                    "Container deployment",
                    "Containerized and deployed the service with Docker.",
                    ["Docker", "containers", "deployment"],
                    2,
                ),
                evidence(
                    "Cloud delivery",
                    "Delivered a GCP Vertex AI service with IAM and cost monitoring.",
                    ["GCP", "Vertex AI", "IAM", "cost monitoring"],
                    3,
                ),
            ]
        )


class _BroadOnlyInterpreter:
    def interpret(self, documents):
        document = documents[0]
        return CandidateCVData(
            evidence=[
                CareerEvidenceDraft(
                    evidence_type="project",
                    title="Prototype retrieval work",
                    text="Built a LangGraph RAG prototype.",
                    skills=["LangGraph", "RAG"],
                    provenance=[
                        EvidenceProvenance(
                            document_sha256=document.provenance.document_sha256,
                            segment_ids=[document.segments[0].segment_id],
                        )
                    ],
                ),
                CareerEvidenceDraft(
                    evidence_type="project",
                    title="Generic cloud work",
                    text="Deployed a service to cloud infrastructure.",
                    skills=["cloud deployment"],
                    provenance=[
                        EvidenceProvenance(
                            document_sha256=document.provenance.document_sha256,
                            segment_ids=[document.segments[1].segment_id],
                        )
                    ],
                ),
            ]
        )


_DETAILED_CV = b"""# AI platform delivery
Implemented an OpenAI API-backed FastAPI service with system prompts, evaluation, pytest, Git/GitHub, and CI/CD.
# Retrieval implementation
Built RAG using embeddings and vector retrieval over structured and unstructured document data.
# Container deployment
Containerized and deployed the service with Docker.
# Cloud delivery
Delivered a GCP Vertex AI service with IAM and cost monitoring.
"""


def test_detailed_source_grounded_evidence_survives_confirmation_and_bounded_retrieval(
    db_session,
    runtime_snapshot_a,
) -> None:
    service = CVIngestionService(db_session, interpreter=_DetailedInterpreter(), runtime_snapshot=runtime_snapshot_a)
    draft = service.upload("user-1", [("cv.md", "text/markdown", _DETAILED_CV)])

    reviewed = service.interpret("user-1", draft.id)
    assert reviewed.merged is not None
    assert [item.title for item in reviewed.merged.evidence] == [
        "AI platform delivery",
        "Retrieval implementation",
        "Container deployment",
        "Cloud delivery",
    ]
    assert all(item.provenance and item.provenance[0].segment_ids for item in reviewed.merged.evidence)

    service.confirm("user-1", draft.id)
    persisted = list(
        db_session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == "user-1")
        )
    )
    assert len(persisted) == 4
    assert all(json.loads(item.provenance_json)[0]["segment_ids"] for item in persisted)

    reader = CanonicalCandidateReadService(db_session)
    context = reader.candidate_context(reader.read("user-1"), require_complete_evidence=True)
    assert context is not None
    selected = top_evidence(
        context.evidence,
        JobProfile(
            requirements=[
                JobRequirement(text="OpenAI API system prompt evaluation"),
                JobRequirement(text="RAG embeddings vector retrieval"),
                JobRequirement(text="Docker container deployment"),
                JobRequirement(text="GCP Vertex AI IAM cost monitoring"),
            ]
        ),
    )

    assert {item.title for item in selected} == {
        "AI platform delivery",
        "Retrieval implementation",
        "Container deployment",
        "Cloud delivery",
    }


def test_broad_source_wording_does_not_gain_unsupported_implementation_facts(db_session, runtime_snapshot_a) -> None:
    service = CVIngestionService(db_session, interpreter=_BroadOnlyInterpreter(), runtime_snapshot=runtime_snapshot_a)
    draft = service.upload(
        "user-1",
        [
            (
                "cv.md",
                "text/markdown",
                b"# Retrieval\nBuilt a LangGraph RAG prototype.\n# Cloud\nDeployed a service to cloud infrastructure.",
            )
        ],
    )

    reviewed = service.interpret("user-1", draft.id)
    assert reviewed.merged is not None
    rendered = " ".join(
        " ".join([item.title, item.text, *item.skills])
        for item in reviewed.merged.evidence
    ).casefold()

    assert "openai" not in rendered
    assert "anthropic" not in rendered
    assert "vector" not in rendered
    assert "gcp" not in rendered
    assert "iam" not in rendered


def test_invalid_evidence_segment_provenance_is_rejected(db_session, runtime_snapshot_a) -> None:
    class _InvalidProvenanceInterpreter:
        def interpret(self, documents):
            document = documents[0]
            return CandidateCVData(
                evidence=[
                    CareerEvidenceDraft(
                        evidence_type="project",
                        title="Unsupported provenance",
                        text="Built a service.",
                        provenance=[
                            EvidenceProvenance(
                                document_sha256=document.provenance.document_sha256,
                                segment_ids=["not-a-source-segment"],
                            )
                        ],
                    )
                ]
            )

    service = CVIngestionService(db_session, interpreter=_InvalidProvenanceInterpreter(), runtime_snapshot=runtime_snapshot_a)
    draft = service.upload("user-1", [("cv.md", "text/markdown", b"# Project\nBuilt a service.")])

    with pytest.raises(ValueError, match="supplied source segments"):
        service.interpret("user-1", draft.id)


def test_semantic_evidence_without_provenance_is_rejected_without_fallback(db_session, runtime_snapshot_a) -> None:
    class _MissingProvenanceInterpreter:
        def interpret(self, _documents):
            return CandidateCVData(
                evidence=[
                    CareerEvidenceDraft(
                        evidence_type="project",
                        title="Ungrounded technical claim",
                        text="Implemented an API service.",
                        skills=["API"],
                        provenance=[],
                    )
                ]
            )

    service = CVIngestionService(db_session, interpreter=_MissingProvenanceInterpreter(), runtime_snapshot=runtime_snapshot_a)
    draft = service.upload(
        "user-1",
        [("cv.md", "text/markdown", b"# Project\nImplemented an API service.")],
    )

    with pytest.raises(ValueError, match="Semantic CV evidence must identify"):
        service.interpret("user-1", draft.id)

    assert service.read("user-1", draft.id).state == "uploaded"
    assert db_session.scalars(select(CandidateEvidenceRecord)).all() == []


def test_semantic_evidence_can_preserve_multiple_valid_source_segments(db_session, runtime_snapshot_a) -> None:
    class _MultiSegmentInterpreter:
        def interpret(self, documents):
            document = documents[0]
            return CandidateCVData(
                evidence=[
                    CareerEvidenceDraft(
                        evidence_type="project",
                        title="Service delivery",
                        text="Built a service and tested it with pytest.",
                        skills=["pytest"],
                        provenance=[
                            EvidenceProvenance(
                                document_sha256=document.provenance.document_sha256,
                                segment_ids=[
                                    document.segments[0].segment_id,
                                    document.segments[1].segment_id,
                                ],
                            )
                        ],
                    )
                ]
            )

    service = CVIngestionService(db_session, interpreter=_MultiSegmentInterpreter(), runtime_snapshot=runtime_snapshot_a)
    draft = service.upload(
        "user-1",
        [
            (
                "cv.md",
                "text/markdown",
                b"# Build\nBuilt a service.\n# Test\nTested it with pytest.",
            )
        ],
    )

    reviewed = service.interpret("user-1", draft.id)

    assert reviewed.merged is not None
    assert len(reviewed.merged.evidence[0].provenance[0].segment_ids) == 2


def test_technical_evidence_prompt_requires_source_grounded_detail() -> None:
    prompt = (Path(__file__).resolve().parents[2] / "prompts" / "cv_evidence_extraction.md").read_text(
        encoding="utf-8"
    )

    assert "concrete implementation facts" in prompt
    assert "specific source segment" in prompt
    assert "do not infer OpenAI API use from LangGraph" in prompt
    assert "vector databases from RAG" in prompt
