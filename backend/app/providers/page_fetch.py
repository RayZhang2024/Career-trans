from typing import Protocol
from urllib.request import Request, urlopen

from app.schemas.agentic_discovery import PageContent


class PageFetcher(Protocol):
    def fetch(self, url: str) -> PageContent: ...


class PublicHttpPageFetcher:
    """Small bounded public-page fetcher; it does not automate browsers or log in."""

    _max_bytes = 1_000_000

    def fetch(self, url: str) -> PageContent:
        request = Request(url, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
        with urlopen(request, timeout=10) as response:  # noqa: S310 - selected public URL
            payload = response.read(self._max_bytes + 1)
            if len(payload) > self._max_bytes:
                raise ValueError("Page exceeded the discovery size limit.")
            charset = response.headers.get_content_charset() or "utf-8"
            return PageContent(
                requested_url=url,
                final_url=response.geturl(),
                html=payload.decode(charset, errors="replace"),
            )
