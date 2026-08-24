import io

from docx import Document
from sqlalchemy import select

from app.api.deps import get_cv_ingestion_service
from app.main import app
from app.models.candidate_cv_ingestion import CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.user import User
from app.schemas.cv_ingestion import CandidateCVData
from app.services.cv_file_extraction_service import CVFileExtractionService
from app.services.cv_ingestion_service import CVIngestionService, PersistedCandidateContextLoader
from app.services.cv_merge_service import CVMergeService


class FakeInterpreter:
    def __init__(self) -> None:
        self.calls = 0

    def interpret(self, documents):
        self.calls += 1
        return CandidateCVData.model_validate({"skills": [{"name": "Python"}]})


def _auth(client, email: str) -> dict[str, str]:
    credentials = {"email": email, "password": "strong-password"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    return {"Authorization": f"Bearer {client.post('/api/v1/auth/login', json=credentials).json()['access_token']}"}


def _json_cv() -> dict:
    return {
        "employment": [{"employer": "Example", "title": "Engineer", "description": "Built reliable systems."}],
        "skills": [{"name": "Python", "category": "technical"}],
        "projects": [{"name": "Platform", "description": "Delivered a service.", "skills": ["Python"]}],
        "achievements": [{"text": "Improved reliability."}],
        "evidence": [{"evidence_type": "employment", "title": "Engineer at Example", "text": "Built reliable systems.", "skills": ["Python"]}],
    }


def test_json_upload_import_is_deterministic_reviewable_and_requires_confirmation(client, db_session) -> None:
    interpreter = FakeInterpreter()
    service = CVIngestionService(db_session, interpreter=interpreter)
    app.dependency_overrides[get_cv_ingestion_service] = lambda: service
    try:
        headers = _auth(client, "cv-json@example.com")
        upload = client.post("/api/v1/cv-ingestion/upload", headers=headers, files=[("files", ("cv.json", __import__("json").dumps(_json_cv()), "application/json"))])
        assert upload.status_code == 201
        draft_id = upload.json()["id"]
        assert db_session.scalars(select(CandidateEvidenceRecord)).all() == []
        review = client.post(f"/api/v1/cv-ingestion/{draft_id}/interpret", headers=headers)
        assert review.status_code == 200
        assert review.json()["state"] == "review_ready"
        assert interpreter.calls == 0
        assert db_session.scalars(select(CandidateEvidenceRecord)).all() == []
        confirmed = client.post(f"/api/v1/cv-ingestion/{draft_id}/confirm", headers=headers)
        assert confirmed.status_code == 200
        assert confirmed.json()["confirmed_evidence_count"] == 1
        record = db_session.scalar(select(CandidateEvidenceRecord))
        assert record is not None and record.user_id
        assert db_session.scalar(select(CandidateStructuredProfile)) is not None
    finally:
        app.dependency_overrides.pop(get_cv_ingestion_service, None)


def test_cv_drafts_are_scoped_to_authenticated_user(client, db_session) -> None:
    service = CVIngestionService(db_session, interpreter=FakeInterpreter())
    app.dependency_overrides[get_cv_ingestion_service] = lambda: service
    try:
        first = _auth(client, "first-cv@example.com")
        second = _auth(client, "second-cv@example.com")
        draft_id = client.post("/api/v1/cv-ingestion/upload", headers=first, files=[("files", ("cv.md", "# CV\nPython engineer", "text/markdown"))]).json()["id"]
        assert client.get(f"/api/v1/cv-ingestion/{draft_id}", headers=second).status_code == 404
    finally:
        app.dependency_overrides.pop(get_cv_ingestion_service, None)


def test_confirmed_evidence_and_context_are_isolated_per_user(client, db_session) -> None:
    service = CVIngestionService(db_session, interpreter=FakeInterpreter())
    app.dependency_overrides[get_cv_ingestion_service] = lambda: service
    try:
        first_headers = _auth(client, "context-first@example.com")
        second_headers = _auth(client, "context-second@example.com")
        first_id = db_session.scalar(select(User.id).where(User.email == "context-first@example.com"))
        second_id = db_session.scalar(select(User.id).where(User.email == "context-second@example.com"))
        assert first_id and second_id
        draft = service.upload(first_id, [("cv.json", "application/json", __import__("json").dumps(_json_cv()).encode())])
        service.interpret(first_id, draft.id)
        service.confirm(first_id, draft.id)
        assert PersistedCandidateContextLoader(db_session).load(first_id).evidence
        assert PersistedCandidateContextLoader(db_session).load(second_id).evidence == []
        assert client.get(f"/api/v1/cv-ingestion/{draft.id}", headers=second_headers).status_code == 404
        assert first_headers
    finally:
        app.dependency_overrides.pop(get_cv_ingestion_service, None)


def test_markdown_uses_semantic_interpreter_and_context_uses_confirmed_data(db_session) -> None:
    interpreter = FakeInterpreter()
    service = CVIngestionService(db_session, interpreter=interpreter)
    draft = service.upload("user-1", [("cv.md", "text/markdown", b"Python engineer")])
    service.interpret("user-1", draft.id)
    assert interpreter.calls == 1
    service.confirm("user-1", draft.id)
    context = PersistedCandidateContextLoader(db_session).load("user-1")
    assert context.skills_text == "Python"
    assert context.career_strategy_text == ""


def test_docx_extraction_and_conservative_merge_preserve_distinct_evidence() -> None:
    document = Document()
    document.add_paragraph("Software engineer")
    buffer = io.BytesIO()
    document.save(buffer)
    extracted = CVFileExtractionService().extract(filename="cv.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", content=buffer.getvalue())
    assert extracted.segments[0].text == "Software engineer"
    merged = CVMergeService().merge([
        CandidateCVData.model_validate({"skills": [{"name": "Python"}], "evidence": [{"evidence_type": "project", "title": "A", "text": "Built A", "provenance": [{"document_sha256": "a", "segment_ids": ["a:1"]}]}]}),
        CandidateCVData.model_validate({"skills": [{"name": "python"}, {"name": "Rust"}], "evidence": [{"evidence_type": "project", "title": "A", "text": "Built A", "provenance": [{"document_sha256": "b", "segment_ids": ["b:1"]}]}, {"evidence_type": "project", "title": "B", "text": "Built B"}]}),
    ])
    assert [item.name for item in merged.skills] == ["Python", "Rust"]
    assert [item.title for item in merged.evidence] == ["A", "B"]
    assert {item.document_sha256 for item in merged.evidence[0].provenance} == {"a", "b"}


def test_file_validation_rejects_unreadable_or_non_schema_input() -> None:
    extractor = CVFileExtractionService()
    try:
        extractor.extract(filename="cv.exe", content_type="application/octet-stream", content=b"not a CV")
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("Unsupported files must be rejected")
    document = extractor.extract(filename="cv.json", content_type="application/json", content=b"[]")
    try:
        extractor.parsed_json(document)
    except ValueError as exc:
        assert "object" in str(exc)
    else:
        raise AssertionError("Non-object JSON must be rejected")
