from urllib.request import Request

import pytest

from app.providers.page_fetch import PublicHttpPageFetcher, _SafeRedirectHandler
from app.providers.url_safety import UnsafePageUrlError, validate_public_http_url


def public_resolver(_: str) -> list[str]:
    return ["8.8.8.8"]


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


def test_public_https_url_is_allowed_without_network() -> None:
    validate_public_http_url("https://jobs.example.com/roles/1", resolve_host=public_resolver)


def test_fetcher_rejects_direct_private_target_before_opening() -> None:
    class Opener:
        def open(self, *_args, **_kwargs):
            raise AssertionError("Unsafe URL must not be opened.")

    with pytest.raises(UnsafePageUrlError):
        PublicHttpPageFetcher(opener=Opener()).fetch("http://127.0.0.1/private")


def test_public_redirect_to_private_target_is_rejected_before_following() -> None:
    handler = _SafeRedirectHandler(resolve_host=public_resolver)
    with pytest.raises(UnsafePageUrlError):
        handler.redirect_request(
            Request("https://jobs.example.com/roles/1"),
            None,
            302,
            "Found",
            {},
            "http://169.254.169.254/latest/meta-data",
        )
