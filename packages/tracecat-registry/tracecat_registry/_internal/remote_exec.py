"""Shared guards for actions that run commands on caller-chosen remote hosts."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Sequence

MAX_ERROR_PATTERNS = 20
MAX_ERROR_PATTERN_LENGTH = 256
ERROR_OUTPUT_TAIL_CHARS = 2000

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Cloud instance metadata and host-agent endpoints outside link-local space.
_METADATA_ADDRESSES: frozenset[IPAddress] = frozenset(
    {
        ipaddress.ip_address("fd00:ec2::254"),  # AWS IMDS over IPv6
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud metadata
        ipaddress.ip_address("168.63.129.16"),  # Azure WireServer
    }
)
_THIS_NETWORK = ipaddress.IPv4Network("0.0.0.0/8")
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")


class RemoteTargetDenied(ValueError):
    """Raised when a remote host resolves to an address actions must not reach."""


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    if address in _NAT64_WELL_KNOWN or (
        address in _IPV4_COMPATIBLE and int(address) > 1
    ):
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_denied_remote_address(address: IPAddress) -> bool:
    """Return whether an address is loopback, link-local, metadata, or unroutable.

    Private ranges (RFC 1918, CGNAT, unique local) stay reachable because SSH
    and Ansible targets are usually internal hosts; the executor sandbox egress
    policy decides whether those ranges are reachable in a given deployment.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if (embedded := _embedded_ipv4(address)) is not None:
            return is_denied_remote_address(embedded)
    return (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or address in _METADATA_ADDRESSES
        or (isinstance(address, ipaddress.IPv4Address) and address in _THIS_NETWORK)
    )


def resolve_remote_target(host: str, port: int) -> str:
    """Resolve a host once, reject denied addresses, and return the IP to dial.

    Callers must connect to the returned address instead of resolving ``host``
    again, so a DNS answer cannot change between validation and connection.
    """
    candidate = host.strip()
    if not candidate or "%" in candidate:
        raise RemoteTargetDenied("Remote host is not allowed")
    try:
        infos = socket.getaddrinfo(
            candidate, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except (OSError, UnicodeError) as exc:
        raise RemoteTargetDenied("Remote host could not be resolved") from exc
    addresses = list(dict.fromkeys(ipaddress.ip_address(i[4][0]) for i in infos))
    if not addresses:
        raise RemoteTargetDenied("Remote host could not be resolved")
    if any(is_denied_remote_address(address) for address in addresses):
        raise RemoteTargetDenied(
            "Remote host resolves to a loopback, link-local, metadata, or "
            "otherwise reserved address"
        )
    return str(addresses[0])


def validate_error_patterns(patterns: Sequence[str] | None) -> list[str]:
    """Validate literal error substrings supplied as action inputs."""
    if not patterns:
        return []
    if len(patterns) > MAX_ERROR_PATTERNS:
        raise ValueError(f"error_patterns accepts at most {MAX_ERROR_PATTERNS} items")
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("error_patterns items must be non-empty strings")
        if len(pattern) > MAX_ERROR_PATTERN_LENGTH:
            raise ValueError(
                f"error_patterns items must be at most {MAX_ERROR_PATTERN_LENGTH} characters"
            )
    return list(patterns)


def find_error_pattern(patterns: Sequence[str], texts: Iterable[str]) -> str | None:
    """Return the first literal pattern found in any text.

    Patterns are matched as plain substrings, never as regular expressions, so
    matching is linear in the output size and cannot backtrack.
    """
    texts = list(texts)
    for pattern in patterns:
        if any(pattern in text for text in texts):
            return pattern
    return None


def output_tail(text: str, limit: int = ERROR_OUTPUT_TAIL_CHARS) -> str:
    """Return the last ``limit`` characters of command output."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]
