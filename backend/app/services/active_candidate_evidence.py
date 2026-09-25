"""Canonical current-evidence construction and legacy-safe reconciliation.

The current confirmed structured profile is the V1 authority for CV-derived
evidence.  Persisted rows are historical storage, not an active-set query.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_cv_ingestion import CandidateEvidenceRecord
from app.models.candidate_adviser import CandidateAdviserClarificationRecord
from app.schemas.candidate import CareerEvidence, CareerEvidenceProvenance
from app.schemas.candidate_adviser import ClarificationAnswerKind, ClarificationInterpretation
from app.schemas.cv_ingestion import (
    CandidateCVData,
    CareerEvidenceDraft,
    Credential,
    Education,
    Employment,
)
from app.services.career_evidence_fingerprint import (
    career_evidence_fingerprint,
    confirmed_profile_source_ref,
    legacy_career_evidence_fingerprint,
)


class CanonicalCareerEvidenceDraft:
    """Application-owned draft; unlike CV extraction it can use all canonical provenance."""

    def __init__(
        self,
        *,
        evidence_type: str,
        title: str,
        text: str,
        skills: list[str] | None = None,
        provenance: list[CareerEvidenceProvenance] | None = None,
    ) -> None:
        self.evidence_type = evidence_type
        self.title = title
        self.text = text
        self.skills = skills or []
        self.provenance = provenance or []


class CanonicalCandidateEvidenceBuilder:
    """Build factual, current-profile evidence without semantic paraphrasing."""

    def build(
        self,
        data: CandidateCVData,
        clarification_drafts: Iterable[CanonicalCareerEvidenceDraft] = (),
    ) -> list[CanonicalCareerEvidenceDraft]:
        drafts = [self._from_semantic(item) for item in data.evidence]
        drafts.extend(self._employment(item) for item in data.employment)
        drafts.extend(self._education(item) for item in data.education)
        drafts.extend(self._credential(item) for item in data.credentials)
        drafts.extend(clarification_drafts)
        return self._deduplicate_current(drafts)

    @staticmethod
    def _from_semantic(item: CareerEvidenceDraft) -> CanonicalCareerEvidenceDraft:
        """Convert CV extraction provenance only after schema/application validation."""
        return CanonicalCareerEvidenceDraft(
            evidence_type=item.evidence_type,
            title=item.title,
            text=item.text,
            skills=item.skills,
            provenance=[CareerEvidenceProvenance(**value.model_dump(mode="json")) for value in item.provenance],
        )

    @staticmethod
    def _employment(item: Employment) -> CanonicalCareerEvidenceDraft:
        dates = " – ".join(value for value in (item.start_date, item.end_date) if value)
        title = f"{item.title} at {item.employer}"
        text = title + (f" ({dates})" if dates else "")
        return CanonicalCareerEvidenceDraft(
            evidence_type="employment",
            title=title,
            text=text,
            provenance=[
                CareerEvidenceProvenance(
                    source_kind="confirmed_profile",
                    source_ref=confirmed_profile_source_ref(
                        "employment",
                        [item.employer, item.title, item.start_date, item.end_date, item.location],
                    ),
                )
            ],
        )

    @staticmethod
    def _education(item: Education) -> CanonicalCareerEvidenceDraft:
        detail = ", ".join(value for value in (item.qualification, item.field_of_study) if value)
        title = f"{detail} at {item.institution}"
        return CanonicalCareerEvidenceDraft(
            evidence_type="education",
            title=title,
            text=title,
            provenance=[
                CareerEvidenceProvenance(
                    source_kind="confirmed_profile",
                    source_ref=confirmed_profile_source_ref(
                        "education",
                        [item.institution, item.qualification, item.field_of_study],
                    ),
                )
            ],
        )

    @staticmethod
    def _credential(item: Credential) -> CanonicalCareerEvidenceDraft:
        title = item.name
        details = [item.name, item.credential_type.value]
        if item.issuer:
            details.append(item.issuer)
        if item.status:
            details.append(item.status)
        if item.issued_date:
            details.append(f"issued {item.issued_date}")
        if item.expiry_date:
            details.append(f"expires {item.expiry_date}")
        return CanonicalCareerEvidenceDraft(
            evidence_type="credential",
            title=title,
            text=" — ".join(details),
            provenance=[
                CareerEvidenceProvenance(
                    source_kind="confirmed_profile",
                    source_ref=confirmed_profile_source_ref(
                        "credential",
                        [
                            item.name,
                            item.credential_type.value,
                            item.issuer,
                            item.issued_date,
                            item.expiry_date,
                            item.status,
                        ],
                    ),
                )
            ],
        )

    @staticmethod
    def _deduplicate_current(
        items: Iterable[CanonicalCareerEvidenceDraft],
    ) -> list[CanonicalCareerEvidenceDraft]:
        """Union only duplicate representations within this one confirmed profile."""
        result: list[CanonicalCareerEvidenceDraft] = []
        by_fingerprint: dict[str, CanonicalCareerEvidenceDraft] = {}
        for item in items:
            key = career_evidence_fingerprint(item)
            existing = by_fingerprint.get(key)
            if existing is None:
                existing = CanonicalCareerEvidenceDraft(
                    evidence_type=item.evidence_type,
                    title=item.title,
                    text=item.text,
                    skills=list(item.skills),
                    provenance=list(item.provenance),
                )
                by_fingerprint[key] = existing
                result.append(existing)
                continue
            existing.skills = _unique([*existing.skills, *item.skills])
            known = {_provenance_key(value) for value in existing.provenance}
            existing.provenance.extend(
                value for value in item.provenance if _provenance_key(value) not in known
            )
        return result


class ActiveCandidateEvidenceResolver:
    """Reconcile current canonical claims and return only the active records.

    Each call uses a savepoint, so a failed materialisation leaves the session's
    pre-existing persisted state intact.  The caller controls the outer commit.
    """

    def __init__(self, session: Session, *, builder: CanonicalCandidateEvidenceBuilder | None = None) -> None:
        self._session = session
        self._builder = builder or CanonicalCandidateEvidenceBuilder()

    def resolve(self, user_id: str, data: CandidateCVData) -> list[CareerEvidence]:
        drafts = self._builder.build(data, self._confirmed_clarification_drafts(user_id))
        with self._session.begin_nested():
            records = list(
                self._session.scalars(
                    select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
                )
            )
            by_fingerprint = {record.fingerprint: record for record in records}
            active: list[CareerEvidence] = []
            for item in drafts:
                canonical = career_evidence_fingerprint(item)
                record = by_fingerprint.get(canonical)
                if record is None:
                    record = by_fingerprint.get(legacy_career_evidence_fingerprint(item))
                if record is None:
                    record = CandidateEvidenceRecord(
                        user_id=user_id,
                        fingerprint=canonical,
                        evidence_type=item.evidence_type,
                        title=item.title,
                        text=item.text,
                        skills_json="[]",
                        provenance_json="[]",
                    )
                    self._session.add(record)
                    by_fingerprint[canonical] = record
                # Current metadata replaces historical metadata across revisions.
                record.fingerprint = canonical
                record.evidence_type = item.evidence_type
                record.title = item.title
                record.text = item.text
                record.skills_json = json.dumps(_unique(item.skills))
                record.provenance_json = json.dumps(
                [value.model_dump(mode="json") for value in item.provenance], sort_keys=True
                )
                self._session.flush()
                active.append(_runtime(record))
        return active

    def read_active(self, user_id: str, data: CandidateCVData) -> list[CareerEvidence]:
        """Return the persisted current set without reconciling or mutating rows."""
        return list(self.inspect_active(user_id, data).evidence)

    def inspect_active(
        self, user_id: str, data: CandidateCVData
    ) -> "ActiveCandidateEvidenceRead":
        """Inspect the current derived set and persisted coverage without writes.

        ``read_active`` intentionally returns only persisted evidence rows. This
        richer read makes omissions explicit so a downstream consumer cannot
        mistake a partial materialisation for the full current evidence set.
        """
        drafts = self._builder.build(data, self._confirmed_clarification_drafts(user_id))
        records = list(self._session.scalars(
            select(CandidateEvidenceRecord).where(CandidateEvidenceRecord.user_id == user_id)
        ))
        by_fingerprint = {record.fingerprint: record for record in records}
        active: list[CareerEvidence] = []
        missing: list[str] = []
        for item in drafts:
            canonical = career_evidence_fingerprint(item)
            record = by_fingerprint.get(canonical) or by_fingerprint.get(
                legacy_career_evidence_fingerprint(item)
            )
            if record is None:
                missing.append(canonical)
            else:
                active.append(_runtime(record))
        return ActiveCandidateEvidenceRead(
            evidence=tuple(active),
            expected_count=len(drafts),
            missing_fingerprints=tuple(missing),
        )

    def _confirmed_clarification_drafts(self, user_id: str) -> list[CanonicalCareerEvidenceDraft]:
        records = self._session.scalars(
            select(CandidateAdviserClarificationRecord).where(
                CandidateAdviserClarificationRecord.user_id == user_id,
                CandidateAdviserClarificationRecord.status == "confirmed",
            ).order_by(CandidateAdviserClarificationRecord.clarification_id)
        ).all()
        drafts: list[CanonicalCareerEvidenceDraft] = []
        for record in records:
            if not record.interpretation_json:
                continue
            interpretation = ClarificationInterpretation.model_validate(json.loads(record.interpretation_json))
            if interpretation.answer_kind not in {
                ClarificationAnswerKind.CAREER_FACT,
                ClarificationAnswerKind.MIXED,
            }:
                continue
            for proposal in interpretation.proposed_evidence:
                drafts.append(CanonicalCareerEvidenceDraft(
                    evidence_type=proposal.evidence_type,
                    title=proposal.title,
                    text=proposal.text,
                    skills=proposal.skills,
                    provenance=[CareerEvidenceProvenance(
                        source_kind="user_confirmed",
                        source_ref=f"clarification:{record.clarification_id}",
                    )],
                ))
        return drafts


def _runtime(record: CandidateEvidenceRecord) -> CareerEvidence:
    provenance = json.loads(record.provenance_json)
    return CareerEvidence(
        evidence_id=record.id,
        evidence_type=record.evidence_type,
        title=record.title,
        text=record.text,
        skills=json.loads(record.skills_json),
        provenance=provenance if isinstance(provenance, list) else [],
    )


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def _provenance_key(value: CareerEvidenceProvenance) -> tuple[str, str | None, tuple[str, ...], str | None]:
    return (value.source_kind, value.document_sha256, tuple(value.segment_ids), value.source_ref)


@dataclass(frozen=True, slots=True)
class ActiveCandidateEvidenceRead:
    evidence: tuple[CareerEvidence, ...]
    expected_count: int
    missing_fingerprints: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_fingerprints
