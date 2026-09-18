"""Application Preparation orchestration over existing candidate/job authorities."""

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.application_drafting import ApplicationDraftingAgent
from app.models.application_preparation import ApplicationPreparation
from app.models.candidate_cv_ingestion import CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.models.discovered_job import DiscoveredJob
from app.models.user import User
from app.providers.page_fetch import PageFetcher
from app.schemas.application_preparation import (
    ApplicationAnswerStatus, ApplicationIdentitySnapshot, ApplicationInsufficientDetailError,
    ApplicationPreparationRead, ApplicationPreparationResult, ApplicationPrepareRequest,
    ApplicationSourceRef, ApplicationTargetKind, ApplicationTargetSnapshot, CoverLetterContent,
    CVWritingDraft, TailoredBullet, TailoredCVContent, TailoredRole,
)
from app.schemas.candidate import CandidateContext, CareerEvidence
from app.schemas.cv_ingestion import CandidateCVData
from app.schemas.discovery import JobListing
from app.schemas.matching import EvidenceRef, EvidenceSourceType, RequirementMatch
from app.services.cv_ingestion_service import PersistedCandidateContextLoader
from app.services.user_job_discovery_service import UserJobDiscoveryService, _application_revision
from app.services.application_document_renderer import ApplicationDocumentRenderer
from app.workflows.career_analysis_graph import CareerAnalysisGraph


_CONTRACT_VERSION = "application-preparation-v1"
_MAX_CONTEXT_SOURCES = 18
_NUMBER = re.compile(r"(?<![A-Za-z])(?:£|\$|€)?\d+(?:[.,]\d+)?(?:\s*(?:%|years?|months?|hours?|minutes?|days?|k|m|million|bn))?", re.IGNORECASE)


