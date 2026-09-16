"""Organization IP allowlist rules.

An organization may restrict API access to a set of IP addresses or CIDR
ranges (for example, its VPN egress). This module holds the pure parsing and
matching logic; see ``tracecat.auth.ip_allowlist_enforcement`` for the
request-time check.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from tracecat.logger import logger

IP_ALLOWLIST_ENABLED_KEY = "ip_allowlist_enabled"
IP_ALLOWLISTS_KEY = "ip_allowlists"
IP_ALLOWLIST_MAX_ENTRIES = 100
IP_ALLOWLIST_MAX_CIDRS_PER_LIST = 50
IP_ALLOWLIST_NAME_MAX_LENGTH = 100
IP_ALLOWLIST_DESCRIPTION_MAX_LENGTH = 500
IP_ALLOWLIST_CACHE_TTL_SECONDS = 30

IP_ALLOWLIST_DENIED_DETAIL = (
    "Access from your IP address is not permitted by your organization's IP allowlist."
)

type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class OrgIPAllowlist:
    """Compiled allowlist for one organization."""

    enabled: bool
    networks: tuple[IPNetwork, ...]

    @property
    def enforced(self) -> bool:
        return self.enabled and bool(self.networks)

    def match(self, ip: IPAddress) -> IPNetwork | None:
        """Return the first network containing ``ip``, if any."""
        for network in self.networks:
            if ip.version == network.version and ip in network:
                return network
        return None


def parse_cidr(value: str) -> IPNetwork:
    """Parse a single IP or CIDR string into a network.

    Host bits are masked (``10.0.0.5/8`` -> ``10.0.0.0/8``).

    Raises:
        ValueError: If the value is not a valid IPv4/IPv6 address or CIDR.
    """
    return ipaddress.ip_network(value.strip(), strict=False)


def normalize_cidrs(values: list[str]) -> list[str]:
    """Validate and canonicalize a list of IPs/CIDRs, dropping duplicates."""
    seen: dict[str, None] = {}
    for value in values:
        if not value.strip():
            continue
        seen.setdefault(parse_cidr(value).with_prefixlen, None)
    return list(seen)


def compile_allowlist(*, enabled: bool, cidrs: list[str]) -> OrgIPAllowlist:
    networks: list[IPNetwork] = []
    for cidr in cidrs:
        try:
            networks.append(parse_cidr(cidr))
        except ValueError:
            logger.warning("Ignoring invalid IP allowlist entry", cidr=cidr)
    return OrgIPAllowlist(enabled=enabled, networks=tuple(networks))


def parse_client_ip(value: str | None) -> IPAddress | None:
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None
