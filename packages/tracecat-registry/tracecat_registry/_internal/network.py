"""Egress policy for browser targets that Tracecat asks a browser to open.

Actions that hand a caller-supplied target URL to a remote browser sit outside
the API's outbound HTTP policy: the page request leaves the browser, so no
transport-level guard in this process ever sees it. Alert data routinely carries
attacker-chosen URLs, and a browser reached over a private network can answer a
`http://169.254.169.254/` or `http://127.0.0.1/` target with cloud-instance
credentials or an unauthenticated internal admin page. This module keeps the
same address policy as `tracecat.network` for the one boundary the registry
owns, because `tracecat_registry` is a dependency of `tracecat` and importing
that module here would invert the dependency.

The check resolves the target host and rejects any answer that is not publicly
routable. It deliberately does not cover a redirect or a DNS rebind to a private
address after navigation starts: the browser, not Tracecat, performs those, so
they need network policy where the browser runs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from tracecat_registry import config

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

ALLOWED_TARGET_SCHEMES = frozenset({"http", "https"})


class DisallowedTargetUrlError(ValueError):
    """Raised when a target URL does not address a publicly routable HTTP host."""


def is_disallowed_target_address(address: IPAddress) -> bool:
    """Return whether an address is outside the public internet.

    Operator-configured CIDRs are allowed first so an intentional private
    deployment stays reachable without weakening the default policy.
    """
    # Classify an IPv4-mapped IPv6 address as its IPv4 form, otherwise
    # ::ffff:169.254.169.254 reads as an ordinary global IPv6 address.
    effective = (
        address.ipv4_mapped or address
        if isinstance(address, ipaddress.IPv6Address)
        else address
    )
    if any(
        effective in network
        for network in config.TRACECAT__OUTBOUND_ALLOWED_PRIVATE_CIDRS
    ):
        return False
    # `is_global` is the authoritative "publicly routable" check and rejects
    # ranges the explicit flags miss, such as CGNAT 100.64.0.0/10 and TEST-NET.
    # The explicit flags stay for clarity and for any class it does not cover.
    return (
        not effective.is_global
        or effective.is_private
        or effective.is_loopback
        or effective.is_link_local
        or effective.is_reserved
        or effective.is_multicast
        or effective.is_unspecified
    )


async def _resolve_target_addresses(hostname: str, port: int) -> list[IPAddress]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except (OSError, UnicodeError) as exc:
        # UnicodeError covers malformed DNS labels, which getaddrinfo raises
        # instead of gaierror.
        raise DisallowedTargetUrlError("Target host could not be resolved") from exc
    try:
        addresses = list(dict.fromkeys(ipaddress.ip_address(i[4][0]) for i in infos))
    except (IndexError, ValueError) as exc:
        raise DisallowedTargetUrlError("Target host is not allowed") from exc
    if not addresses:
        raise DisallowedTargetUrlError("Target host could not be resolved")
    return addresses


async def validate_public_target_url(url: str) -> str:
    """Return `url` unchanged once every resolved address is publicly routable.

    Raises:
        DisallowedTargetUrlError: If the URL is malformed, carries credentials,
            uses a scheme other than http or https, or resolves to any address
            that is not publicly routable.
    """
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise DisallowedTargetUrlError("Target URL is invalid") from exc
    if parts.scheme not in ALLOWED_TARGET_SCHEMES:
        raise DisallowedTargetUrlError("Target URL must use http or https")
    if not hostname:
        raise DisallowedTargetUrlError("Target URL must include a hostname")
    if parts.username is not None or parts.password is not None:
        raise DisallowedTargetUrlError("Target URL must not embed credentials")
    if "%" in hostname:
        raise DisallowedTargetUrlError("Target URL must not use a scoped address")
    # The port barely affects host resolution, but getaddrinfo requires one.
    port = port or (443 if parts.scheme == "https" else 80)
    for address in await _resolve_target_addresses(hostname, port):
        if is_disallowed_target_address(address):
            raise DisallowedTargetUrlError("Target host is not allowed")
    return url
