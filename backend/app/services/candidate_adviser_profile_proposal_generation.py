"""Explicit orchestration for generating source-grounded Profile proposals."""

from dataclasses import dataclass
from typing import Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.candidate_adviser_profile_proposal import CandidateAdviserProfileProposalGenerator
from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.models.candidate_adviser_profile_proposal import (
    CandidateAdviserEnrichmentRecord,
    CandidateAdviserProfileProposalRecord,
)
from app.providers.llm import SemanticOutputError
from app.schemas.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalGeneration,
    CandidateAdviserProfileProposalGenerationInput,
    CandidateAdviserProfileProposalGenerationRead,
    CandidateAdviserProfileProposalUpdate,
    StructuredProfileSection,
    _UPDATE_ADAPTER,
)
from app.services.candidate_adviser_profile_proposal import (
    CandidateAdviserProfileProposalConflict,
    CandidateAdviserProfileProposalService,
    _canonical_json,
)


_SECTION_ITEMS = {
    StructuredProfileSection.EMPLOYMENT: "employment",
    StructuredProfileSection.EDUCATION: "education",
    StructuredProfileSection.CREDENTIALS: "credentials",
    StructuredProfileSection.SKILLS: "skills",
    StructuredProfileSection.PROJECTS: "projects",
    StructuredProfileSection.ACHIEVEMENTS: "achievements",
}


@dataclass(frozen=True, slots=True)
class _ConfirmedSourceBaseline:
    row_id: str
    user_id: str
    clarification_id: str
    status: str
    question_text: str
    interpretation_json: str
    origin_assessment_fingerprint: str

    @classmethod
    def capture(
        cls, record: CandidateAdviserClarificationRecord
    ) -> "_ConfirmedSourceBaseline":
        return cls(
            row_id=record.id,
            user_id=record.user_id,
            clarification_id=record.clarification_id,
            status=record.status,
            question_text=record.question_text,
            interpretation_json=record.interpretation_json,
            origin_assessment_fingerprint=record.origin_assessment_fingerprint,
        )


