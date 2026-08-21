# Career Agent — Job Analysis V1 Integration Guide

This package is an overlay for the existing `D:\career-trans` repository.

## What this milestone adds

```text
Raw job description
        |
        v
POST /api/v1/jobs/analyse
        |
        v
JobAnalysisService
        |
        v
JobExtractor interface
        |
        +--> OpenAIJobExtractor
        |
        v
typed JobProfile
        |
        +--> responsibilities
        +--> requirements
        +--> essential/desirable importance
        +--> technical skills
        +--> eligibility/security information
```

No candidate matching is performed yet.

## Files that can be copied directly

Copy:

- `backend/app/schemas/job.py`
- `backend/app/agents/job_extraction.py`
- `backend/app/services/job_analysis_service.py`
- `backend/app/api/routes/jobs.py`
- `backend/tests/test_jobs.py`
- `backend/tests/test_job_schema.py`
- `prompts/job_extraction.md`

## Existing files to edit

The package deliberately supplies snippets instead of blindly replacing existing shared files.

### 1. `backend/app/api/deps.py`

Merge the contents of:

`backend/app/api/deps_job_analysis_snippet.py`

Do not remove the existing authentication/database dependencies.

### 2. `backend/app/api/router.py`

Merge:

`backend/app/api/router_job_snippet.py`

### 3. `backend/app/core/config.py`

Add the two fields from:

`backend/app/core/config_job_snippet.py`

to the existing `Settings` class.

### 4. `backend/.env.example`

Append:

`backend/.env.example_additions.txt`

Then add your real key to `backend/.env`, never `.env.example`:

```text
OPENAI_API_KEY=...
OPENAI_JOB_EXTRACTION_MODEL=gpt-5.6-luna
```

### 5. `backend/pyproject.toml`

Add:

```toml
"openai>=1.0",
```

to the runtime dependency list.

Then reinstall:

```powershell
cd D:\career-trans\backend
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Test

```powershell
python -m pytest
```

The job-analysis tests use a fake extractor and therefore make no OpenAI calls.

## Run

```powershell
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

Try:

`POST /api/v1/jobs/analyse`

with a real job description.

## Why no LangGraph yet?

This is one semantic transformation:

```text
job text -> JobProfile
```

A graph would add complexity without useful orchestration. LangGraph becomes valuable when the workflow gains multiple stateful stages such as:

```text
job extraction
    -> candidate retrieval
    -> evidence matching
    -> fit scoring
    -> career assessment
    -> routing
```

## Why no database persistence yet?

The purpose of this milestone is to validate the intelligence boundary and schema first.

Persistence can be added after we are satisfied that `JobProfile` and `JobRequirement` contain the right information.
