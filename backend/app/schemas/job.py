from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RequirementImportance(StrEnum):
    ESSENTIAL = "essential"
    DESIRABLE = "desirable"
    UNSPECIFIED = "unspecified"


class RequirementCategory(StrEnum):
    TECHNICAL = "technical"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    DOMAIN = "domain"
    LEADERSHIP = "leadership"
    CUSTOMER = "customer"
    COMMUNICATION = "communication"
    LOCATION = "location"
    WORK_AUTHORIZATION = "work_authorization"
    SECURITY = "security"
    OTHER = "other"


class JobRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    importance: RequirementImportance = RequirementImportance.UNSPECIFIED
    category: RequirementCategory = RequirementCategory.OTHER
    source_text: str | None = None


class JobProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    company: str | None = None
    location: str | None = None
    work_arrangement: str | None = None
    seniority: str | None = None
    salary: str | None = None
    employment_type: str | None = None
    application_deadline: str | None = None

    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[JobRequirement] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    domain_knowledge: list[str] = Field(default_factory=list)
    security_requirements: list[str] = Field(default_factory=list)
    work_authorization_requirements: list[str] = Field(default_factory=list)


class JobAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_text: str = Field(
        min_length=50,
        max_length=50_000,
        description="Raw job description text to analyse.",
    )


class JobAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_profile: JobProfile
