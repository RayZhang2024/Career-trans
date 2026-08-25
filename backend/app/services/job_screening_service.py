import re

from app.schemas.discovery import JobListing, JobSearchQuery


class JobScreeningService:
    """Apply only explicit, factual discovery constraints.

    Positive query keywords are discovery seeds.  Semantic relevance belongs to
    the bounded relevance stage, not to this deterministic filter.
    """

    def screen(self, listings: list[JobListing], query: JobSearchQuery) -> list[JobListing]:
        return [
            listing
            for listing in listings
            if self.matches_hard_constraints(listing, query)
        ]

    def is_promising(self, listing: JobListing, query: JobSearchQuery) -> bool:
        """Compatibility alias for callers using the former screening name."""
        return self.matches_hard_constraints(listing, query)

    def matches_hard_constraints(self, listing: JobListing, query: JobSearchQuery) -> bool:
        """Apply factual policy constraints without treating keywords as a relevance score."""
        return (
            self._matches_company(listing, query.companies)
            and self._does_not_match_excluded_company(listing, query.excluded_companies)
            and self._does_not_match_excluded_title(listing, query.excluded_title_terms)
            and self._matches_location(listing, query.locations, query.remote_ok)
            and self._matches_employment_type(listing, query.employment_types)
        )

    @staticmethod
    def _matches_company(listing: JobListing, companies: list[str]) -> bool:
        if not companies or not listing.company:
            return True
        company = listing.company.casefold()
        return any(term.casefold().strip() in company for term in companies if term.strip())

    @staticmethod
    def _does_not_match_excluded_company(listing: JobListing, excluded_companies: list[str]) -> bool:
        if not listing.company:
            return True
        company = listing.company.casefold()
        return not any(
            term.casefold().strip() in company
            for term in excluded_companies
            if term.strip()
        )

    @staticmethod
    def _does_not_match_excluded_title(listing: JobListing, excluded_title_terms: list[str]) -> bool:
        title = listing.title.casefold()
        return not any(
            term.casefold().strip() in title
            for term in excluded_title_terms
            if term.strip()
        )

    @staticmethod
    def _matches_location(
        listing: JobListing,
        locations: list[str],
        remote_ok: bool | None,
    ) -> bool:
        haystack = " ".join(
            filter(None, [listing.location, listing.work_arrangement])
        ).casefold()
        is_remote = "remote" in haystack
        if is_remote:
            return remote_ok is not False
        if not locations or not listing.location:
            return True
        location = listing.location.casefold()
        return any(
            term.casefold().strip() in location or location in term.casefold().strip()
            for term in locations
            if term.strip()
        )

    @staticmethod
    def _matches_employment_type(listing: JobListing, employment_types: list[str]) -> bool:
        if not employment_types or not listing.employment_type:
            return True
        employment_type = JobScreeningService._normalize_employment_type(listing.employment_type)
        return any(
            normalized_term in employment_type
            or employment_type in normalized_term
            for term in employment_types
            if (normalized_term := JobScreeningService._normalize_employment_type(term))
        )

    @staticmethod
    def _normalize_employment_type(value: str) -> str:
        """Normalize harmless spacing and hyphen variants, without broadening role policy."""
        return re.sub(r"[\s-]+", " ", value.casefold().strip())
