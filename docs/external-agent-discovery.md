# External runtime job discovery

Career-trans accepts factual public-vacancy evidence from an authenticated external coding-agent runtime. The server does not start Codex, Qwen, Claude Code, a browser, or a shell process.

## Codex-first V1 flow

1. Authenticate to Career-trans and request `POST /api/v1/jobs/external-discovery/search-context` with the discovery constraints.
2. Use the returned compact profile and constraints in Codex (or another compatible runtime) to search public vacancy pages using that runtime's own tools.
3. Submit supported vacancy facts to `POST /api/v1/jobs/import-discovered` with the same constraints and a runtime identifier such as `codex`.
4. Career-trans validates HTTP(S) URLs, canonicalizes, applies hard policy constraints, deduplicates, caps, and persists accepted jobs in the shared job universe.
5. Call the existing ranking flow separately if semantic analysis is wanted.

The import endpoint never invokes OpenAI web search and imported jobs are non-authoritative: a later omission or runtime failure cannot mark a prior imported job inactive.

Only factual fields belong in an import: vacancy title, company, location, source URL, description where available, posted date, employment type, work arrangement, and short source provenance. Career-trans retains bounded runtime, `source_ref`, and `discovered_via` evidence against the shared discovered-job record. Do not send prompts, credentials, hidden reasoning, or agent scratchpad content.
