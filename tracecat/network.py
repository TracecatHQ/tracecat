"""Shared SSRF guards for outbound URLs to caller-influenced destinations.

Resolves a hostname and rejects any address that is not publicly routable, so
a request cannot be steered at loopback, private, link-local, or cloud-metadata
targets. Callers that also require a specific scheme enforce that separately.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

_SocketInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[Any, ...]]


class DisallowedUrlError(ValueError):
    """Raised when a URL resolves to a non-public or unroutable address."""


def is_disallowed_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    # ``is_global`` is the authoritative "publicly routable" check and rejects
    # ranges the explicit flags miss (e.g. CGNAT 100.64.0.0/10, TEST-NET, and
    # other non-global assignments). Keep the explicit flags for clarity and to
    # guard against any address class not yet covered by ``is_global``.
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_resolved_addresses(infos: Sequence[_SocketInfo]) -> None:
    """Reject any resolved address that is not publicly routable."""
    if not infos:
        raise DisallowedUrlError("Host could not be resolved")
    for *_, sockaddr in infos:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except (IndexError, ValueError) as exc:
            raise DisallowedUrlError("Host is not allowed") from exc
        if is_disallowed_address(address):
            raise DisallowedUrlError("Host is not allowed")


async def validate_url_resolves_public_async(url: str, *, default_port: int) -> None:
    """Resolve the URL host off-thread and require public addresses.

    ``default_port`` is used only for resolution when the URL omits a port.
    Raises :class:`DisallowedUrlError` on a missing host, resolution failure, or
    any non-public address, without echoing the resolved address.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise DisallowedUrlError("URL is invalid") from exc
    if not hostname:
        raise DisallowedUrlError("URL must include a hostname")
    port = port or default_port
    try:
        # getaddrinfo is blocking C I/O (hosts file, resolv.conf, network
        # resolver); keep it off the event loop so a slow DNS server for one
        # URL cannot stall every other coroutine on the loop.
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, UnicodeError) as exc:
        # UnicodeError covers malformed DNS labels (e.g. a label over 63 chars),
        # which getaddrinfo raises instead of gaierror.
        raise DisallowedUrlError("Host could not be resolved") from exc
    validate_resolved_addresses(infos)
