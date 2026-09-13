"""Shared outbound-network policy for caller-influenced HTTP destinations.

The guarded HTTP transports resolve and validate a hostname inside the socket
connection path, then connect to the validated numeric address. This binds DNS
validation to the connection and prevents a second resolver lookup from being
rebound to a private destination.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

import httpx

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class SocketInfo:
    """Named representation of one ``socket.getaddrinfo`` result."""

    address_family: socket.AddressFamily
    socket_kind: socket.SocketKind
    protocol: int
    canonical_name: str
    socket_address: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class HttpOrigin:
    """Normalized HTTP origin used by an outbound-network policy."""

    scheme: str
    host: str
    port: int

    @classmethod
    def from_url(cls, url: str | httpx.URL) -> Self:
        """Parse an HTTP(S) URL into its normalized origin.

        Args:
            url: Absolute URL to parse.

        Returns:
            The normalized scheme, host, and effective port.

        Raises:
            DisallowedUrlError: If the URL is malformed or is not HTTP(S).
        """
        try:
            parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
        except (httpx.InvalidURL, TypeError, ValueError) as exc:
            raise DisallowedUrlError("URL is invalid") from exc

        if parsed.scheme not in {"http", "https"}:
            raise DisallowedUrlError("URL must use HTTP or HTTPS")
        if not parsed.raw_host:
            raise DisallowedUrlError("URL must include a hostname")
        if parsed.username or parsed.password:
            raise DisallowedUrlError("URL must not include credentials")

        try:
            host = parsed.raw_host.decode("ascii").rstrip(".").lower()
            explicit_port = parsed.port
        except (httpx.InvalidURL, UnicodeDecodeError) as exc:
            raise DisallowedUrlError("URL hostname is invalid") from exc
        default_port = 443 if parsed.scheme == "https" else 80
        port = default_port if explicit_port is None else explicit_port
        if not 1 <= port <= 65535:
            raise DisallowedUrlError("URL port is invalid")
        return cls(scheme=parsed.scheme, host=host, port=port)


class HttpEgressPurpose(StrEnum):
    """Trusted product surface selecting a private-origin policy."""

    LLM = "llm"
    MCP = "mcp"
    OAUTH = "oauth"
    AUDIT = "audit"
    OTEL = "otel"
    SAML = "saml"
    VCS = "vcs"


@dataclass(frozen=True, slots=True)
class HttpEgressPolicy:
    """Private HTTP origins explicitly approved by a deployment operator."""

    allowed_private_origins: frozenset[HttpOrigin] = frozenset()

    @classmethod
    def from_allowed_private_origins(cls, origins: Iterable[str]) -> Self:
        """Create a policy from exact, path-free HTTP origins."""
        parsed_origins: set[HttpOrigin] = set()
        for value in origins:
            try:
                url = httpx.URL(value)
            except (httpx.InvalidURL, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid private HTTP origin: {value!r}") from exc
            if url.path not in {"", "/"} or url.query or url.fragment:
                raise ValueError(
                    "Private HTTP origin must not include a path, query, or "
                    f"fragment: {value!r}"
                )
            try:
                parsed_origins.add(HttpOrigin.from_url(url))
            except DisallowedUrlError as exc:
                raise ValueError(f"Invalid private HTTP origin: {value!r}") from exc
        return cls(allowed_private_origins=frozenset(parsed_origins))

    @classmethod
    def for_purpose(
        cls,
        purpose: HttpEgressPurpose,
        configured_origins: Iterable[str],
    ) -> Self:
        """Select exact private origins for one product surface.

        Configured entries use ``purpose=origin`` so allowing a private LLM,
        for example, does not make that origin reachable through MCP.
        """
        selected: list[str] = []
        for entry in configured_origins:
            configured_purpose, separator, origin = entry.partition("=")
            configured_purpose = configured_purpose.strip()
            origin = origin.strip()
            if not separator or not configured_purpose or not origin:
                raise ValueError(
                    "Private HTTP origins must use the format purpose=origin"
                )
            try:
                parsed_purpose = HttpEgressPurpose(configured_purpose)
            except ValueError as exc:
                raise ValueError(
                    f"Unknown private HTTP origin purpose: {configured_purpose!r}"
                ) from exc
            if parsed_purpose is purpose:
                selected.append(origin)
        return cls.from_allowed_private_origins(selected)

    def allows_private_address(self, origin: HttpOrigin) -> bool:
        """Return whether private addresses are allowed for an exact origin."""
        return origin in self.allowed_private_origins


def configured_http_egress_policy(purpose: HttpEgressPurpose) -> HttpEgressPolicy:
    """Load the operator-owned private-origin policy for one product surface."""
    from tracecat import config

    if (
        config.TRACECAT__EE_MULTI_TENANT
        and config.TRACECAT__HTTP_EGRESS_ALLOWED_PRIVATE_ORIGINS
    ):
        raise ValueError(
            "Private HTTP origins are unavailable in multi-tenant deployments"
        )
    return HttpEgressPolicy.for_purpose(
        purpose,
        config.TRACECAT__HTTP_EGRESS_ALLOWED_PRIVATE_ORIGINS,
    )


class DisallowedUrlError(ValueError):
    """Raised when an outbound URL violates the network policy."""


def is_disallowed_address(address: IPAddress) -> bool:
    """Return whether an address is not publicly routable."""
    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _addresses_from_socket_infos(infos: Sequence[SocketInfo]) -> tuple[IPAddress, ...]:
    """Extract unique IP addresses from resolver results."""
    if not infos:
        raise DisallowedUrlError("Host could not be resolved")

    addresses: list[IPAddress] = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info.socket_address[0])
        except (IndexError, ValueError) as exc:
            raise DisallowedUrlError("Host is not allowed") from exc
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def validate_resolved_addresses(
    infos: Sequence[SocketInfo], *, allow_private: bool = False
) -> tuple[IPAddress, ...]:
    """Validate resolver results and return their unique IP addresses."""
    addresses = _addresses_from_socket_infos(infos)
    if not allow_private and any(is_disallowed_address(item) for item in addresses):
        raise DisallowedUrlError("Host is not allowed")
    return addresses


def resolve_host(host: str, port: int) -> tuple[SocketInfo, ...]:
    """Resolve a TCP host without exposing resolver details in errors."""
    try:
        return tuple(
            SocketInfo(
                address_family=address_family,
                socket_kind=socket_kind,
                protocol=protocol,
                canonical_name=canonical_name,
                socket_address=socket_address,
            )
            for (
                address_family,
                socket_kind,
                protocol,
                canonical_name,
                socket_address,
            ) in socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        )
    except (socket.gaierror, UnicodeError) as exc:
        raise DisallowedUrlError("Host could not be resolved") from exc


async def resolve_host_async(host: str, port: int) -> tuple[SocketInfo, ...]:
    """Resolve a TCP host off the event loop."""
    return await asyncio.to_thread(resolve_host, host, port)


async def validate_url_resolves_public_async(url: str) -> None:
    """Resolve a URL host off-thread and require public addresses.

    This helper remains useful for validation-only flows. Requests to
    caller-influenced destinations must use the guarded transports in
    :mod:`tracecat.outbound_http` so validation and connection cannot be
    separated by a DNS lookup.
    """
    origin = HttpOrigin.from_url(url)
    infos = await resolve_host_async(origin.host, origin.port)
    validate_resolved_addresses(infos)
