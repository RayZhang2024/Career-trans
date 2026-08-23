from app.providers.jobs.ashby import AshbyJobSource
from app.providers.jobs.base import JobSource
from app.providers.jobs.greenhouse import GreenhouseJobSource
from app.providers.jobs.lever import LeverJobSource
from app.providers.jobs.recruitee import RecruiteeJobSource
from app.providers.jobs.smartrecruiters import SmartRecruitersJobSource
from app.schemas.job_sources import ResolvedJobSource


def create_job_source(resolved: ResolvedJobSource) -> JobSource:
    """Create a discovery adapter from a resolver result without environment config."""

    if resolved.provider == GreenhouseJobSource.name:
        return GreenhouseJobSource([resolved.source_token], company=resolved.company)
    if resolved.provider == AshbyJobSource.name:
        return AshbyJobSource([resolved.source_token], company=resolved.company)
    if resolved.provider == LeverJobSource.name:
        return LeverJobSource([resolved.source_token], company=resolved.company)
    if resolved.provider == SmartRecruitersJobSource.name:
        return SmartRecruitersJobSource([resolved.source_token], company=resolved.company)
    if resolved.provider == RecruiteeJobSource.name:
        return RecruiteeJobSource([resolved.source_token], company=resolved.company)
    raise ValueError(f"Unsupported job source provider: {resolved.provider}")
