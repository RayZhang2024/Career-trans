from app.schemas.cv_ingestion import CandidateCVData, CareerEvidenceDraft, EvidenceProvenance


class CVMergeService:
    """Conservative deterministic merge: first fact wins, provenance is always unioned."""

    def merge(self, values: list[CandidateCVData]) -> CandidateCVData:
        merged = CandidateCVData()
        for field, key in (("employment", lambda item: (item.employer.casefold(), item.title.casefold(), item.start_date)), ("education", lambda item: (item.institution.casefold(), item.qualification.casefold())), ("credentials", lambda item: (item.name.casefold(), item.credential_type.value, (item.issuer or "").casefold())), ("skills", lambda item: item.name.casefold()), ("projects", lambda item: item.name.casefold()), ("achievements", lambda item: item.text.casefold())):
            seen = {key(item) for item in getattr(merged, field)}
            for data in values:
                for item in getattr(data, field):
                    if key(item) not in seen:
                        getattr(merged, field).append(item)
                        seen.add(key(item))
        evidence: dict[tuple[str, str, str], CareerEvidenceDraft] = {}
        for data in values:
            for item in data.evidence:
                key = (item.evidence_type.casefold(), item.title.casefold(), item.text.casefold())
                existing = evidence.get(key)
                if existing is None:
                    evidence[key] = item.model_copy(deep=True)
                else:
                    known = {(p.document_sha256, tuple(p.segment_ids), p.source_kind) for p in existing.provenance}
                    existing.provenance.extend(
                        p
                        for p in item.provenance
                        if (p.document_sha256, tuple(p.segment_ids), p.source_kind) not in known
                    )
                    existing.skills.extend(
                        skill for skill in item.skills if skill.casefold() not in {value.casefold() for value in existing.skills}
                    )
        merged.evidence = list(evidence.values())
        return merged
