# Local LangSmith tracing

LangSmith tracing is disabled by default. The existing OpenAI client wrappers
continue to create provider spans when tracing is explicitly enabled; this
configuration does not add traces to read-only endpoints or locally executed
Codex/Ollama operations.

## Configure Compose

Compose reads the repository-root `.env` file for interpolation. It does not
automatically read `backend/.env`, and the backend image does not copy that
file. Start from the safe, blank-key values in the root `.env.example`:

```dotenv
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=career-trans-dev
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_WORKSPACE_ID=
```

To opt in, set `LANGSMITH_TRACING=true` and put your LangSmith key only in the
root local `.env`. Set `LANGSMITH_PROJECT` to the project you plan to inspect.
The example endpoint is LangSmith's US SaaS endpoint; for an EU account set
`LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`. `LANGSMITH_ENDPOINT`
is passed through unchanged to the backend, so use the endpoint for the
selected organization/region. Organization-scoped API keys require
`LANGSMITH_WORKSPACE_ID`; leave it blank for other keys. LangSmith documents
the tracing variables, EU endpoint, and workspace-ID requirement in its
[Python SDK setup](https://github.com/langchain-ai/langsmith-sdk/blob/main/python/README.md).

After editing the root `.env`, recreate the backend so Compose supplies the
new environment. This preserves the named SQLite volume:

```powershell
docker compose up -d --build backend
```

Do not use `docker compose down -v` for this configuration change. The
frontend service has no LangSmith variables, and the key is not passed to
frontend build arguments or the browser.

## Verify configuration without revealing credentials

The backend logs a startup status only when tracing is enabled. It includes the
project, endpoint origin, whether a key is configured, and whether a workspace
ID is configured. It never prints key/workspace values, environment contents,
prompts, CVs, candidate context, or job text. A configured key is not proof of
successful trace delivery.

You can inspect the same sanitized status inside the running backend:

```powershell
docker compose exec backend python -c "from pprint import pprint; from app.core.config import get_settings, langsmith_configuration_status; pprint(langsmith_configuration_status(get_settings()))"
```

If tracing is off, startup continues without a LangSmith key. If tracing is on
and the key is missing, the backend emits a warning that trace delivery is
unavailable. Review only the LangSmith diagnostic line in recent backend logs;
do not print or paste the container environment or `.env` values.

## Verify trace visibility

First verify the selected project and workspace in LangSmith. Then, only after
explicitly opting in, send one provider-free synthetic trace with an inert
public value from inside the backend container:

```python
from langsmith import traceable


@traceable(name="career-trans-operator-smoke")
def smoke_trace(value: str) -> str:
    return f"received {value}"


smoke_trace("synthetic connectivity check")
```

Allow the SDK time to flush the trace, then look for the new
`career-trans-operator-smoke` run in the configured project. This verifies
basic trace ingestion only; it does not verify OpenAI provider spans. To check
the existing `wrap_openai` spans, an operator must explicitly approve and run
one real OpenAI-backed semantic operation with safe synthetic input. That
operation may incur provider cost, so it is intentionally absent from tests
and this implementation's validation.

Only new LLM-backed calls can create new OpenAI spans. Search History, Results,
Saved, other read-only requests, reused evaluations that make no model call,
and host-side Local Codex CLI operations are not evidence of tracing failure.
Existing runs are not retroactively traced. Avoid broad tracing of sensitive
candidate/job payloads unless the operator has explicitly chosen that privacy
trade-off.
