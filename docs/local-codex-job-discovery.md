# Local Codex browser job discovery

## Implementation diagnosis

- Browser discovery already resolves the authenticated user's explicit provider through `JobDiscoverySettingsService`, then passes a `WebSearchProvider` into `AgenticJobDiscoveryService`. That pipeline owns search strategy, page fetching, extraction, normalization, deduplication, and persistence.
- The advanced `career-trans jobs discover-external` and `career-trans jobs hunt` flows run `CodexExternalDiscoveryRunner` on the CLI host. They obtain a compact candidate search context through the Career-trans API, invoke Codex, then use the existing external import and optional ranking APIs. They remain a separate workflow.
- The implementation host resolves `codex` from PATH to the installed Codex CLI 0.155.0-alpha.9.2. The installed help reports `codex exec`, global `--search` for live native web search, `exec --output-schema`, `--output-last-message`, and `-` for a prompt supplied on stdin. `codex login status` is supported; at implementation time it reports signed out. The probe suppresses account identity and raw command output.
- The selected structured invocation is `codex -m <explicit-model> --search exec --output-schema <schema-file> --output-last-message <result-file> -`, with the task piped on stdin, `shell=False`, a temporary working directory, a bounded timeout, and a sanitized environment. The supported CLI help exposes the required features, so an app-server is not needed.
- Runtime readiness uses bounded CLI installation/version/help probes and `codex login status`; it never reads Codex credential files. Login status proves only that Codex reports an authenticated session, not that search, network, model, or quota is available. Settings' Test Local Codex action is the live search check.
- Browser discovery uses a distinct deployment-owned `LOCAL_CODEX_SEARCH_MODEL`, defaulting to `gpt-5.6-luna`, matching the repository's existing Codex model default. It is separate from both semantic AI settings and `CODEX_EXTERNAL_DISCOVERY_MODEL`.
- Fresh databases receive `local_codex` through SQLAlchemy metadata. Existing SQLite databases use an explicit table-rebuild migration that preserves every settings column and row while replacing only the provider CHECK constraint; the user foreign key and cascade behavior are retained. PostgreSQL deployments use the companion constraint-only SQL migration.
- Manual Jobs discovery and schedule Run now use the HTTP backend's cheap readiness context. Automatic due-runner execution is a separate context and defaults to `unverified`; it fails closed before semantic work unless the operator explicitly attests that the scheduled process has compatible Codex executable, OS account, authentication, and environment.

## Deployment and trust boundary

Local Codex runs under the Career-trans backend host's OS account and environment; it does not use the browser user's local CLI unless the backend itself runs there. Enabling `LOCAL_CODEX_DISCOVERY_ENABLED=true` lets eligible authenticated Career-trans users consume the backend host's Codex session and subscription. Use this only in a trusted/local deployment unless the operator accepts that shared-host trust model. Career-trans does not create per-user Codex identities, read or store Codex credentials, or show the host account identity.

Set `LOCAL_CODEX_DISCOVERY_ENABLED=true` to opt in. The default is false. `LOCAL_CODEX_SEARCH_MODEL` defaults to `gpt-5.6-luna` and can be set to another valid Codex model identifier. Leave `LOCAL_CODEX_SCHEDULED_DISCOVERY_CAPABILITY=unverified` unless the scheduled due-runner context has been checked; only explicitly setting it to `supported` enables automatic Local Codex schedule runs. `unsupported` keeps those runs disabled.

The HTTP backend and `python -m app.scheduled_discovery_runner` may run with different PATH, OS accounts, home directories, and Codex sessions. A Settings test proves only the HTTP context. The current Compose backend does not install Codex; enabling Local Codex in that container alone cannot make the CLI available.

The advanced `jobs discover-external` and `jobs hunt` commands remain host-side workflows using their existing Career-trans token/base URL, external search-context/import, and `CODEX_EXTERNAL_DISCOVERY_MODEL` behavior.
