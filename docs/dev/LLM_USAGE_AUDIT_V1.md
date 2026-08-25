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

The parser also accepts the single-run export shape used by Issue #106, where
safe fields are top-level `metadata`, `outputs`, `error`, and `langsmith`
objects. Stage/model names are read from explicit safe metadata/output fields;
`outputs.usage_metadata.input_token_details.cache_read` and
`outputs.usage_metadata.output_token_details.reasoning` are supported, as are
`outputs.created_at`/`completed_at` timestamps.

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

The deterministic test fixture contains ten jobs: two objective URL gate
failures, two relevance rejections, one relevant incomplete job, and five
complete relevant jobs. Fake relevance/archetype agents and a fake graph verify
that all eight survivors are screened, six are archetyped, the incomplete job
does not consume a deep-analysis slot, and a two-job deep-analysis cap is
respected.
