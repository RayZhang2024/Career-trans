import json

import pytest
from docx import Document
from pypdf import PdfReader

from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.user import User
from app.schemas.application_preparation import (
    ApplicationPrepareRequest, ApplicationQuestionAnswerSet, ApplicationTargetInput, ApplicationSourceRef,
    CVWritingDraft, CoverLetterContent, TailoredBullet, TailoredRoleDraft,
)
from app.schemas.candidate import CandidateContext, CareerEvidence, CareerEvidenceProvenance
from app.schemas.cv_ingestion import CandidateCVData, Credential, Education, Employment, Skill
from app.schemas.job import JobProfile, JobRequirement, RequirementCategory, RequirementImportance
from app.schemas.matching import EvidenceSourceType, MatchType, RequirementMatch
from app.services.application_document_renderer import ApplicationDocumentRenderer
from app.services.application_preparation_service import ApplicationPreparationService
from app.services.cv_ingestion_service import PersistedCandidateContextLoader


class _Drafting:
    def __init__(self, *, bad_skill=False, derived_number=False) -> None:
        self.calls = 0; self.bad_skill = bad_skill; self.derived_number = derived_number
    def draft_cv(self, context):
        self.calls += 1
        text = "Led delivery from 2 hours to 20 minutes" if not self.derived_number else "Improved delivery by 83%"
        return CVWritingDraft(
            professional_summary=text,
            summary_source_refs=[ApplicationSourceRef(source_type=EvidenceSourceType.CAREER_EVIDENCE, source_ref="e1")],
            key_skills=["AWS" if self.bad_skill else "Python"],
            role_drafts=[TailoredRoleDraft(employment_index=0, bullets=[TailoredBullet(text=text, source_refs=[ApplicationSourceRef(source_type=EvidenceSourceType.CAREER_EVIDENCE, source_ref="e1")], priority=90)])],
        )
    def draft_cover_letter(self, context):
        self.calls += 1
        return CoverLetterContent(body="I led delivery from 2 hours to 20 minutes.", source_refs=[ApplicationSourceRef(source_type=EvidenceSourceType.CAREER_EVIDENCE, source_ref="e1")])
    def draft_answers(self, context):
        return ApplicationQuestionAnswerSet(answers=[{"question": item, "status": "unsupported", "answer": None, "source_refs": []} for item in context["questions"]])


class _Graph:
    def __init__(self): self.calls = 0
    def invoke(self, *, job_text, candidate_context, job_listing=None):
        self.calls += 1
        requirement = JobRequirement(text="Python delivery", category=RequirementCategory.TECHNICAL, importance=RequirementImportance.ESSENTIAL, source_text="Python delivery")
        return {"job_profile": JobProfile(title="Engineer", requirements=[requirement]), "requirement_matches": [RequirementMatch(requirement_index=0, requirement=requirement, match_type=MatchType.DEMONSTRATED, score=1, evidence_ids=["e1"], reasoning="safe")]}


class _Discovery:
    def is_currently_actionable(self, job): return True
    def current_evaluation_for_job(self, user_id, job): return None


def _data():
    return CandidateCVData(
        employment=[Employment(employer="Example", title="Engineer", start_date="2020", end_date="2024", location="London", description="Delivered systems")],
        education=[Education(institution="University", qualification="MSc")],
        credentials=[Credential(name="Certification", credential_type="certification", issued_date="2024")],
        skills=[Skill(name="Python")],
    )


def _context():
    return CandidateContext(skills_text="Python", evidence=[CareerEvidence(evidence_id="e1", title="Delivery", text="Led delivery from 2 hours to 20 minutes", skills=["Python"], provenance=[CareerEvidenceProvenance(document_sha256="a" * 64, segment_ids=["s1"])])])


def _service(db_session, monkeypatch, drafting=None):
    user = User(id="u1", email="account@example.test", password_hash="safe")
    profile = CandidateProfile(user_id="u1", display_name="Example Person", preferred_email="old@example.test", location="London")
    db_session.add_all([user, profile, CandidateStructuredProfile(user_id="u1", structured_json=_data().model_dump_json())]); db_session.commit()
    context = _context(); monkeypatch.setattr(PersistedCandidateContextLoader, "load_confirmed", lambda _self, _user: context)
    return ApplicationPreparationService(db_session, graph=_Graph(), drafting_agent=drafting or _Drafting(), user_discovery=_Discovery()), context


def test_raw_text_creates_immutable_preparation_and_documents(db_session, monkeypatch):
    service, _ = _service(db_session, monkeypatch)
    result = service.prepare("u1", ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Python delivery role " * 20)))
    assert result.target.source_kind == "job_text" and result.identity.email == "old@example.test" and result.result.actual_pdf_pages <= 2
    renderer = ApplicationDocumentRenderer(); docx = renderer.render_cv_docx(result.identity, result.target, result.result); pdf, pages, status = renderer.render_cv_pdf(result.identity, result.target, result.result)
    assert Document(__import__("io").BytesIO(docx)).paragraphs
    assert PdfReader(__import__("io").BytesIO(pdf)).pages and pages <= 2 and status == "fit"
    assert "Example Person" in "\n".join(page.extract_text() or "" for page in PdfReader(__import__("io").BytesIO(pdf)).pages)
    db_session.query(CandidateProfile).filter_by(user_id="u1").one().preferred_email = "new@example.test"; db_session.commit()
    assert service.get("u1", result.id).identity.email == "old@example.test"


def test_unknown_skill_and_derived_number_fail_closed(db_session, monkeypatch):
    service, _ = _service(db_session, monkeypatch, _Drafting(bad_skill=True))
    with pytest.raises(ValueError, match="skill"):
        service.prepare("u1", ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Python role " * 20), include_cover_letter=False))
    service._drafting_agent = _Drafting(derived_number=True)
    with pytest.raises(ValueError, match="numerical"):
        service.prepare("u1", ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Python role " * 20), include_cover_letter=False))


def test_target_must_be_exactly_one_and_unknown_source_rejected(db_session, monkeypatch):
    with pytest.raises(ValueError, match="Exactly one"):
        ApplicationTargetInput(job_text="x" * 100, discovered_job_id="j")
    service, _ = _service(db_session, monkeypatch)
    class Bad(_Drafting):
        def draft_cv(self, context):
            result = super().draft_cv(context); result.summary_source_refs = [ApplicationSourceRef(source_type=EvidenceSourceType.CAREER_EVIDENCE, source_ref="missing")]; return result
    service._drafting_agent = Bad()
    with pytest.raises(ValueError, match="unknown or inactive"):
        service.prepare("u1", ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Python role " * 20), include_cover_letter=False))


def test_identity_is_checked_before_drafting(db_session, monkeypatch):
    drafting = _Drafting(); service, _ = _service(db_session, monkeypatch, drafting)
    db_session.query(CandidateProfile).filter_by(user_id="u1").one().display_name = None; db_session.commit()
    with pytest.raises(ValueError, match="display name"):
        service.prepare("u1", ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Python role " * 20)))
    assert drafting.calls == 0


def test_unsupported_questions_are_not_fabricated(db_session, monkeypatch):
    service, _ = _service(db_session, monkeypatch)
    result = service.prepare("u1", ApplicationPrepareRequest(target=ApplicationTargetInput(job_text="Python role " * 20), include_cover_letter=False, application_questions=["Unsupported question?"]))
    assert result.result.answers[0].status == "unsupported" and result.result.answers[0].answer is None
