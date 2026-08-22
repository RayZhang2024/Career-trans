from app.providers.jobs.probes.base import (
    JobSourceProbe,
    JobSourceProbeError,
    derive_slug,
    require_safe_slug,
)
from app.schemas.job_sources import (
    AtsResolutionResponse,
    CompanySourceResolution,
    CompanyTarget,
)


class AtsResolverService:
    """Resolve company targets to verified, supported public ATS sources."""

    def __init__(self, probes: list[JobSourceProbe]) -> None:
        self._probes = probes

    def resolve(self, companies: list[CompanyTarget]) -> AtsResolutionResponse:
        return AtsResolutionResponse(results=[self._resolve_company(company) for company in companies])

    def _resolve_company(self, company: CompanyTarget) -> CompanySourceResolution:
        try:
            slug = company.slug or derive_slug(company.name)
            require_safe_slug(slug)
        except ValueError as exc:
            return CompanySourceResolution(company=company.name, error=str(exc))

        attempted: list[str] = []
        failures: list[str] = []
        for probe in self._probes:
            attempted.append(probe.name)
            try:
                resolved = probe.probe(company, slug)
            except JobSourceProbeError:
                failures.append(f"{probe.name}: request failed")
                continue
            except Exception:
                failures.append(f"{probe.name}: probe failed")
                continue
            if resolved is not None:
                return CompanySourceResolution(
                    company=company.name,
                    resolved=resolved,
                    attempted_providers=attempted,
                )

        error = "; ".join(failures) if failures else "No supported source with published jobs was found."
        return CompanySourceResolution(
            company=company.name,
            attempted_providers=attempted,
            error=error,
        )
