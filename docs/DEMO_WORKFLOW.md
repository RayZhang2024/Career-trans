# Demo End-to-End Workflow

## Purpose

`POST /api/v1/demo/analyse-and-match` is a development/evaluation endpoint for exercising the current Career Agent intelligence chain with one request.

It is not the intended production candidate-data path.

## Flow

```text
job_text
   |
   v
JobAnalysisService
   |
   v
JobProfile
   |
   +-------------------------------+
   |                               |
   v                               v
ray_demo Markdown          RequirementMatchingService
   |                               ^
   v                               |
CandidateContext ------------------+
   |
   +------------------------------+
   |                              |
   v                              v
RequirementMatch[]        CareerAlignmentAgent
   |                              ^
   v                              |
FitAssessment --------------------+
                                  |
                                  v
                         CareerAssessmentService
                                  |
                                  v
                         CareerAssessment
```

The endpoint returns:

- `candidate_source`
- `evidence_count`
- structured `job_profile`
- requirement-level `matches`
- deterministic `fit_assessment`
- `career_assessment` with six dimensions, a deterministic weighted score, and
  explicit confidence

## Why this endpoint exists

The generic `/jobs/match` endpoint accepts a complete `CandidateContext`, which is useful as an application boundary but cumbersome for manual Swagger testing.

The demo endpoint loads `resources/examples/ray_demo/` internally so a developer can test extraction and matching using only a job description.

## Production boundary

Do not use repository demo files as production user data.

When production profile ingestion is implemented, authenticated user data will be assembled into the same `CandidateContext` schema and passed to the same matching service.

## Local test

Run the backend:

```powershell
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

Then call:

```text
POST /api/v1/demo/analyse-and-match
```

with:

```json
{
  "job_text": "<paste a real job description here>"
}
```

A configured `OPENAI_API_KEY` is required for the real endpoint. Job extraction,
semantic requirement matching, and career alignment use replaceable OpenAI-backed
components; deterministic services validate and aggregate their outputs. Automated
tests use fake AI components and make no OpenAI calls.
