# Local Codex browser job discovery

## Implementation diagnosis

The browser Jobs path already resolves a user-scoped provider through
`JobDiscoverySettingsService` and calls the shared `WebSearchProvider` contract.
The separate advanced `jobs discover-external` / `jobs hunt` path calls
`CodexExternalDiscoveryRunner`, consumes Career-trans search context, imports
discovered jobs and may run ranking. Browser Local Codex now shares only the
low-level runtime adapter; those higher-level flows remain separate.

The implementation host's installed CLI was `codex-cli 0.155.0-alpha.9.2`.
Its help exposed `--search` as live web search using `web_search`; `codex exec
--help` exposed `--output-schema`, `--output-last-message`, and stdin prompts.
`codex login status` was supported and reported signed out. Accordingly the
adapter uses `codex -m <model> --search exec --output-schema <temp-schema>
--output-last-message <temp-result> -`, with the prompt on stdin, and the
explicit deployment-owned `LOCAL_CODEX_SEARCH_MODEL` (default
`gpt-5.6-luna`). Authentication is probed through `codex login status`; it is
not treated as proof that live search or quota works. The supported exec
interface made app-server unnecessary. No live Local Codex call was made on
the implementation host because its Codex session was signed out.

The existing database constraint allows only Tavily, OpenAI and Disabled.
Fresh schema and the Issue #256 base migration now include `local_codex`;
existing SQLite databases use a transactional, foreign-key-checked table
rebuild that copies every setting and timestamp. PostgreSQL deployments have a
constraint-only migration. HTTP/manual discovery and schedule Run now use the
backend request context. Automatic due-runner execution requires the explicit
`supported` capability setting; it defaults to `unverified` and fails closed.

The Settings -> Job Discovery page can select **Local Codex** for the normal
browser discovery pipeline. It implements the existing web-search provider
contract; Career-trans still opens and extracts vacancy pages. It does not use
the advanced external-agent import workflow and does not need a Career-trans
API token or base URL.

Local Codex runs under the OS account and environment of the Career-trans
backend host. Enabling it with `LOCAL_CODEX_DISCOVERY_ENABLED=true` is a
deployment-wide trust decision: eligible Career-trans users can consume the
Codex session/subscription available to that host account. This is intended for
trusted/local deployments unless the operator explicitly accepts that shared
host model. Career-trans never stores Codex credentials or shows the host
account identity.

Set `LOCAL_CODEX_SEARCH_MODEL` (default `gpt-5.6-luna`) to choose the explicit
Codex model used for browser search. It is independent of Career-trans semantic
models and `CODEX_EXTERNAL_DISCOVERY_MODEL`. Automatic due-runner use remains
fail-closed by default; only set
`LOCAL_CODEX_SCHEDULED_DISCOVERY_CAPABILITY=supported` after verifying the
scheduled process runs with the intended OS account, PATH, CLI and Codex
session. `unverified` is the default.

Existing SQLite databases must run
`backend/migrations/20261001_local_codex_provider_sqlite.py <DATABASE_URL>`
from the backend environment before selecting `local_codex`; PostgreSQL
deployments should apply `20261001_local_codex_provider_postgresql.sql`.

The Settings status endpoint performs bounded local CLI capability checks. The
explicit **Test Local Codex** action performs one live search and may consume
Codex usage. Neither action persists jobs or alters schedules.
