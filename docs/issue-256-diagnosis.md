# Issue #256 diagnosis — Job Discovery provider settings

**Inspection base:** `1e54e8d14bf06cb17479987c6063970f6d2b9883` (`origin/main`); merged PR #255 (`da036eee2aec5f9ea92da3714e558cdd051c55ea`) is an ancestor.

This diagnosis was completed before production-code changes.

## Existing provider seam

`backend/app/providers/web_search.py` defines `WebSearchProvider.search(query, limit) -> list[SearchResult]`. `SearchResult` is provider-neutral (`title`, `snippet`, `url`, `domain`, `rank`). OpenAI Web Search returns citations mapped to this schema; Brave makes a bounded HTTP request and maps normalized results into the same schema. This synchronous query/limit seam is sufficient for Tavily Basic Search. No parallel provider hierarchy or Tavily-specific downstream contract is needed.

## Current provider construction and resolution

`backend/app/api/deps.py:get_agentic_web_search_provider(settings)` resolves only the deployment `AGENTIC_SEARCH_PROVIDER` (`disabled`, `openai`, or `brave`) and the corresponding deployment credential. `_build_agentic_job_discovery_service` calls it before constructing strategy-generation and vacancy-extraction clients, which provides the existing fail-fast-before-semantic-work property. `get_settings()` is cached deployment configuration; it is not user scoped.

Manual routes differ:

- `/jobs/discover-agentic` is the low-level explicit-context route and uses `get_agentic_job_discovery_service`, which resolves the deployment provider.
- Authenticated `/jobs/discover-agentic-me` obtains the current candidate and semantic runtime, but `get_user_agentic_job_discovery_service` still builds the search provider solely from deployment settings. There is no per-user Job Discovery provider setting today.

Scheduled execution differs by entry point:

- HTTP run-now obtains `ScheduledDiscoveryExecutionService` from `get_scheduled_discovery_execution_service`. It resolves the owner's semantic `ResolvedRuntimeSnapshot`, then invokes an agentic factory that accepts only that snapshot and builds search from global deployment settings. The execution service knows `execution.user_id`, but does not pass it to the factory.
- `backend/app/scheduled_discovery_runner.py:build_service` supplies a lazy factory that calls `deps.get_agentic_job_discovery_service(session)`. It also uses deployment provider settings only and has no user-scoped resolver. Due execution resolves semantic settings by owner in the execution service, but search settings are global.

The common execution service creates agentic discovery lazily when the schedule's agentic channel is enabled. A user-scoped provider must therefore be resolved once per execution, using the execution owner, and the resolved instance/config must be held for the full in-flight run. Schedule configuration snapshots currently contain schedule/query/acquisition/evaluation only; provider settings should remain execution-time state, not be frozen in a schedule.

## Discovery pipeline and semantic-provider boundary

`AgenticJobDiscoveryService.discover` runs `SearchStrategyGenerator`, calls `WebSearchProvider`, opens selected result pages, uses vacancy metadata or `PageVacancyExtractor`, normalizes listings, applies deterministic hard screening and deduplication, then persists accepted listings through `SqlAlchemyDiscoveredJobStateStore`. Scheduled execution subsequently runs the existing user discovery ranking/evaluation pipeline.

Search strategy generation, fallback page-vacancy extraction, and later relevance/ranking/analysis are semantic LLM operations configured through the independent AI runtime. Choosing Tavily changes the web URL/result search provider only; it does not imply that semantic AI calls stop. Provider readiness must remain checked before those semantic operations begin.

## Existing Settings domain

`UserAiSettings` (`user_ai_settings`) stores semantic provider preferences and `preferences_json`; its API and schemas are deliberately credential-free. `AiSettingsService` uses a per-user revision and optimistic compare-and-swap update. The frontend currently has `/settings/ai` and `AiSettingsPage`, with one Settings entry in the main navigation. Job Discovery settings should be a separate model/schema/service/API and a distinct `/settings/discovery` surface, with shared Settings navigation, not additional fields on `UserAiSettings` or its JSON.

## Persistence and migration

SQLAlchemy models are registered from `backend/app/models/__init__.py`; test fixtures create a fresh schema through `Base.metadata.create_all`. The repository uses additive SQL files under `backend/migrations/` and tests migration text/application explicitly where useful. User-owned tables use `user_id` foreign keys with `ON DELETE CASCADE`. New settings and credential tables can therefore be registered for fresh test/local schemas and paired with an explicit additive SQL migration compatible with the repository's SQLite tests and PostgreSQL target. Existing users need no row/backfill; absence means inherit deployment configuration. Existing schedule records need no provider snapshot migration.

## Credential-storage capability and selected design

Inspection found no encrypted credential store, OS credential integration, server-side secret service, or separately managed encryption-key mechanism. `EnvironmentCredentialResolver` in `backend/app/providers/llm.py` only resolves deployment-provided semantic credentials; it does not persist user credentials. The JWT signing key is not an appropriate encryption key.

A per-user Tavily key cannot be placed in an ordinary plaintext column. The focused design is a separate user-owned credential table holding only an AES-GCM nonce and ciphertext, with `user_id` as authenticated associated data. Encryption uses a required, separately provisioned `TAVILY_CREDENTIAL_ENCRYPTION_KEY` (base64-encoded 32-byte key), distinct from JWT material and outside the database. The API will not return ciphertext or plaintext. Missing encryption configuration prevents saving/using an encrypted user credential with a bounded safe configuration error; it does not prevent Tavily from using a deployment `TAVILY_API_KEY`. Replacing the encryption key makes existing ciphertext unavailable until the credential is re-entered; this behavior must fail closed and be documented rather than trying plaintext or another key. Provider instances remain request/execution scoped and are never globally cached with a user's decrypted key.

## Implementation boundary

Use the current provider seam; add a separate revisioned Job Discovery settings domain and write-only encrypted credential API; resolve provider/credential precedence once for the authenticated user or schedule owner; preserve deployment-only Brave and low-level deployment-default endpoints; record provider/credential-source/search-depth audit metadata on scheduled execution records (never credentials); update HTTP run-now and standalone due-runner factories to share the user-scoped resolver. No schedule snapshot, execution history, logs, errors, AI settings, or frontend storage may contain the key.