class ApplicationPreparationService:
    """Small coordinator; target resolution, drafting validation and rendering stay separate."""

    def __init__(
        self, session: Session, *, graph: CareerAnalysisGraph,
        drafting_agent: ApplicationDraftingAgent, user_discovery: UserJobDiscoveryService,
        page_fetcher: PageFetcher | None = None,
    ) -> None:
        self._session = session
        self._graph = graph
        self._drafting_agent = drafting_agent
        self._user_discovery = user_discovery
        self._page_fetcher = page_fetcher

    def prepare(self, user_id: str, request: ApplicationPrepareRequest) -> ApplicationPreparationRead:
        # Validate every deterministic prerequisite before a semantic call.
        context = PersistedCandidateContextLoader(self._session).load_confirmed(user_id)
        if context is None:
            raise ValueError("Candidate profile is not ready.")
        profile = self._session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        user = self._session.get(User, user_id)
        if profile is None or not profile.display_name or not profile.display_name.strip() or user is None:
            raise ValueError("Application display name is required before preparation.")
        structured = self._structured(user_id)
        identity = ApplicationIdentitySnapshot(
            display_name=profile.display_name.strip(), email=(profile.preferred_email or user.email).strip(),
            phone=profile.phone, location=profile.location, linkedin_url=profile.linkedin_url,
            github_url=profile.github_url, portfolio_url=profile.portfolio_url,
        )
        target = self._resolve_target(user_id, request, context)
        source_catalog = self._bounded_sources(context, structured, target.requirement_matches)
        draft_context = self._draft_context(target, source_catalog, structured)
        cv_draft = self._drafting_agent.draft_cv(draft_context)
        cv = self._materialize_cv(cv_draft, structured, source_catalog)
        letter = None
        if request.include_cover_letter:
            letter = self._drafting_agent.draft_cover_letter({**draft_context, "cv_summary": cv.professional_summary})
            self._validate_text_and_refs(letter.body, letter.source_refs, source_catalog)
        answers = self._draft_answers(request.application_questions, draft_context, source_catalog)
        result = ApplicationPreparationResult(cv=cv, cover_letter=letter, answers=answers, target_pages=request.target_pages)
        # PDF is the authoritative V1 pagination measurement.  Persist only
        # its safe layout result; document bytes are rendered again on download.
        _, pages, layout_status = ApplicationDocumentRenderer().render_cv_pdf(identity, target, result)
        result = result.model_copy(update={"actual_pdf_pages": pages, "layout_status": layout_status})
        record = ApplicationPreparation(
            user_id=user_id,
            target_snapshot_json=_dump(target), identity_snapshot_json=_dump(identity),
            preparation_input_fingerprint=self._input_fingerprint(target, identity, source_catalog, structured),
            preparation_contract_fingerprint=self._contract_fingerprint(), preparation_result_json=_dump(result),
        )
        self._session.add(record); self._session.commit(); self._session.refresh(record)
        return self._read(record)

    def list_preparations(self, user_id: str) -> list[ApplicationPreparationRead]:
        return [self._read(row) for row in self._session.scalars(select(ApplicationPreparation).where(ApplicationPreparation.user_id == user_id).order_by(ApplicationPreparation.created_at.desc())).all()]

    def get(self, user_id: str, preparation_id: str) -> ApplicationPreparationRead:
        row = self._session.scalar(select(ApplicationPreparation).where(ApplicationPreparation.id == preparation_id, ApplicationPreparation.user_id == user_id))
        if row is None:
            raise LookupError("Application preparation not found.")
        return self._read(row)

    def _resolve_target(self, user_id: str, request: ApplicationPrepareRequest, context: CandidateContext) -> ApplicationTargetSnapshot:
        target = request.target
        if target.kind == ApplicationTargetKind.DISCOVERED_JOB:
            job = self._session.get(DiscoveredJob, target.discovered_job_id)
            if job is None or not self._user_discovery.is_currently_actionable(job):
                raise LookupError("Application target is unavailable.")
            listing = JobListing(source=job.source, source_token=job.source_token, external_id=job.external_id, title=job.title, company=job.company, location=job.location, url=job.url, description=job.description, posted_at=job.posted_at, work_arrangement=job.work_arrangement, employment_type=job.employment_type, detail_authority=job.detail_authority, verification_status=job.verification_status, verification_reason=job.verification_reason)
            reusable = self._user_discovery.current_evaluation_for_job(user_id, job)
            if reusable is not None:
                return ApplicationTargetSnapshot(source_kind=target.kind, canonical_discovered_job_id=job.id, public_url=job.url, title=job.title, company=job.company, location=job.location, work_arrangement=job.work_arrangement, employment_type=job.employment_type, job_profile=reusable.job_profile, requirement_matches=reusable.requirement_matches, job_content_hash=job.content_hash)
            return self._analysis_snapshot(target.kind, listing, context, canonical_id=job.id, content_hash=job.content_hash)
        if target.kind == ApplicationTargetKind.JOB_TEXT:
            listing = JobListing(source="application_text", title="Application target", url="application://local", description=target.job_text)
            return self._analysis_snapshot(target.kind, listing, context, content_hash=_hash(target.job_text or ""))
        url = str(target.job_url)
        existing = self._session.scalar(select(DiscoveredJob).where(DiscoveredJob.url == url))
        if existing is not None and self._user_discovery.is_currently_actionable(existing):
            copied = request.model_copy(update={"target": target.model_copy(update={"discovered_job_id": existing.id, "job_url": None})})
            return self._resolve_target(user_id, copied, context)
        if self._page_fetcher is None:
            raise ApplicationInsufficientDetailError("Job URL detail is unavailable; provide job text.")
        try:
            page = self._page_fetcher.fetch(url)
            detail = _html_text(page.html)
        except Exception as exc:
            raise ApplicationInsufficientDetailError("Job URL detail is unavailable; provide job text.") from exc
        if len(detail) < 500:
            raise ApplicationInsufficientDetailError("Job URL detail is insufficient; provide job text.")
        listing = JobListing(source="application_url", title="Application target", url=page.final_url, description=detail)
        return self._analysis_snapshot(target.kind, listing, context, content_hash=_hash(detail))

    def _analysis_snapshot(self, kind: ApplicationTargetKind, listing: JobListing, context: CandidateContext, *, canonical_id: str | None = None, content_hash: str) -> ApplicationTargetSnapshot:
        if not listing.description:
            raise ApplicationInsufficientDetailError("Job detail is unavailable; provide job text.")
        state = self._graph.invoke(job_text=listing.description, candidate_context=context, job_listing=listing)
        profile = state.get("job_profile")
        matches = state.get("requirement_matches")
        if profile is None or not profile.requirements or not matches:
            raise ApplicationInsufficientDetailError("Job detail did not yield sufficient requirements; provide job text.")
        return ApplicationTargetSnapshot(source_kind=kind, canonical_discovered_job_id=canonical_id, public_url=listing.url if kind != ApplicationTargetKind.JOB_TEXT else None, title=profile.title or listing.title, company=profile.company or listing.company, location=profile.location or listing.location, work_arrangement=profile.work_arrangement or listing.work_arrangement, employment_type=profile.employment_type or listing.employment_type, job_profile=profile, requirement_matches=matches, job_content_hash=content_hash)

    def _bounded_sources(self, context: CandidateContext, data: CandidateCVData, matches: list[RequirementMatch]) -> dict[tuple[str, str], str]:
        catalog: dict[tuple[str, str], str] = {}
        by_id = {item.evidence_id: item for item in context.evidence}
        for match in sorted(matches, key=lambda item: (item.requirement.importance.value != "essential", item.requirement_index)):
            refs = match.evidence_refs or [EvidenceRef(source_type=EvidenceSourceType.CAREER_EVIDENCE, source_ref=item) for item in match.evidence_ids]
            for ref in refs:
                if ref.source_type == EvidenceSourceType.CAREER_EVIDENCE and ref.source_ref in by_id:
                    catalog.setdefault((ref.source_type.value, ref.source_ref), by_id[ref.source_ref].text)
                elif ref.value:
                    catalog.setdefault((ref.source_type.value, ref.source_ref), ref.value)
                if len(catalog) >= _MAX_CONTEXT_SOURCES:
                    break
            if len(catalog) >= _MAX_CONTEXT_SOURCES:
                break
        # Confirmed deterministic facts are separately typed, never fake evidence.
        for index, item in enumerate(data.employment):
            if len(catalog) >= _MAX_CONTEXT_SOURCES: break
            catalog.setdefault((EvidenceSourceType.EMPLOYMENT.value, f"employment:{index}"), f"{item.title} at {item.employer}. {item.description}")
        for index, item in enumerate(data.education):
            if len(catalog) >= _MAX_CONTEXT_SOURCES: break
            catalog.setdefault((EvidenceSourceType.EDUCATION.value, f"education:{index}"), f"{item.qualification} at {item.institution}. {item.description}")
        for index, item in enumerate(data.credentials):
            if len(catalog) >= _MAX_CONTEXT_SOURCES: break
            catalog.setdefault((EvidenceSourceType.CREDENTIAL.value, f"credential:{index}"), " ".join(value for value in [item.name, item.issuer, item.issued_date, item.expiry_date, item.description] if value))
        return catalog

    def _draft_context(self, target: ApplicationTargetSnapshot, catalog: dict[tuple[str, str], str], data: CandidateCVData) -> dict[str, object]:
        return {"job": {"title": target.title, "company": target.company, "requirements": [item.requirement.text for item in target.requirement_matches]}, "sources": [{"source_type": key[0], "source_ref": key[1], "text": value} for key, value in catalog.items()], "allowed_skills": sorted({item.name for item in data.skills}, key=str.casefold), "employment_count": len(data.employment)}

    def _materialize_cv(self, draft: CVWritingDraft, data: CandidateCVData, catalog: dict[tuple[str, str], str]) -> TailoredCVContent:
        self._validate_text_and_refs(draft.professional_summary, draft.summary_source_refs, catalog)
        allowed_skills = {item.name.casefold(): item.name for item in data.skills}
        if any(skill.casefold() not in allowed_skills for skill in draft.key_skills):
            raise ValueError("Generated CV contains a skill outside confirmed candidate skills.")
        drafts = {item.employment_index: item for item in draft.role_drafts}
        if any(index < 0 or index >= len(data.employment) for index in drafts):
            raise ValueError("Generated CV references an unknown employment record.")
        ordered = sorted(enumerate(data.employment), key=lambda pair: ((pair[1].end_date or pair[1].start_date or ""), pair[0]), reverse=True)
        roles: list[TailoredRole] = []
        for index, item in ordered:
            bullets = drafts.get(index).bullets if index in drafts else []
            for bullet in bullets:
                self._validate_text_and_refs(bullet.text, bullet.source_refs, catalog)
            roles.append(TailoredRole(employer=item.employer, title=item.title, start_date=item.start_date, end_date=item.end_date, location=item.location, bullets=bullets))
        return TailoredCVContent(professional_summary=draft.professional_summary, summary_source_refs=draft.summary_source_refs, key_skills=[allowed_skills[item.casefold()] for item in draft.key_skills], roles=roles, education=[f"{item.qualification} — {item.institution}" for item in data.education], credentials=[item.name for item in data.credentials])

    def _validate_text_and_refs(self, text: str, refs: list[ApplicationSourceRef], catalog: dict[tuple[str, str], str]) -> None:
        if not text.strip() or not refs:
            raise ValueError("Generated application content requires text and source references.")
        sources: list[str] = []
        for ref in refs:
            value = catalog.get((ref.source_type.value, ref.source_ref))
            if value is None:
                raise ValueError("Generated application content referenced an unknown or inactive source.")
            sources.append(_normal(value))
        for token in _NUMBER.findall(text):
            normalized = _normal(token)
            if normalized and not any(normalized in source for source in sources):
                raise ValueError("Generated application content contains an unsupported numerical claim.")

    def _draft_answers(self, questions: list[str], draft_context: dict[str, object], catalog: dict[tuple[str, str], str]):
        if not questions:
            return []
        drafted = self._drafting_agent.draft_answers({**draft_context, "questions": questions}).answers
        if len(drafted) != len(questions) or [item.question for item in drafted] != questions:
            raise ValueError("Application answer drafting did not return one answer per supplied question.")
        for item in drafted:
            if item.status == ApplicationAnswerStatus.DRAFTED:
                assert item.answer is not None
                self._validate_text_and_refs(item.answer, item.source_refs, catalog)
        return drafted

    @staticmethod
    def _unsupported_answer(question: str):
        from app.schemas.application_preparation import ApplicationQuestionAnswer
        return ApplicationQuestionAnswer(question=question, status=ApplicationAnswerStatus.UNSUPPORTED)

    def _structured(self, user_id: str) -> CandidateCVData:
        record = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        if record is None:
            raise ValueError("Candidate profile is not ready.")
        return CandidateCVData.model_validate(json.loads(record.structured_json))

    @staticmethod
    def _input_fingerprint(target, identity, catalog, data) -> str:
        sources = [
            {"source_type": source_type, "source_ref": source_ref, "text": text}
            for (source_type, source_ref), text in sorted(catalog.items())
        ]
        return _hash(_dump({"target": target, "identity": identity, "sources": sources, "structured": data}))

    @staticmethod
    def _contract_fingerprint() -> str:
        return _hash(_dump({"contract": _CONTRACT_VERSION, "revision": _application_revision(), "renderer": "ats_standard-v1", "provider": "semantic", "models": "application_drafting"}))

    @staticmethod
    def _read(row: ApplicationPreparation) -> ApplicationPreparationRead:
        return ApplicationPreparationRead(id=row.id, target=ApplicationTargetSnapshot.model_validate_json(row.target_snapshot_json), identity=ApplicationIdentitySnapshot.model_validate_json(row.identity_snapshot_json), preparation_input_fingerprint=row.preparation_input_fingerprint, preparation_contract_fingerprint=row.preparation_contract_fingerprint, result=ApplicationPreparationResult.model_validate_json(row.preparation_result_json), created_at=row.created_at)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []; self._ignored = 0
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}: self._ignored += 1
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}: self._ignored = max(0, self._ignored - 1)
    def handle_data(self, data: str) -> None:
        if not self._ignored: self.parts.append(data)


def _html_text(value: str) -> str:
    parser = _TextExtractor(); parser.feed(value); return " ".join(" ".join(parser.parts).split())


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"Unsupported application snapshot value: {type(value).__name__}")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normal(value: str) -> str:
    return " ".join(value.casefold().replace("approximately", "").replace("~", "").split())
