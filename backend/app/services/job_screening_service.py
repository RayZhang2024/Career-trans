import re

from app.schemas.discovery import JobListing, JobSearchQuery


class JobScreeningService:
    """Conservative, deterministic first-pass discovery filter."""

    def screen(self, listings: list[JobListing], query: JobSearchQuery) -> list[JobListing]:
        return [listing for listing in listings if self.is_promising(listing, query)]

    def is_promising(self, listing: JobListing, query: JobSearchQuery) -> bool:
        return (
            self._matches_keywords(listing, query.keywords)
            and self.matches_hard_constraints(listing, query)
        )

    def matches_hard_constraints(self, listing: JobListing, query: JobSearchQuery) -> bool:
        """Apply factual policy constraints without treating keywords as a relevance score."""
        return (
            self._matches_company(listing, query.companies)
            and self._does_not_match_excluded_company(listing, query.excluded_companies)
            and self._does_not_match_excluded_title(listing, query.excluded_title_terms)
            and self._matches_location(listing, query.locations, query.remote_ok)
            and self._matches_employment_type(listing, query.employment_types)
        )

    def _matches_keywords(self, listing: JobListing, keywords: list[str]) -> bool:
        if not keywords:
            return True
        searchable = " ".join(filter(None, [listing.title, listing.description])).casefold()
        for keyword in keywords:
            normalized = keyword.casefold().strip()
            if not normalized:
                continue
            if normalized in searchable:
                return True
            words = [word for word in re.findall(r"[a-z0-9+#.]+", normalized) if len(word) >= 3]
            if words and all(word in searchable for word in words):
                return True
        return False

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
        employment_type = listing.employment_type.casefold()
        return any(
            term.casefold().strip() in employment_type
            or employment_type in term.casefold().strip()
            for term in employment_types
            if term.strip()
        )
