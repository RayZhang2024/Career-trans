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
top-level `metadata.application_attempt` (or compatible wrapped metadata),
distinguishing the application retry from provider/SDK behavior.

The parser also accepts the single-run export shape used by Issue #106, where
safe fields are top-level `metadata`, `outputs`, `error`, and `langsmith`
objects. Stage/model names are read from explicit safe metadata/output fields;
`outputs.usage_metadata.input_token_details.cache_read` and
`outputs.usage_metadata.output_token_details.reasoning` are supported, as are
`outputs.created_at`/`completed_at` timestamps.

LangSmith UI downloads can omit the displayed provider run name. In that case,
provide the exact stage label during the development-only normalization step:

```powershell
.\.venv\Scripts\career-trans.exe dev normalize-llm-traces `
  --stage job_relevance=job_relevance.json `
  --stage job_archetype=job_archetype.json `
  --stage job_extraction=job_extraction.json `
  --stage requirement_matching=requirement_matching.json `
  --stage career_alignment=career_alignment.json `
  --output normalized-trace.json
```

`--stage` is repeatable, including for the same stage. Preserve each provider
export when collecting a multi-job benchmark or a requirement-matching retry:

```powershell
.\.venv\Scripts\career-trans.exe dev normalize-llm-traces `
  --stage job_relevance=job-1-relevance.json `
  --stage job_relevance=job-2-relevance.json `
  --stage requirement_matching=matching-attempt-1.json `
  --stage requirement_matching=matching-attempt-2.json `
  --funnel rank-diagnostics.json `
  --output normalized-trace.json
```

The optional `--funnel` (also accepted as `--diagnostics`) reads a ranking
response or diagnostics export and retains only the numeric funnel counters and
derived semantic-screening counts. Job listings, candidate context, prompts,
evidence, and other payloads are never copied. Run `analyse-llm-trace` on the
combined file to report provider usage alongside the funnel.

Only safe stage/model/usage/timestamp/status fields are retained in the
normalized file. The five exact stage labels above are the complete supported
set; no substring or orchestration-name inference is used for the other
stages. Run `analyse-llm-trace` on `normalized-trace.json` afterwards. A root
run JSON download without hydrated children or explicit provider exports cannot
represent the complete end-to-end funnel.

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

### Authoritative warm-cache 1-job baseline

The first controlled warm-cache benchmark was recorded at revision `5f1cc05`.
All five provider calls used `gpt-5.6-luna`; the values below are the sanitized
provider export totals and are locked by an offline regression fixture (no paid
benchmark is run by the test):

| Stage | Input | Cached input | Output | Reasoning | Total |
|---|---:|---:|---:|---:|---:|
| `job_relevance` | 1,829 | 1,826 | 153 | 0 | 1,982 |
| `job_archetype` | 1,259 | 1,256 | 174 | 117 | 1,433 |
| `job_extraction` | 2,331 | 2,328 | 3,226 | 361 | 5,557 |
| `requirement_matching` | 4,675 | 0 | 3,025 | 708 | 7,700 |
| `career_alignment` | 5,464 | 0 | 1,008 | 134 | 6,472 |
| **Total** | **15,558** | **5,410** | **7,586** | **1,320** | **23,144** |

The summed provider-stage time was approximately 63 seconds. The end-to-end
wall-clock span, from the earliest `job_relevance` creation to the final
`career_alignment` completion, was approximately 66 seconds. These are
observational baselines, not targets or optimization claims.

The deterministic test fixture contains ten jobs: two objective URL gate
failures, two relevance rejections, one relevant incomplete job, and five
complete relevant jobs. Fake relevance/archetype agents and a fake graph verify
that all eight survivors are screened, six are archetyped, the incomplete job
does not consume a deep-analysis slot, and a two-job deep-analysis cap is
respected.
