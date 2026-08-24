import http.client
import socket
import ssl
from typing import Protocol
from urllib.parse import urljoin

from app.schemas.agentic_discovery import PageContent
from app.providers.url_safety import HostResolver, ValidatedPageTarget, resolve_public_http_target


class PageFetcher(Protocol):
    def fetch(self, url: str) -> PageContent: ...


class PublicHttpPageFetcher:
    """Small bounded public-page fetcher; it does not automate browsers or log in."""

    _max_bytes = 1_000_000
    _max_redirects = 5

    def __init__(
        self,
        *,
        resolve_host: HostResolver | None = None,
        connection_factory: "ConnectionFactory | None" = None,
    ) -> None:
        self._resolve_host = resolve_host
        self._connection_factory = connection_factory or _connection_for

    def fetch(self, url: str) -> PageContent:
        requested_url = url
        for _ in range(self._max_redirects + 1):
            target = resolve_public_http_target(url, resolve_host=self._resolve_host)
            connection = self._connection_factory(target, 10)
            try:
                connection.request("GET", target.path, headers={"User-Agent": "Career-trans Job Discovery/1.0"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    response.close()
                    if not location:
                        raise ValueError("Page redirect did not provide a target.")
                    url = urljoin(target.url, location)
                    continue
                if response.status >= 400:
                    response.close()
                    raise ValueError("Page request failed.")
                payload = response.read(self._max_bytes + 1)
                response.close()
                if len(payload) > self._max_bytes:
                    raise ValueError("Page exceeded the discovery size limit.")
                charset = response.headers.get_content_charset() or "utf-8"
                return PageContent(
                    requested_url=requested_url,
                    final_url=target.url,
                    html=payload.decode(charset, errors="replace"),
                )
            finally:
                connection.close()
        raise ValueError("Page exceeded the redirect limit.")


class ConnectionFactory(Protocol):
    def __call__(self, target: ValidatedPageTarget, timeout: float): ...


def _connection_for(target: ValidatedPageTarget, timeout: float):
    if target.scheme == "https":
        return _ValidatedHTTPSConnection(target, timeout)
    return _ValidatedHTTPConnection(target, timeout)


class _ValidatedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: ValidatedPageTarget, timeout: float) -> None:
        super().__init__(target.hostname, port=target.port, timeout=timeout)
        self._address = target.address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _ValidatedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: ValidatedPageTarget, timeout: float) -> None:
        super().__init__(target.hostname, port=target.port, timeout=timeout, context=ssl.create_default_context())
        self._address = target.address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
