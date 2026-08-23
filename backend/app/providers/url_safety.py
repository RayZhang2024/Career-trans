import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit


class UnsafePageUrlError(ValueError):
    """Raised when an untrusted page URL could target a non-public host."""


HostResolver = Callable[[str], list[str]]
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


@dataclass(frozen=True)
class ValidatedPageTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    address: str
    path: str


def resolve_public_http_target(
    url: str,
    *,
    resolve_host: HostResolver | None = None,
) -> ValidatedPageTarget:
    """Allow only publicly routable HTTP(S) page targets.

    Resolution is deliberately injected so unit tests can exercise safety rules without
    network access. Every resolved address must be globally routable.
    """
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafePageUrlError("Malformed page URL.") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        raise UnsafePageUrlError("Only absolute HTTP(S) URLs with a hostname are allowed.")
    if parsed.username or parsed.password or not _valid_hostname(hostname):
        raise UnsafePageUrlError("Page URL has an invalid hostname.")
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
        raise UnsafePageUrlError("Local page targets are not allowed.")

    addresses = [hostname] if _is_ip_address(hostname) else (resolve_host or _resolve_host)(hostname)
    if not addresses:
        raise UnsafePageUrlError("Page hostname did not resolve to a public address.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafePageUrlError("Page hostname resolved to an invalid address.") from exc
        if not ip.is_global:
            raise UnsafePageUrlError("Page URL resolves to a non-public address.")
    scheme = parsed.scheme.casefold()
    return ValidatedPageTarget(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=port or (443 if scheme == "https" else 80),
        address=addresses[0],
        path=(parsed.path or "/") + (f"?{parsed.query}" if parsed.query else ""),
    )


def validate_public_http_url(url: str, *, resolve_host: HostResolver | None = None) -> None:
    """Validate a public HTTP(S) URL without exposing the resolved target."""
    resolve_public_http_target(url, resolve_host=resolve_host)


def _resolve_host(hostname: str) -> list[str]:
    try:
        return list({item[4][0] for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise UnsafePageUrlError("Page hostname could not be resolved.") from exc


def _is_ip_address(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _valid_hostname(hostname: str) -> bool:
    if len(hostname) > 253:
        return False
    return all(_HOST_LABEL.fullmatch(label) for label in hostname.rstrip(".").split("."))
