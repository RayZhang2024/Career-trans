import hashlib
import json
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import CandidateCVIngestionDraft, CandidateEvidenceRecord, CandidateStructuredProfile
from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate import CandidateContext, CandidateContextSummary, CareerEvidence
from app.schemas.cv_ingestion import CVIngestionDraftRead, CVIngestionState, CandidateCVData, CareerEvidenceDraft, EvidenceProvenance, ExtractedCVDocument
from app.services.cv_file_extraction_service import CVFileExtractionService
from app.services.cv_interpretation_service import CVSemanticInterpreter
from app.services.cv_merge_service import CVMergeService


class CVIngestionService:
    def __init__(
        self,
        session: Session,
        *,
        interpreter: CVSemanticInterpreter | None = None,
        interpreter_factory: Callable[[], CVSemanticInterpreter] | None = None,
        extractor: CVFileExtractionService | None = None,
        merger: CVMergeService | None = None,
    ) -> None:
        self._session = session
        self._interpreter = interpreter
        self._interpreter_factory = interpreter_factory
        self._extractor = extractor or CVFileExtractionService()
        self._merger = merger or CVMergeService()

    def upload(self, user_id: str, files: list[tuple[str, str | None, bytes]]) -> CVIngestionDraftRead:
        self._extractor.validate_batch(files)
        documents = [self._extractor.extract(filename=name, content_type=media_type, content=content) for name, media_type, content in files]
        draft = CandidateCVIngestionDraft(user_id=user_id, state=CVIngestionState.UPLOADED, documents_json=json.dumps([item.model_dump(mode="json") for item in documents]))
        self._session.add(draft)
        self._session.commit()
        self._session.refresh(draft)
        return self._read(draft)

    def interpret(self, user_id: str, draft_id: str) -> CVIngestionDraftRead:
        draft = self._draft(user_id, draft_id)
        if draft.state != CVIngestionState.UPLOADED:
            raise ValueError("Only an uploaded CV ingestion draft can be interpreted.")
        documents = self._documents(draft)
        imported: list[CandidateCVData] = []
        unstructured: list[ExtractedCVDocument] = []
        for document in documents:
            parsed = self._extractor.parsed_json(document)
            if parsed is None:
                unstructured.append(document)
            else:
                imported.append(CandidateCVData.model_validate(parsed))
        if unstructured:
            imported.append(self._semantic_interpreter().interpret(unstructured))
        merged = self._enrich_provenance(self._merger.merge(imported), documents)
        draft.merged_json = json.dumps(merged.model_dump(mode="json"))
        draft.state = CVIngestionState.REVIEW_READY
        self._session.commit()
        self._session.refresh(draft)
        return self._read(draft)

    def read(self, user_id: str, draft_id: str) -> CVIngestionDraftRead:
        return self._read(self._draft(user_id, draft_id))

    def edit_review(self, user_id: str, draft_id: str, corrected: CandidateCVData) -> CVIngestionDraftRead:
        draft = self._draft(user_id, draft_id)
        if draft.state != CVIngestionState.REVIEW_READY:
            raise ValueError("Only a review-ready CV ingestion draft can be edited.")
        draft.merged_json = json.dumps(corrected.model_dump(mode="json"))
        self._session.commit()
        self._session.refresh(draft)
        return self._read(draft)

    def confirm(self, user_id: str, draft_id: str) -> int:
        draft = self._draft(user_id, draft_id)
        if draft.state == CVIngestionState.CONFIRMED:
            return 0
        if draft.state != CVIngestionState.REVIEW_READY or not draft.merged_json:
            raise ValueError("CV ingestion draft is not ready for confirmation.")
        data = CandidateCVData.model_validate(json.loads(draft.merged_json))
        profile = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        if profile is None:
            profile = CandidateStructuredProfile(user_id=user_id, structured_json=json.dumps(data.model_dump(mode="json")))
            self._session.add(profile)
        else:
            profile.structured_json = json.dumps(data.model_dump(mode="json"))
        count = 0
        for item in data.evidence:
            fingerprint = self._fingerprint(item)
            record = self._session.scalar(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id, CandidateEvidenceRecord.fingerprint == fingerprint))
            if record is None:
                self._session.add(CandidateEvidenceRecord(user_id=user_id, fingerprint=fingerprint, evidence_type=item.evidence_type, title=item.title, text=item.text, skills_json=json.dumps(item.skills), provenance_json=json.dumps([value.model_dump(mode="json") for value in item.provenance])))
                count += 1
        draft.state = CVIngestionState.CONFIRMED
        self._session.commit()
        return count

    def _draft(self, user_id: str, draft_id: str) -> CandidateCVIngestionDraft:
        draft = self._session.scalar(select(CandidateCVIngestionDraft).where(CandidateCVIngestionDraft.id == draft_id, CandidateCVIngestionDraft.user_id == user_id))
        if draft is None:
            raise LookupError("CV ingestion draft not found.")
        return draft

    def _semantic_interpreter(self) -> CVSemanticInterpreter:
        if self._interpreter is None:
            if self._interpreter_factory is None:
                raise ValueError("No semantic CV interpreter is configured for unstructured CV documents.")
            self._interpreter = self._interpreter_factory()
        return self._interpreter

    def _read(self, draft: CandidateCVIngestionDraft) -> CVIngestionDraftRead:
        return CVIngestionDraftRead(id=draft.id, state=draft.state, documents=self._documents(draft), merged=CandidateCVData.model_validate(json.loads(draft.merged_json)) if draft.merged_json else None, created_at=draft.created_at, updated_at=draft.updated_at)

    @staticmethod
    def _documents(draft: CandidateCVIngestionDraft) -> list[ExtractedCVDocument]:
        return [ExtractedCVDocument.model_validate(value) for value in json.loads(draft.documents_json)]

    @staticmethod
    def _enrich_provenance(data: CandidateCVData, documents: list[ExtractedCVDocument]) -> CandidateCVData:
        available_segments = {
            document.provenance.document_sha256: set(document.provenance.segment_ids)
            for document in documents
        }
        fallback = [
            EvidenceProvenance(
                document_sha256=document.provenance.document_sha256,
                segment_ids=document.provenance.segment_ids,
            )
            for document in documents
        ]
        for item in data.evidence:
            if not item.provenance:
                item.provenance = list(fallback)
                continue
            for provenance in item.provenance:
                segment_ids = set(provenance.segment_ids)
                if (
                    provenance.document_sha256 not in available_segments
                    or not segment_ids
                    or not segment_ids.issubset(
                        available_segments[provenance.document_sha256]
                    )
                ):
                    raise ValueError(
                        "CV evidence provenance must reference supplied source segments."
                    )
        return data

    @staticmethod
    def _fingerprint(item: CareerEvidenceDraft) -> str:
        return hashlib.sha256("\x1f".join([item.evidence_type.casefold(), item.title.casefold(), item.text.casefold()]).encode()).hexdigest()


