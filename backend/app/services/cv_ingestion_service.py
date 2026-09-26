import json
from collections import Counter
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import (
    CandidateCVIngestionDraft,
    CandidateCVReviewBaseline,
    CandidateEvidenceRecord,
    CandidateStructuredProfile,
)
from app.models.candidate_cv_overlap_review import CandidateCVOverlapReviewRecord
from app.schemas.candidate import CareerEvidence
from app.schemas.cv_ingestion import CVIngestionDraftRead, CVIngestionHistoryItem, CVIngestionHistoryRead, CVIngestionState, CandidateCVData, EvidenceProvenance, ExtractedCVDocument
from app.schemas.structured_profile import StructuredItemSourceKind, StructuredProfileItemLineageInput
from app.schemas.ai_settings import SemanticOperation
from app.services.cv_file_extraction_service import CVFileExtractionService
from app.services.cv_interpretation_service import CVSemanticInterpreter
from app.services.cv_merge_service import CVMergeService
from app.services.active_candidate_evidence import ActiveCandidateEvidenceResolver
from app.services.llm_runtime import ResolvedRuntimeSnapshot
from app.services.semantic_runtime_attribution import available_attribution, canonical_attribution_json, not_used_attribution, read_attribution
from app.services.candidate_structured_item_lineage import CandidateStructuredItemLineageService
from app.services.cv_overlap_review_service import CVOverlapReviewService