class CandidateAdviserProfileProposalGenerationService:
    def __init__(
        self,
        session: Session,
        *,
        generator_factory: Callable[[], CandidateAdviserProfileProposalGenerator],
    ) -> None:
        self._session = session
        self._generator_factory = generator_factory

    def generate(self, user_id: str, clarification_id: str) -> CandidateAdviserProfileProposalGenerationRead:
        lifecycle = CandidateAdviserProfileProposalService(self._session)
        source_record, source = lifecycle.generation_source(user_id, clarification_id)
        enrichment = self._session.get(
            CandidateAdviserEnrichmentRecord, (user_id, clarification_id)
        )
        if enrichment is None:
            enrichment = CandidateAdviserEnrichmentRecord(
                user_id=user_id,
                clarification_id=clarification_id,
                source_assessment_fingerprint=source_record.origin_assessment_fingerprint,
                state="pending",
            )
            self._session.add(enrichment)
            self._session.commit()

        existing = self._session.scalars(
            select(CandidateAdviserProfileProposalRecord).where(
                CandidateAdviserProfileProposalRecord.user_id == user_id,
                CandidateAdviserProfileProposalRecord.source_clarification_id == clarification_id,
            ).order_by(CandidateAdviserProfileProposalRecord.created_at,
                       CandidateAdviserProfileProposalRecord.id)
        ).all()
        if existing:
            enrichment.state = "proposals_created"
            self._session.commit()
            return CandidateAdviserProfileProposalGenerationRead(
                proposals=[lifecycle._read(row) for row in existing]
            )
        if enrichment.state == "reviewed_no_update":
            return CandidateAdviserProfileProposalGenerationRead(proposals=[])
        if not source.proposed_evidence:
            enrichment.state = "reviewed_no_update"
            self._session.commit()
            return CandidateAdviserProfileProposalGenerationRead(proposals=[])
        # A deferred enrichment is retryable only when the user explicitly
        # re-enters Review for Profile through this endpoint.
        if enrichment.state == "deferred":
            enrichment.state = "pending"
            self._session.commit()
        source_baseline = _ConfirmedSourceBaseline.capture(source_record)

        catalogue = lifecycle.target_catalogue(user_id)
        generation_input = CandidateAdviserProfileProposalGenerationInput(
            source=source,
            target_catalogue=catalogue,
        )
        generated = self._generator_factory().generate(generation_input=generation_input)
        updates = self._canonical_updates(generated)

        # Re-read all authority after the remote call; stale source/targets fail
        # closed before any proposal row is inserted.
        current_source_record, _ = lifecycle.generation_source(
            user_id, clarification_id, fresh=True
        )
        if _ConfirmedSourceBaseline.capture(current_source_record) != source_baseline:
            raise CandidateAdviserProfileProposalConflict(
                "The confirmed clarification changed during Profile proposal generation."
            )
        current_catalogue = lifecycle.target_catalogue(user_id, fresh=True)
        current_counts = lifecycle.target_fingerprint_counts(user_id, fresh=True)
        # First ensure each target was present in the immutable catalogue the
        # provider received, then ensure it remains exact in freshly loaded
        # current state and current bounded catalogue.
        self._validate_targets(updates, catalogue, current_counts)
        self._validate_targets(updates, current_catalogue, current_counts)
        persisted = lifecycle.materialize_batch_from_confirmed_clarification(
            user_id, clarification_id, updates
        )
        enrichment = self._session.get(
            CandidateAdviserEnrichmentRecord, (user_id, clarification_id)
        )
        if enrichment is None:
            enrichment = CandidateAdviserEnrichmentRecord(
                user_id=user_id,
                clarification_id=clarification_id,
                source_assessment_fingerprint=source_record.origin_assessment_fingerprint,
                state="pending",
            )
            self._session.add(enrichment)
        enrichment.state = "proposals_created" if persisted else "reviewed_no_update"
        self._session.commit()
        return CandidateAdviserProfileProposalGenerationRead(proposals=persisted)

    def defer(self, user_id: str, clarification_id: str) -> None:
        lifecycle = CandidateAdviserProfileProposalService(self._session)
        source_record, _ = lifecycle.generation_source(user_id, clarification_id)
        enrichment = self._session.get(
            CandidateAdviserEnrichmentRecord, (user_id, clarification_id)
        )
        if enrichment is None:
            enrichment = CandidateAdviserEnrichmentRecord(
                user_id=user_id,
                clarification_id=clarification_id,
                source_assessment_fingerprint=source_record.origin_assessment_fingerprint,
                state="pending",
            )
            self._session.add(enrichment)
        if enrichment.state == "pending":
            enrichment.state = "deferred"
            self._session.commit()

    @staticmethod
    def _canonical_updates(
        generated: CandidateAdviserProfileProposalGeneration,
    ) -> list[CandidateAdviserProfileProposalUpdate]:
        try:
            generated = CandidateAdviserProfileProposalGeneration.model_validate(generated)
            updates = [_UPDATE_ADAPTER.validate_python(update) for update in generated.proposals]
        except ValidationError as exc:
            raise SemanticOutputError(
                "Candidate Adviser Profile proposal generation returned an invalid proposal."
            ) from exc
        canonical = [_canonical_json(update.model_dump(mode="json")) for update in updates]
        if len(set(canonical)) != len(canonical):
            raise SemanticOutputError(
                "Candidate Adviser Profile proposal generation returned duplicate proposals."
            )
        return updates

    @staticmethod
    def _validate_targets(updates, catalogue, current_counts) -> None:
        supplied = {
            section: {entry.fingerprint for entry in getattr(catalogue, field_name)}
            for section, field_name in _SECTION_ITEMS.items()
        }
        supplied_anywhere = set().union(*supplied.values())

        for update in updates:
            update = _UPDATE_ADAPTER.validate_python(update)
            if update.operation == "add":
                if update.target_fingerprint is not None:
                    raise CandidateAdviserProfileProposalConflict(
                        "An add proposal cannot target a current structured item."
                    )
                continue
            target = update.target_fingerprint
            section = StructuredProfileSection(update.section)
            if not target or target not in supplied_anywhere:
                raise CandidateAdviserProfileProposalConflict(
                    "A replace_exact proposal must target an item in the supplied current Profile catalogue."
                )
            if target not in supplied[section]:
                raise CandidateAdviserProfileProposalConflict(
                    "A replace_exact target must belong to the same structured section."
                )
            if current_counts[section][target] != 1 or any(
                count[target] for other_section, count in current_counts.items()
                if other_section is not section
            ):
                raise CandidateAdviserProfileProposalConflict(
                    "A replace_exact target must match exactly one item in the same structured section."
                )
