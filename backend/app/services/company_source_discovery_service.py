import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_career_source import CompanyCareerSource
from app.schemas.job_sources import (
    CompanySourceDiscoveryRequest,
    CompanySourceDiscoveryResponse,
    CompanySourceDiscoveryResult,
    CompanySourceProvenance,
    CompanySourceStatus,
    CompanyTarget,
)
from app.services.ats_resolver_service import AtsResolverService


class CompanySourceDiscoveryService:
    """Resolve and persist public ATS sources before Issue #22 job discovery."""

    def __init__(
        self,
        *,
        session: Session,
        resolver: AtsResolverService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._resolver = resolver
        self._now = now or (lambda: datetime.now(timezone.utc))

    def resolve_sources(
        self, request: CompanySourceDiscoveryRequest
    ) -> CompanySourceDiscoveryResponse:
        now = self._now()
        stale_after = timedelta(days=request.stale_after_days)
        results = [
            self._resolve_target(target, now, stale_after, request.refresh)
            for target in self._unique_enabled_targets(request.companies)
        ]
        self._session.commit()
        return CompanySourceDiscoveryResponse(results=results)

    def _resolve_target(
        self,
        target: CompanyTarget,
        now: datetime,
        stale_after: timedelta,
        refresh: bool,
    ) -> CompanySourceDiscoveryResult:
        key = canonical_company_key(target.name)
        record = self._session.scalar(
            select(CompanyCareerSource).where(
                CompanyCareerSource.canonical_company_key == key
            )
        )
        if record is not None and self._is_healthy(record, now, stale_after, refresh):
            record.last_checked_at = now
            return self._result(record, CompanySourceProvenance.REGISTRY_REUSE)

        provenance = (
            CompanySourceProvenance.REFRESHED_RESOLUTION
            if record is not None
            else CompanySourceProvenance.NEW_RESOLUTION
        )
        try:
            resolution = self._resolver.resolve([target]).results[0]
        except Exception:
            record = self._upsert_failure(
                record, target, key, now, CompanySourceStatus.TEMPORARILY_FAILED,
                "Source resolution request failed.",
            )
            return self._result(record, provenance)

        if resolution.resolved is not None:
            record = self._upsert_resolved(record, key, resolution.resolved, now)
            return self._result(record, provenance)

        source_status = self._status_for_error(resolution.error)
        record = self._upsert_failure(
            record,
            target,
            key,
            now,
            source_status,
            self._safe_diagnostic(resolution.error),
        )
        return self._result(record, provenance)

    @staticmethod
    def _unique_enabled_targets(companies: list[CompanyTarget]) -> list[CompanyTarget]:
        unique: list[CompanyTarget] = []
        seen: set[str] = set()
        for target in companies:
            if not target.enabled:
                continue
            key = canonical_company_key(target.name)
            if key in seen:
                continue
            seen.add(key)
            unique.append(target)
        return unique

    @staticmethod
    def _is_healthy(
        record: CompanyCareerSource,
        now: datetime,
        stale_after: timedelta,
        refresh: bool,
    ) -> bool:
        return (
            not refresh
            and record.status == CompanySourceStatus.RESOLVED
            and bool(record.provider and record.source_token and record.careers_url)
            and now - _as_utc(record.last_checked_at) < stale_after
        )

    def _upsert_resolved(self, record: CompanyCareerSource | None, key: str, resolved, now: datetime) -> CompanyCareerSource:
        if record is None:
            record = CompanyCareerSource(
                company_name=resolved.company,
                canonical_company_key=key,
                provider=resolved.provider,
                source_token=resolved.source_token,
                careers_url=resolved.careers_url,
                status=CompanySourceStatus.RESOLVED,
                diagnostic=None,
                first_seen_at=now,
                last_checked_at=now,
                last_successful_resolution_at=now,
            )
            self._session.add(record)
            return record

        record.company_name = resolved.company
        record.provider = resolved.provider
        record.source_token = resolved.source_token
        record.careers_url = resolved.careers_url
        record.status = CompanySourceStatus.RESOLVED
        record.diagnostic = None
        record.last_checked_at = now
        record.last_successful_resolution_at = now
        return record

    def _upsert_failure(
        self,
        record: CompanyCareerSource | None,
        target: CompanyTarget,
        key: str,
        now: datetime,
        source_status: CompanySourceStatus,
        diagnostic: str,
    ) -> CompanyCareerSource:
        if record is None:
            record = CompanyCareerSource(
                company_name=target.name,
                canonical_company_key=key,
                status=source_status,
                diagnostic=diagnostic,
                first_seen_at=now,
                last_checked_at=now,
            )
            self._session.add(record)
            return record

        record.company_name = target.name
        record.status = source_status
        record.diagnostic = diagnostic
        record.last_checked_at = now
        return record

    @staticmethod
    def _status_for_error(error: str | None) -> CompanySourceStatus:
        if not error:
            return CompanySourceStatus.UNRESOLVED
        if "request failed" in error or "probe failed" in error:
            return CompanySourceStatus.TEMPORARILY_FAILED
        if "No supported source" in error:
            return CompanySourceStatus.UNSUPPORTED
        return CompanySourceStatus.UNRESOLVED

    @staticmethod
    def _safe_diagnostic(error: str | None) -> str:
        return error or "No source resolution result was returned."

    @staticmethod
    def _result(
        record: CompanyCareerSource,
        provenance: CompanySourceProvenance,
    ) -> CompanySourceDiscoveryResult:
        return CompanySourceDiscoveryResult(
            company=record.company_name,
            canonical_company_key=record.canonical_company_key,
            status=CompanySourceStatus(record.status),
            provenance=provenance,
            provider=record.provider,
            source_token=record.source_token,
            careers_url=record.careers_url,
            diagnostic=record.diagnostic,
            first_seen_at=_as_utc(record.first_seen_at),
            last_checked_at=_as_utc(record.last_checked_at),
            last_successful_resolution_at=(
                _as_utc(record.last_successful_resolution_at)
                if record.last_successful_resolution_at
                else None
            ),
            last_successful_fetch_at=(
                _as_utc(record.last_successful_fetch_at)
                if record.last_successful_fetch_at
                else None
            ),
        )


def canonical_company_key(name: str) -> str:
    """Normalize human company labels into a stable registry identity."""
    return " ".join(re.findall(r"[a-z0-9]+", name.casefold()))


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naïve datetime round-trip for deterministic staleness checks."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
