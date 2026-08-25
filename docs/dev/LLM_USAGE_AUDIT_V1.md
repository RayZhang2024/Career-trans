# LLM Usage Audit V1

Issue #107 establishes a privacy-safe baseline without changing ranking behavior.

## Necessity Gate

The ranking funnel already has separate LangSmith names for `job_relevance`,
`job_archetype`, `job_extraction`, `requirement_matching`, and
`career_alignment`. Requirement-matching application attempts are represented by
safe metadata on the existing provider run after Issue #106. No production
LangSmith API dependency or additional semantic instrumentation is required.

## Offline trace summary

Export a LangSmith trace as JSON, then run:

```powershell
.\.venv\Scripts\career-trans.exe dev analyse-llm-trace --input trace.json
```

The output contains only stage names, model names, numeric token/timing counters,
success/failure counts, retry counts, and funnel counters. It never copies raw
inputs, outputs, prompts, evidence, IDs, credentials, or provider bodies.

The parser accepts a run list or an object containing `runs`, `child_runs`, or
`children`. Token fields are read from common LangSmith/OpenAI usage names;
missing fields remain zero. Requirement-matching retries are counted from
`extra.metadata.application_attempt`, distinguishing the application retry from
provider/SDK behavior.

## Canonical benchmark shapes

Use the same deterministic persisted jobs and candidate context for both runs:

| Benchmark | Input | Semantic cap | Deep-analysis cap | Purpose |
|---|---:|---:|---:|---|
| single-job baseline | 1 complete job | 1 | 1 | stage cost and latency |
| funnel baseline | 10-job fixture | 10 | 2 | gate/screen/finalist conversion |

Before accepting a live result, start the backend from the tested checkout and
record `git rev-parse --short HEAD`. The LangSmith `revision_id` must match that
value. The HTTP-only CLI talks to the running server, so an old process can make
trace attribution invalid.

Record per stage: calls, model, input, cached input, output, reasoning, total
tokens, latency, successes, failures, and retries. Also record input, gated-out,
relevance-screened, archetyped, finalists, and deeply analysed counts. Repeat an
identical benchmark where practical and compare cache reads, call counts, and
judgement stability. This issue intentionally does not optimize any result.