class PersistedCandidateContextLoader:
    """Build existing CandidateContext only from confirmed records owned by one user."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self, user_id: str) -> CandidateContext:
        profile = self._session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        structured = self._session.scalar(select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id))
        data = CandidateCVData.model_validate(json.loads(structured.structured_json)) if structured else CandidateCVData()
        records = list(self._session.scalars(select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id).order_by(CandidateEvidenceRecord.created_at)))
        profile_text = " ".join(value for value in ([profile.headline, profile.current_role, profile.summary, profile.location] if profile else []) if value)
        employment_text = "\n".join(f"{item.title} at {item.employer}. {item.description}" for item in data.employment)
        education_text = "\n".join(f"{item.qualification} at {item.institution}. {item.description}" for item in data.education)
        return CandidateContext(
            profile_text="\n".join(value for value in [profile_text, employment_text, education_text] if value),
            skills_text=", ".join(item.name for item in data.skills),
            career_strategy_text=profile.career_goal if profile and profile.career_goal else "",
            job_search_criteria_text=profile.job_search_criteria if profile and profile.job_search_criteria else "",
            evidence=[CareerEvidence(evidence_id=record.id, title=record.title, text=record.text, skills=json.loads(record.skills_json)) for record in records],
        )

    def load_confirmed(self, user_id: str) -> CandidateContext | None:
        """Return only confirmed CV-derived context; never fall back to demo data."""
        structured = self._session.scalar(
            select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)
        )
        return self.load(user_id) if structured is not None else None

    def summary(self, user_id: str) -> CandidateContextSummary:
        profile = self._session.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        career_strategy_configured = bool(profile and profile.career_goal and profile.career_goal.strip())
        job_search_criteria_configured = bool(
            profile and profile.job_search_criteria and profile.job_search_criteria.strip()
        )
        structured = self._session.scalar(
            select(CandidateStructuredProfile).where(CandidateStructuredProfile.user_id == user_id)
        )
        if structured is None:
            return CandidateContextSummary(
                ready=False,
                employment_count=0,
                education_count=0,
                skill_count=0,
                evidence_count=0,
                career_strategy_configured=career_strategy_configured,
                job_search_criteria_configured=job_search_criteria_configured,
            )
        data = CandidateCVData.model_validate(json.loads(structured.structured_json))
        evidence_count = len(
            list(
                self._session.scalars(
                    select(CandidateEvidenceRecord.id).where(CandidateEvidenceRecord.user_id == user_id)
                )
            )
        )
        return CandidateContextSummary(
            ready=True,
            employment_count=len(data.employment),
            education_count=len(data.education),
            skill_count=len(data.skills),
            evidence_count=evidence_count,
            career_strategy_configured=career_strategy_configured,
            job_search_criteria_configured=job_search_criteria_configured,
        )
