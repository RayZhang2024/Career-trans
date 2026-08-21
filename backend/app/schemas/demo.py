from pydantic import BaseModel, ConfigDict, Field

from app.schemas.assessment import FitAssessment
from app.schemas.career_assessment import CareerAssessment
from app.schemas.job import JobProfile
from app.schemas.matching import RequirementMatch


class DemoAnalyseAndMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_text: str = Field(
        min_length=50,
        max_length=50_000,
        description="Raw job-description text for the demo workflow.",
    )


class DemoAnalyseAndMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_source: str
    evidence_count: int = Field(ge=0)
    job_profile: JobProfile
    matches: list[RequirementMatch]
    fit_assessment: FitAssessment
    career_assessment: CareerAssessment
