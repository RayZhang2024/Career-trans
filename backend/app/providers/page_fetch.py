from typing import Protocol
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.schemas.agentic_discovery import PageContent
from app.providers.url_safety import HostResolver, validate_public_http_url


class PageFetcher(Protocol):
    def fetch(self, url: str) -> PageContent: ...


class PublicHttpPageFetcher:
    """Small bounded public-page fetcher; it does not automate browsers or log in."""

    _max_bytes = 1_000_000

    def __init__(
        self,
        *,
        resolve_host: HostResolver | None = None,
        opener: object | None = None,
    ) -> None:
        self._resolve_host = resolve_host
        self._opener = opener or build_opener(_SafeRedirectHandler(resolve_host=resolve_host))

    def fetch(self, url: str) -> PageContent:
        validate_public_http_url(url, resolve_host=self._resolve_host)
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with self._opener.open(request, timeout=10) as response:
            validate_public_http_url(response.geturl(), resolve_host=self._resolve_host)
            payload = response.read(self._max_bytes + 1)
            if len(payload) > self._max_bytes:
                raise ValueError("Page exceeded the discovery size limit.")
            charset = response.headers.get_content_charset() or "utf-8"
            return PageContent(
                requested_url=url,
                final_url=response.geturl(),
                html=payload.decode(charset, errors="replace"),
            )


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, resolve_host: HostResolver | None) -> None:
        super().__init__()
        self._resolve_host = resolve_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(urljoin(req.full_url, newurl), resolve_host=self._resolve_host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