class CVIngestionService:
    def __init__(
        self,
        session: Session,
        *,
        interpreter: CVSemanticInterpreter | None = None,
        interpreter_factory: Callable[[], CVSemanticInterpreter] | None = None,
        extractor: CVFileExtractionService | None = None,
        merger: CVMergeService | None = None,
        runtime_snapshot: ResolvedRuntimeSnapshot | None = None,
    ) -> None:
        self._session = session
        self._interpreter = interpreter
        self._interpreter_factory = interpreter_factory
        self._extractor = extractor or CVFileExtractionService()
        self._merger = merger or CVMergeService()
        self._runtime_snapshot = runtime_snapshot

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
                imported_data = CandidateCVData.model_validate(parsed)
                self._enrich_provenance(
                    imported_data,
                    [document],
                    allow_missing_provenance=True,
                )
                imported.append(imported_data)
        if unstructured:
            if self._runtime_snapshot is None:
                raise ValueError("A resolved runtime snapshot is required for semantic CV interpretation.")
            semantic_data = self._semantic_interpreter().interpret(unstructured)
            self._enrich_provenance(
                semantic_data,
                unstructured,
                allow_missing_provenance=False,
            )
            imported.append(semantic_data)
        merged = self._merger.merge(imported)
        attribution = (
            available_attribution(self._runtime_snapshot, (SemanticOperation.CV_SEMANTIC_EXTRACTION,))
            if unstructured and self._runtime_snapshot is not None
            else not_used_attribution()
        )
        try:
            # Flush the draft transition first so SQLite has opened the outer
            # transaction before _create_baseline_once() creates its SAVEPOINT.
            # Otherwise that SAVEPOINT can be the first physical transaction;
            # releasing it would commit the baseline independently, making a
            # later failure impossible to roll back atomically.
            draft.merged_json = json.dumps(merged.model_dump(mode="json"))
            draft.runtime_attribution_json = canonical_attribution_json(attribution)
            draft.state = CVIngestionState.REVIEW_READY
            self._session.flush()
            self._create_baseline_once(draft, merged.evidence)
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(draft)
        return self._read(draft)

    def read(self, user_id: str, draft_id: str) -> CVIngestionDraftRead:
        return self._read(self._draft(user_id, draft_id))

    def edit_review(self, user_id: str, draft_id: str, corrected: CandidateCVData) -> CVIngestionDraftRead:
        draft = self._draft(user_id, draft_id, for_update=True)
        if draft.state != CVIngestionState.REVIEW_READY:
            raise ValueError("Only a review-ready CV ingestion draft can be edited.")
        baseline = self._baseline_for_review(draft)
        self._validate_evidence_subset(corrected.evidence, baseline)
        try:
            draft.merged_json = json.dumps(corrected.model_dump(mode="json"))
            self._session.execute(
                delete(CandidateCVOverlapReviewRecord).where(
                    CandidateCVOverlapReviewRecord.user_id == user_id,
                    CandidateCVOverlapReviewRecord.draft_id == draft.id,
                )
            )
            self._session.commit()
            self._session.refresh(draft)
        except Exception:
            self._session.rollback()
            raise
        return self._read(draft)

    def confirm(self, user_id: str, draft_id: str) -> int:
        draft = self._draft(user_id, draft_id, for_update=True)
        if draft.state == CVIngestionState.CONFIRMED:
            return 0
        if draft.state != CVIngestionState.REVIEW_READY or not draft.merged_json:
            raise ValueError("CV ingestion draft is not ready for confirmation.")
        data = CandidateCVData.model_validate(json.loads(draft.merged_json))
        try:
            profile = self._session.scalar(
                select(CandidateStructuredProfile)
                .where(CandidateStructuredProfile.user_id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            resolved_data, transitions = CVOverlapReviewService(
                self._session
            ).resolve_for_confirmation(
                user_id, draft, profile, data
            )
            if profile is None:
                profile = CandidateStructuredProfile(
                    user_id=user_id,
                    structured_json=json.dumps(resolved_data.model_dump(mode="json")),
                )
                self._session.add(profile)
            else:
                profile.structured_json = json.dumps(resolved_data.model_dump(mode="json"))
            existing_ids = set(
                self._session.scalars(
                    select(CandidateEvidenceRecord.id).where(CandidateEvidenceRecord.user_id == user_id)
                )
            )
            active = ActiveCandidateEvidenceResolver(self._session).resolve(user_id, resolved_data)
            count = len([item for item in active if item.evidence_id not in existing_ids])
            CandidateStructuredItemLineageService(self._session).stage_many(
                user_id,
                [
                    StructuredProfileItemLineageInput(
                        section=transition.section,
                        item=transition.item,
                        source_kind=StructuredItemSourceKind.CV,
                        source_ref=draft.id,
                        relationship=transition.relationship,
                        predecessor_item=transition.predecessor_item,
                    )
                    for transition in transitions
                ],
            )
            draft.state = CVIngestionState.CONFIRMED
            self._session.commit()
            return count
        except Exception:
            self._session.rollback()
            raise

    def _draft(
        self, user_id: str, draft_id: str, *, for_update: bool = False
    ) -> CandidateCVIngestionDraft:
        statement = select(CandidateCVIngestionDraft).where(
            CandidateCVIngestionDraft.id == draft_id,
            CandidateCVIngestionDraft.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        draft = self._session.scalar(statement)
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
        return _project_draft(draft)

    @staticmethod
    def _documents(draft: CandidateCVIngestionDraft) -> list[ExtractedCVDocument]:
        return [ExtractedCVDocument.model_validate(value) for value in json.loads(draft.documents_json)]

    def _create_baseline_once(
        self,
        draft: CandidateCVIngestionDraft,
        evidence: list,
    ) -> CandidateCVReviewBaseline:
        """Persist one immutable baseline, including safely racing legacy callers."""
        existing = self._session.scalar(
            select(CandidateCVReviewBaseline).where(
                CandidateCVReviewBaseline.draft_id == draft.id
            )
        )
        if existing is not None:
            return existing
        serialized = json.dumps([item.model_dump(mode="json") for item in evidence], sort_keys=True)
        try:
            with self._session.begin_nested():
                baseline = CandidateCVReviewBaseline(
                    draft_id=draft.id,
                    evidence_json=serialized,
                )
                self._session.add(baseline)
                self._session.flush()
                return baseline
        except IntegrityError:
            baseline = self._session.scalar(
                select(CandidateCVReviewBaseline).where(
                    CandidateCVReviewBaseline.draft_id == draft.id
                )
            )
            if baseline is None:
                raise
            return baseline

    def _baseline_for_review(self, draft: CandidateCVIngestionDraft) -> list:
        baseline = self._session.scalar(
            select(CandidateCVReviewBaseline).where(
                CandidateCVReviewBaseline.draft_id == draft.id
            )
        )
        if baseline is None:
            # Grandfather one immutable snapshot for legacy review-ready drafts,
            # before considering the caller's mutable correction.
            current = CandidateCVData.model_validate(json.loads(draft.merged_json or "{}"))
            baseline = self._create_baseline_once(draft, current.evidence)
        return [
            self._evidence_key(value)
            for value in json.loads(baseline.evidence_json)
        ]

    @staticmethod
    def _evidence_key(value: object) -> str:
        """Exact canonical identity for CV semantic evidence, preserving provenance."""
        item = value if isinstance(value, dict) else value.model_dump(mode="json")
        selected = {
            key: item.get(key)
            for key in ("evidence_type", "title", "text", "skills", "provenance")
        }
        return json.dumps(selected, sort_keys=True, separators=(",", ":"))

    def _validate_evidence_subset(self, submitted: list, baseline: list[str]) -> None:
        submitted_keys = Counter(self._evidence_key(value) for value in submitted)
        baseline_keys = Counter(baseline)
        if any(count > baseline_keys[key] for key, count in submitted_keys.items()):
            raise ValueError(
                "Career Evidence can only retain or exclude unchanged evidence from the interpreted CV."
            )

    @staticmethod
    def _enrich_provenance(
        data: CandidateCVData,
        documents: list[ExtractedCVDocument],
        *,
        allow_missing_provenance: bool,
    ) -> CandidateCVData:
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
                if not allow_missing_provenance:
                    raise ValueError(
                        "Semantic CV evidence must identify supplied source segments."
                    )
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


class CVIngestionReadService:
    """Settings/provider-independent ownership-scoped historical CV reads."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, user_id: str, *, limit: int = 20) -> CVIngestionHistoryRead:
        rows = list(self._session.scalars(
            select(CandidateCVIngestionDraft)
            .where(CandidateCVIngestionDraft.user_id == user_id)
            .order_by(CandidateCVIngestionDraft.created_at.desc(), CandidateCVIngestionDraft.id.desc())
            .limit(limit + 1)
        ))
        truncated = len(rows) > limit
        rows = rows[:limit]
        items = []
        for draft in rows:
            documents = [ExtractedCVDocument.model_validate(value) for value in json.loads(draft.documents_json)]
            items.append(CVIngestionHistoryItem(
                id=draft.id,
                state=draft.state,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
                filenames=[document.provenance.filename for document in documents],
                document_count=len(documents),
            ))
        return CVIngestionHistoryRead(items=items, limit=limit, truncated=truncated)

    def read(self, user_id: str, draft_id: str) -> CVIngestionDraftRead:
        draft = self._session.scalar(select(CandidateCVIngestionDraft).where(
            CandidateCVIngestionDraft.id == draft_id,
            CandidateCVIngestionDraft.user_id == user_id,
        ))
        if draft is None:
            raise LookupError("CV ingestion draft not found.")
        return _project_draft(draft)


def _project_draft(draft: CandidateCVIngestionDraft) -> CVIngestionDraftRead:
    attribution = (
        read_attribution(draft.runtime_attribution_json, null_value=None)
        if draft.state == CVIngestionState.UPLOADED
        else read_attribution(draft.runtime_attribution_json)
    )
    return CVIngestionDraftRead(
        id=draft.id,
        state=draft.state,
        documents=[ExtractedCVDocument.model_validate(value) for value in json.loads(draft.documents_json)],
        merged=CandidateCVData.model_validate(json.loads(draft.merged_json)) if draft.merged_json else None,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        runtime_attribution=attribution,
    )
