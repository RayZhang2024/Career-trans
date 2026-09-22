from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_compose_uses_an_absolute_sqlite_database_in_a_named_volume() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "DATABASE_URL: sqlite:////data/career_agent.db" in compose
    assert "career_agent_data:/data" in compose
    assert "career_agent_data:" in compose
    assert "OPENAI_API_KEY: ${OPENAI_API_KEY:-}" in compose


def test_frontend_runtime_is_same_origin_proxy_with_spa_fallback_and_upload_limit() -> None:
    nginx = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    frontend_dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "proxy_pass http://backend:8000/api/;" in nginx
    assert "root /usr/share/nginx/html;" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "client_max_body_size 20m;" in nginx
    assert "proxy_read_timeout 600s;" in nginx
    assert "VITE_API_BASE_URL=\"\"" in frontend_dockerfile
    assert "OPENAI_API_KEY" not in frontend_dockerfile


def test_docker_build_contexts_exclude_local_secrets_data_and_codex_state() -> None:
    root_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    frontend_ignore = (ROOT / "frontend" / ".dockerignore").read_text(encoding="utf-8")

    for required in (".env", "!.env.example", ".codex", "**/.venv", "**/*.db", "**/.tmp"):
        assert required in root_ignore
    for required in (".env", "node_modules", "dist", "*.db"):
        assert required in frontend_ignore


def test_backend_container_runs_the_existing_application_without_codex() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"' in dockerfile
    assert "COPY prompts /prompts" in dockerfile
    assert "codex" not in dockerfile.lower()
    assert ".codex" not in compose


def test_backend_package_is_copied_before_the_project_is_installed() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.index("COPY backend/app ./app") < dockerfile.index("RUN pip install --no-cache-dir .")
