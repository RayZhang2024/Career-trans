"""Deterministic geography filtering and bounded pre-semantic job allocation."""

import re
from dataclasses import dataclass

from app.schemas.discovery import JobListing, JobSearchQuery


_WORLDWIDE_LOCATION_MARKERS = ("worldwide", "global", "anywhere")


@dataclass(frozen=True)
class PresemanticSelectionResult:
    """Safe local accounting for the hunt's existing semantic budget."""

    eligible: list[JobListing]
    selected: list[JobListing]
    geography_filtered: list[JobListing]
    unknown_geography: list[JobListing]
    work_arrangement_filtered: list[JobListing]

    @property
    def outside_budget(self) -> list[JobListing]:
        selected_ids = {id(listing) for listing in self.selected}
        return [listing for listing in self.eligible if id(listing) not in selected_ids]


class JobPresemanticSelectionService:
    """Allocate bounded semantic work without making a relevance judgement.

    Acquisition remains broad: this service is used only after current-run,
    actionable listings have been deduplicated. Location (where work may be
    performed) is evaluated from the listing's location field alone. Remote is
    a separate work-arrangement constraint and never supplies geography.
    """

    def select_for_hunt(
        self,
        listings: list[JobListing],
        *,
        query: JobSearchQuery,
        limit: int,
    ) -> PresemanticSelectionResult:
        eligible: list[JobListing] = []
        geography_filtered: list[JobListing] = []
        unknown_geography: list[JobListing] = []
        work_arrangement_filtered: list[JobListing] = []
        for listing in listings:
            geography = self._geography_status(listing, query.locations)
            if geography == "unknown":
                geography_filtered.append(listing)
                unknown_geography.append(listing)
                continue
            if geography == "incompatible":
                geography_filtered.append(listing)
                continue
            if not self._matches_remote_policy(listing, query.remote_ok):
                work_arrangement_filtered.append(listing)
                continue
            eligible.append(listing)
        return PresemanticSelectionResult(
            eligible=self.prioritize(eligible, keywords=query.keywords, limit=len(eligible)),
            selected=self.prioritize(eligible, keywords=query.keywords, limit=limit),
            geography_filtered=geography_filtered,
            unknown_geography=unknown_geography,
            work_arrangement_filtered=work_arrangement_filtered,
        )

    @classmethod
    def prioritize(
        cls,
        listings: list[JobListing],
        *,
        keywords: list[str],
        limit: int,
        preserve_input_ties: bool = False,
    ) -> list[JobListing]:
        """Keep one company/source front, then fill by deterministic affinity.

        Keywords are soft search themes. A zero-affinity job remains eligible;
        it is simply lower priority than a direct or near-direct title match.
        Canonical field tie-breaking deliberately avoids acquisition order. The
        existing ATS collector may explicitly preserve its provider order for
        equal-priority ties; hunt allocation never opts into that compatibility
        mode.
        """

        input_order = {id(listing): index for index, listing in enumerate(listings)}

        def sort_key(listing: JobListing) -> tuple[object, ...]:
            tie_breaker: object = input_order[id(listing)] if preserve_input_ties else cls._stable_key(listing)
            return (-cls.search_theme_affinity(listing, keywords), tie_breaker)

        fronts: dict[tuple[str, str], list[JobListing]] = {}
        for listing in listings:
            fronts.setdefault(cls._front_key(listing), []).append(listing)
        for candidates in fronts.values():
            candidates.sort(key=sort_key)

        representatives = [candidates[0] for candidates in fronts.values()]
        representatives.sort(key=sort_key)
        selected = representatives[:limit]
        if len(selected) >= limit:
            return selected

        remaining = [listing for candidates in fronts.values() for listing in candidates[1:]]
        remaining.sort(key=sort_key)
        return selected + remaining[: limit - len(selected)]

    @staticmethod
    def search_theme_affinity(listing: JobListing, keywords: list[str]) -> int:
        """Bounded lexical priority only; this is not a relevance filter."""

        title = " ".join(_tokens(listing.title))
        description_terms = set(_tokens(listing.description or ""))
        best = 0
        for keyword in keywords:
            terms = _tokens(keyword)
            if not terms:
                continue
            phrase = " ".join(terms)
            overlap = len(set(terms) & set(_tokens(listing.title)))
            direct = 100 if phrase in title else 0
            best = max(best, direct + 10 * overlap + len(set(terms) & description_terms))
        return best

    @staticmethod
    def _geography_status(listing: JobListing, requested_locations: list[str]) -> str:
        if not requested_locations:
            return "eligible"
        location_tokens = _tokens(listing.location or "")
        if not location_tokens:
            return "unknown"
        if any(_contains_phrase(location_tokens, _tokens(marker)) for marker in _WORLDWIDE_LOCATION_MARKERS):
            return "eligible"
        for requested in requested_locations:
            request = _normalise_location(requested)
            if not request:
                continue
            if any(
                _contains_phrase(location_tokens, _tokens(alias))
                for alias in _location_aliases(request)
            ):
                return "eligible"
        return "incompatible"

    @staticmethod
    def _matches_remote_policy(listing: JobListing, remote_ok: bool | None) -> bool:
        if remote_ok is not False:
            return True
        arrangement = " ".join(filter(None, [listing.location, listing.work_arrangement])).casefold()
        return "remote" not in arrangement

    @staticmethod
    def _front_key(listing: JobListing) -> tuple[str, str]:
        return (
            _normalise_location(listing.company or listing.source),
            _normalise_location(":".join((listing.source, listing.source_token or ""))),
        )

    @staticmethod
    def _stable_key(listing: JobListing) -> tuple[str, ...]:
        return (
            _normalise_location(listing.title),
            _normalise_location(listing.company or ""),
            _normalise_location(listing.location or ""),
            _normalise_location(listing.source),
            _normalise_location(listing.source_token or ""),
            listing.url.casefold().strip(),
        )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _normalise_location(value: str) -> str:
    return " ".join(_tokens(value))


def _contains_phrase(tokens: list[str], phrase: list[str]) -> bool:
    """Match complete normalized location words, never arbitrary substrings."""

    if not phrase or len(phrase) > len(tokens):
        return False
    return any(tokens[index : index + len(phrase)] == phrase for index in range(len(tokens) - len(phrase) + 1))


def _location_aliases(location: str) -> tuple[str, ...]:
    """Small country-name spelling aliases; no city/country inference is made."""

    aliases = {
        "united kingdom": ("united kingdom", "uk", "great britain"),
        "uk": ("united kingdom", "uk", "great britain"),
        "great britain": ("united kingdom", "uk", "great britain"),
        "united states": ("united states", "usa", "us"),
        "usa": ("united states", "usa", "us"),
        "us": ("united states", "usa", "us"),
    }
    return aliases.get(location, (location,))
