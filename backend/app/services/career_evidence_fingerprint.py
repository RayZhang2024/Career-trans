import hashlib

from app.schemas.cv_ingestion import CareerEvidenceDraft


def career_evidence_fingerprint(item: CareerEvidenceDraft) -> str:
    """Stable identity shared by CV confirmation and bounded downstream views."""
    return hashlib.sha256(
        "\x1f".join(
            [item.evidence_type.casefold(), item.title.casefold(), item.text.casefold()]
        ).encode()
    ).hexdigest()
