"""Small stdlib HTTP adapter for the Career-trans command-line client."""

import json
import mimetypes
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CareerTransApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class CareerTransConnectionError(RuntimeError):
    pass


class CareerTransConfigurationError(RuntimeError):
    pass


class CareerTransApiClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._opener = opener

    def login(self, email: str, password: str) -> dict[str, Any]:
        return self._request("POST", "/api/v1/auth/login", payload={"email": email, "password": password}, authenticated=False)

    def upload_cv(self, files: list[Path]) -> dict[str, Any]:
        boundary = f"----careertrans{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for path in files:
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode(),
                    f"Content-Type: {media_type}\r\n\r\n".encode(),
                    path.read_bytes(),
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return self._request(
            "POST",
            "/api/v1/cv-ingestion/upload",
            data=b"".join(chunks),
            content_type=f"multipart/form-data; boundary={boundary}",
        )

    def interpret_cv(self, draft_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/cv-ingestion/{draft_id}/interpret")

    def get_cv(self, draft_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/cv-ingestion/{draft_id}")

    def edit_cv(self, draft_id: str, corrected: object) -> dict[str, Any]:
        return self._request("PATCH", f"/api/v1/cv-ingestion/{draft_id}", payload=corrected)

    def confirm_cv(self, draft_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/cv-ingestion/{draft_id}/confirm")

    def get_llm_configuration(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/config/llm")

    def check_llm_configuration(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/config/llm/check")

    def get_candidate_context_summary(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/profile/context-summary")

    def get_external_discovery_search_context(self, query: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/jobs/external-discovery/search-context", payload={"query": query})

    def import_discovered_jobs(
        self,
        *,
        runtime: str,
        jobs: list[dict[str, Any]],
        query: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/jobs/import-discovered",
            payload={"runtime": runtime, "jobs": jobs, "query": query},
        )

    def rank_jobs_for_current_user(self, jobs: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/jobs/rank-me", payload={"jobs": jobs})

    def get_opportunity_inbox(self, limit: int) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/jobs/inbox?limit={limit}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        data: bytes | None = None,
        content_type: str = "application/json",
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if authenticated and not self._token:
            raise CareerTransConfigurationError("No API token configured. Use --token or CAREER_TRANS_TOKEN.")
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = content_type
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self._base_url}{path}"
        try:
            with self._opener(Request(url, data=data, headers=headers, method=method), timeout=20) as response:
                return self._decode_json(response.read(), response.geturl())
        except HTTPError as exc:
            try:
                body = self._decode_json(exc.read(), url)
                detail = str(body.get("detail", f"HTTP {exc.code}"))
            except CareerTransApiError:
                detail = f"HTTP {exc.code}"
            raise CareerTransApiError(exc.code, detail) from exc
        except URLError as exc:
            raise CareerTransConnectionError(f"Cannot reach Career-trans at {self._base_url}.") from exc

    @staticmethod
    def _decode_json(body: bytes, url: str) -> dict[str, Any]:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CareerTransApiError(502, f"Career-trans returned an invalid JSON response for {url}.") from exc
        if not isinstance(decoded, dict):
            raise CareerTransApiError(502, f"Career-trans returned an unexpected response for {url}.")
        return decoded
