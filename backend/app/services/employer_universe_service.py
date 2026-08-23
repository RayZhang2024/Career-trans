from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_career_source import CompanyCareerSource
from app.schemas.employer_universe import (
    EmployerUniverseCompany,
    EmployerUniverseEntry,
    EmployerUniverseProvenance,
    EmployerUniverseRequest,
    EmployerUniverseResponse,
)
from app.schemas.job_sources import CompanyTarget
from app.services.company_source_discovery_service import canonical_company_key


class EmployerUniverseService:
    """Deterministically build CompanyTarget inputs without resolving sources."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def build(self, request: EmployerUniverseRequest) -> EmployerUniverseResponse:
        excluded_keys = {
            canonical_company_key(name)
            for name in request.excluded_companies
            if canonical_company_key(name)
        }
        records = self._inputs(request)
        input_count = len(records)
        disabled_count = sum(not target.enabled for target, _ in records)
        source_counts: dict[str, int] = {}
        unique: dict[str, EmployerUniverseCompany] = {}
        deduplicated_count = 0
        excluded_count = 0

        for target, provenance in records:
            if not target.enabled:
                continue
            key = canonical_company_key(target.name)
            if key in excluded_keys:
                excluded_count += 1
                continue
            source_counts[provenance.source] = source_counts.get(provenance.source, 0) + 1
            existing = unique.get(key)
            if existing is None:
                unique[key] = EmployerUniverseCompany(
                    company=target,
                    canonical_company_key=key,
                    provenance=[provenance],
                )
                continue
            deduplicated_count += 1
            if provenance not in existing.provenance:
                existing.provenance.append(provenance)

        companies = list(unique.values())[: request.max_companies]
        return EmployerUniverseResponse(
            companies=companies,
            input_count=input_count,
            disabled_count=disabled_count,
            deduplicated_count=deduplicated_count,
            excluded_count=excluded_count,
            output_count=len(companies),
            source_counts=source_counts,
        )

    def _inputs(
        self, request: EmployerUniverseRequest
    ) -> list[tuple[CompanyTarget, EmployerUniverseProvenance]]:
        watched = [
            (target, EmployerUniverseProvenance(source="watched"))
            for target in request.watched_companies
        ]
        imported = [
            (
                self._target_from_entry(entry),
                EmployerUniverseProvenance(
                    source=f"imported:{entry.source}", source_ref=entry.source_ref
                ),
            )
            for entry in request.imported_entries
        ]
        registry = list(self._registry_inputs()) if request.include_registry else []
        return [*watched, *imported, *registry]

    def _registry_inputs(
        self,
    ) -> Iterable[tuple[CompanyTarget, EmployerUniverseProvenance]]:
        records = self._session.scalars(
            select(CompanyCareerSource).order_by(CompanyCareerSource.canonical_company_key)
        )
        for record in records:
            yield (
                CompanyTarget(name=record.company_name),
                EmployerUniverseProvenance(source="registry", source_ref=record.id),
            )

    @staticmethod
    def _target_from_entry(entry: EmployerUniverseEntry) -> CompanyTarget:
        return CompanyTarget(
            name=entry.name,
            website=entry.website,
            slug=entry.slug,
            priority=entry.priority,
            enabled=entry.enabled,
        )
