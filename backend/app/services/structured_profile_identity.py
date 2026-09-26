import hashlib
import json

from pydantic import BaseModel

from app.schemas.structured_profile import StructuredProfileSection


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def structured_profile_item_fingerprint(section: StructuredProfileSection | str, item: BaseModel) -> str:
    """Exact, section-sensitive identity for one canonical typed item."""
    canonical_section = StructuredProfileSection(section)
    if not isinstance(item, BaseModel):
        raise TypeError("Structured item fingerprints require a typed Pydantic item.")
    canonical = canonical_json({
        "section": canonical_section.value,
        "item": item.model_dump(mode="json"),
    })
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def structured_item_lineage_key(
    *,
    user_id: str,
    section: StructuredProfileSection | str,
    item_fingerprint: str,
    source_kind: str,
    source_ref: str,
    relationship: str,
    predecessor_fingerprint: str | None,
) -> str:
    material = {
        "user_id": user_id,
        "section": StructuredProfileSection(section).value,
        "item_fingerprint": item_fingerprint,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "relationship": relationship,
        "predecessor_fingerprint": predecessor_fingerprint,
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
