import pytest
from pydantic import ValidationError

from app.schemas.job import (
    JobProfile,
    JobRequirement,
    RequirementCategory,
    RequirementImportance,
)


def test_job_profile_schema_accepts_structured_requirements() -> None:
    profile = JobProfile(
        title="Data Engineer",
        requirements=[
            JobRequirement(
                text="Python",
                importance=RequirementImportance.ESSENTIAL,
                category=RequirementCategory.TECHNICAL,
            )
        ],
    )

    assert profile.requirements[0].text == "Python"
    assert profile.requirements[0].importance == RequirementImportance.ESSENTIAL


def test_job_profile_schema_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        JobProfile.model_validate(
            {
                "title": "Data Engineer",
                "invented_field": "not allowed",
            }
        )
