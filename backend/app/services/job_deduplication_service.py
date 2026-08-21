from urllib.parse import urlsplit, urlunsplit

from app.schemas.discovery import JobListing


class JobDeduplicationService:
    """Deterministically retains the first listing for each stable identity."""

    def deduplicate(self, listings: list[JobListing]) -> tuple[list[JobListing], int]:
        seen: set[tuple[str, ...]] = set()
        unique: list[JobListing] = []

        for listing in listings:
            keys = self._keys(listing)
            if any(key in seen for key in keys):
                continue
            seen.update(keys)
            unique.append(listing)

        return unique, len(listings) - len(unique)

    @classmethod
    def _keys(cls, listing: JobListing) -> list[tuple[str, ...]]:
        keys: list[tuple[str, ...]] = [("url", cls._canonical_url(listing.url))]
        if listing.external_id:
            keys.append(("external", listing.source.casefold(), listing.external_id.casefold()))
        if listing.company and listing.title and listing.location:
            keys.append(
                (
                    "fallback",
                    cls._normalize_text(listing.company),
                    cls._normalize_text(listing.title),
                    cls._normalize_text(listing.location),
                )
            )
        return keys

    @staticmethod
    def _canonical_url(url: str) -> str:
        parts = urlsplit(url.strip())
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, "", ""))

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.casefold().split())
