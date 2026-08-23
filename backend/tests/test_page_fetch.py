from email.message import Message

import pytest

from app.providers.page_fetch import PublicHttpPageFetcher
from app.providers.url_safety import UnsafePageUrlError, validate_public_http_url


def public_resolver(_: str) -> list[str]:
    return ["8.8.8.8"]


class FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"<html/>", location: str | None = None) -> None:
        self.status = status
        self._body = body
        self._location = location
        self.headers = Message()

    def getheader(self, name: str) -> str | None:
        return self._location if name.casefold() == "location" else None

    def read(self, _: int) -> bytes:
        return self._body

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, path, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/jobs/1",
        "http://127.0.0.1/jobs/1",
        "http://10.0.0.1/jobs/1",
        "http://192.168.1.1/jobs/1",
        "http://169.254.169.254/latest/meta-data",
        "ftp://example.com/jobs/1",
        "not-a-url",
    ],
)
def test_unsafe_or_malformed_page_targets_are_rejected_before_fetch(url: str) -> None:
    with pytest.raises(UnsafePageUrlError):
        validate_public_http_url(url, resolve_host=public_resolver)


def test_public_https_url_is_allowed_and_connects_to_validated_address_only() -> None:
    connected_addresses: list[str] = []

    def connection_factory(target, _: float) -> FakeConnection:
        connected_addresses.append(target.address)
        return FakeConnection(FakeResponse())

    response = PublicHttpPageFetcher(
        resolve_host=public_resolver,
        connection_factory=connection_factory,
    ).fetch("https://jobs.example.com/roles/1")

    assert response.final_url == "https://jobs.example.com/roles/1"
    assert connected_addresses == ["8.8.8.8"]


def test_fetcher_rejects_direct_private_target_before_opening() -> None:
    def connection_factory(*_args):
        raise AssertionError("Unsafe URL must not be opened.")

    with pytest.raises(UnsafePageUrlError):
        PublicHttpPageFetcher(connection_factory=connection_factory).fetch("http://127.0.0.1/private")


def test_public_redirect_to_private_target_is_rejected_before_following() -> None:
    connected_urls: list[str] = []

    def connection_factory(target, _: float) -> FakeConnection:
        connected_urls.append(target.url)
        return FakeConnection(FakeResponse(status=302, location="http://169.254.169.254/latest/meta-data"))

    with pytest.raises(UnsafePageUrlError):
        PublicHttpPageFetcher(
            resolve_host=public_resolver,
            connection_factory=connection_factory,
        ).fetch("https://jobs.example.com/roles/1")

    assert connected_urls == ["https://jobs.example.com/roles/1"]


def test_dns_rebinding_cannot_change_the_validated_connection_target() -> None:
    resolutions: list[str] = []
    connected_addresses: list[str] = []

    def rebinding_resolver(hostname: str) -> list[str]:
        resolutions.append(hostname)
        return ["8.8.8.8"] if len(resolutions) == 1 else ["127.0.0.1"]

    def connection_factory(target, _: float) -> FakeConnection:
        connected_addresses.append(target.address)
        return FakeConnection(FakeResponse())

    PublicHttpPageFetcher(
        resolve_host=rebinding_resolver,
        connection_factory=connection_factory,
    ).fetch("https://evil.example/jobs/1")

    assert resolutions == ["evil.example"]
    assert connected_addresses == ["8.8.8.8"]
