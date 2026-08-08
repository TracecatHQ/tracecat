"""Network configuration helpers for nsjail sandboxes."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from pathlib import Path
from typing import Literal

from tracecat import config as tracecat_config
from tracecat.sandbox.types import (
    IPNetwork,
    SandboxNetworkMode,
    SandboxNetworkPolicy,
)

NSTUN_GUEST_IP4 = "10.255.255.2"
NSTUN_GATEWAY_IP4 = "10.255.255.1"
NSTUN_GUEST_IP6 = "fc00::2"
NSTUN_GATEWAY_IP6 = "fc00::1"
NSTUN_MAX_RULES = 128

_NSTUN_GATEWAY_ADDRESS4 = IPv4Address(NSTUN_GATEWAY_IP4)
_NSTUN_GATEWAY_ADDRESS6 = IPv6Address(NSTUN_GATEWAY_IP6)

# Networks that must not be reachable from filtered untrusted sandboxes. IPv6
# is denied separately in its entirety until public-only IPv6 classification is
# implemented without risking mapped-address or transition-mechanism bypasses.
_FILTERED_BLOCKED_IPV4_CIDRS: tuple[IPv4Network, ...] = (
    IPv4Network("0.0.0.0/8"),
    IPv4Network("10.0.0.0/8"),
    IPv4Network("100.64.0.0/10"),
    IPv4Network("127.0.0.0/8"),
    IPv4Network("169.254.0.0/16"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.0.0.0/24"),
    IPv4Network("192.0.2.0/24"),
    IPv4Network("192.88.99.0/24"),
    IPv4Network("192.168.0.0/16"),
    IPv4Network("198.18.0.0/15"),
    IPv4Network("198.51.100.0/24"),
    IPv4Network("203.0.113.0/24"),
    IPv4Network("224.0.0.0/4"),
    IPv4Network("240.0.0.0/4"),
)

type IPAddress = IPv4Address | IPv6Address
type NstunAction = Literal["ALLOW", "REJECT", "REDIRECT"]
type NstunProtocol = Literal["ANY", "TCP", "UDP", "ICMP"]


@dataclass(frozen=True, slots=True)
class SandboxDnsRoute:
    """A resolver address exposed to the jail and its parent-side target."""

    guest_address: IPAddress
    host_address: IPAddress

    @property
    def requires_redirect(self) -> bool:
        """Return whether NSTUN must redirect the guest resolver address."""
        return self.guest_address != self.host_address


@dataclass(frozen=True, slots=True)
class SandboxDnsConfig:
    """Generated resolver content and NSTUN routes needed to serve it."""

    resolv_conf: str
    routes: tuple[SandboxDnsRoute, ...]


@dataclass(frozen=True, slots=True)
class SandboxNetworkFiles:
    """Host paths and resolver routes generated for a networked jail."""

    resolv_conf: Path
    hosts: Path
    nsswitch_conf: Path
    dns_routes: tuple[SandboxDnsRoute, ...]


@dataclass(frozen=True, slots=True)
class _NstunRule:
    """Typed representation of one outbound NSTUN rule."""

    action: NstunAction
    destination: IPNetwork
    protocol: NstunProtocol = "ANY"
    destination_port: int | None = None
    redirect_address: IPAddress | None = None
    redirect_port: int | None = None

    def config_lines(self) -> list[str]:
        """Render this rule in NsJail's protobuf text format."""
        lines = [
            f"  rule{self.destination.version} {{",
            "    direction: GUEST_TO_HOST",
            f"    action: {self.action}",
            f"    proto: {self.protocol}",
            f'    dst_ip: "{self.destination}"',
        ]
        if self.destination_port is not None:
            lines.append(f"    dport: {self.destination_port}")
        if self.redirect_address is not None:
            lines.append(f'    redirect_ip: "{self.redirect_address}"')
        if self.redirect_port is not None:
            lines.append(f"    redirect_port: {self.redirect_port}")
        lines.append("  }")
        return lines


def configured_sandbox_network_policy() -> SandboxNetworkPolicy:
    """Return the deployment-owned filtered policy for untrusted sandboxes."""
    return SandboxNetworkPolicy(
        mode=SandboxNetworkMode.FILTERED,
        allowed_cidrs=tracecat_config.TRACECAT__SANDBOX_ALLOWED_EGRESS_CIDRS,
        blocked_cidrs=tracecat_config.TRACECAT__SANDBOX_BLOCKED_EGRESS_CIDRS,
    )


def _network_for_address(address: IPAddress) -> IPNetwork:
    prefix_length = 32 if address.version == 4 else 128
    return ip_network(f"{address}/{prefix_length}")


def _deduplicate_networks(networks: tuple[IPNetwork, ...]) -> tuple[IPNetwork, ...]:
    return tuple(dict.fromkeys(networks))


def _dns_rules(routes: tuple[SandboxDnsRoute, ...]) -> list[_NstunRule]:
    rules: list[_NstunRule] = []
    for route in routes:
        destination = _network_for_address(route.guest_address)
        action: NstunAction = "REDIRECT" if route.requires_redirect else "ALLOW"
        for protocol in ("UDP", "TCP"):
            rules.append(
                _NstunRule(
                    action=action,
                    destination=destination,
                    protocol=protocol,
                    destination_port=53,
                    redirect_address=(
                        route.host_address if route.requires_redirect else None
                    ),
                    redirect_port=53 if route.requires_redirect else None,
                )
            )
    return rules


def _filtered_rules(policy: SandboxNetworkPolicy) -> list[_NstunRule]:
    rules = [
        _NstunRule(action="ALLOW", destination=network)
        for network in _deduplicate_networks(policy.allowed_cidrs)
    ]
    blocked_networks: tuple[IPNetwork, ...] = (
        *_FILTERED_BLOCKED_IPV4_CIDRS,
        *policy.blocked_cidrs,
    )
    rules.extend(
        _NstunRule(action="REJECT", destination=network)
        for network in _deduplicate_networks(blocked_networks)
    )
    rules.extend(
        [
            # NSTUN evaluates the first matching rule. Remaining IPv4 addresses
            # are public from this policy's perspective.
            _NstunRule(
                action="ALLOW",
                destination=IPv4Network("0.0.0.0/0"),
            ),
            # Block IPv6 until public-only IPv6 egress has equivalent coverage.
            _NstunRule(action="REJECT", destination=IPv6Network("::/0")),
        ]
    )
    return rules


def nstun_user_net_config_lines(
    policy: SandboxNetworkPolicy,
    dns_routes: tuple[SandboxDnsRoute, ...] = (),
) -> list[str]:
    """Return NsJail 4.0 NSTUN configuration for an outbound policy.

    DNS rules are emitted first so a private deployment resolver remains
    reachable only on TCP/UDP port 53. Filtered policy exceptions follow, then
    internal-address rejects and an explicit public IPv4 allow rule.

    Args:
        policy: Trusted policy selected by Tracecat or its administrator.
        dns_routes: Resolvers written into the jail's resolv.conf.

    Returns:
        Protobuf text lines. Disabled policies return no ``user_net`` block.

    Raises:
        ValueError: If the generated policy exceeds NSTUN's rule limit.
    """
    if policy.mode is SandboxNetworkMode.DISABLED:
        return []

    rules = _dns_rules(dns_routes)
    if policy.mode is SandboxNetworkMode.FILTERED:
        rules.extend(_filtered_rules(policy))
    else:
        rules.extend(
            [
                _NstunRule(
                    action="ALLOW",
                    destination=IPv4Network("0.0.0.0/0"),
                ),
                _NstunRule(action="ALLOW", destination=IPv6Network("::/0")),
            ]
        )

    if len(rules) > NSTUN_MAX_RULES:
        raise ValueError(
            f"NSTUN policy has {len(rules)} rules; maximum is {NSTUN_MAX_RULES}"
        )

    lines = [
        "",
        "# Userspace networking via NSTUN with explicit outbound policy",
        "user_net {",
        "  backend: NSTUN",
        f'  ip4: "{NSTUN_GUEST_IP4}"',
        f'  gw4: "{NSTUN_GATEWAY_IP4}"',
        f'  ip6: "{NSTUN_GUEST_IP6}"',
        f'  gw6: "{NSTUN_GATEWAY_IP6}"',
    ]
    for rule in rules:
        lines.extend(rule.config_lines())
    lines.append("}")
    return lines


def build_sandbox_dns_config(
    host_resolv_path: Path = Path("/etc/resolv.conf"),
) -> SandboxDnsConfig:
    """Build resolver content and safe NSTUN routes for a networked jail.

    Non-loopback resolvers are preserved. Private resolver addresses receive a
    narrow TCP/UDP port 53 exception; public resolvers remain subject to the
    normal public-egress rule. A parent-namespace loopback resolver is
    represented by the NSTUN gateway address and redirected explicitly to its
    original loopback address on port 53. Unlike Pasta's gateway mapping, this
    does not expose arbitrary parent-loopback ports.

    Args:
        host_resolv_path: Parent namespace resolver configuration.

    Returns:
        Resolver content and the routes required to make it reachable.
    """
    try:
        host_resolv = host_resolv_path.read_text()
    except OSError:
        host_resolv = ""

    nameservers: list[str] = []
    options: list[str] = []
    routes: list[SandboxDnsRoute] = []
    mapped_loopback_versions: set[int] = set()

    for line in host_resolv.splitlines():
        stripped = line.strip()
        if stripped.startswith("nameserver "):
            parts = stripped.split()
            if len(parts) < 2:
                continue
            address_text = parts[1].split("%", maxsplit=1)[0]
            try:
                host_address = ip_address(address_text)
            except ValueError:
                continue
            if (
                host_address.is_unspecified
                or host_address.is_multicast
                or host_address.is_link_local
            ):
                continue

            guest_address: IPAddress = host_address
            if host_address.is_loopback:
                if host_address.version in mapped_loopback_versions:
                    continue
                mapped_loopback_versions.add(host_address.version)
                guest_address = (
                    _NSTUN_GATEWAY_ADDRESS4
                    if host_address.version == 4
                    else _NSTUN_GATEWAY_ADDRESS6
                )

            route = SandboxDnsRoute(
                guest_address=guest_address,
                host_address=host_address,
            )
            if route in routes:
                continue
            routes.append(route)
            nameservers.append(f"nameserver {guest_address}")
        elif stripped.startswith(("search ", "domain ", "options ")):
            options.append(stripped)

    lines = [*nameservers, *options]
    resolv_conf = "\n".join(lines)
    if lines:
        resolv_conf += "\n"
    return SandboxDnsConfig(resolv_conf=resolv_conf, routes=tuple(routes))


def write_sandbox_network_files(
    target_dir: Path,
    host_resolv_path: Path = Path("/etc/resolv.conf"),
) -> SandboxNetworkFiles:
    """Write hostname and resolver files for an NSTUN-enabled jail."""
    target_dir.mkdir(parents=True, exist_ok=True)

    dns_config = build_sandbox_dns_config(host_resolv_path)
    resolv_conf = target_dir / "resolv.conf"
    hosts = target_dir / "hosts"
    nsswitch_conf = target_dir / "nsswitch.conf"

    resolv_conf.write_text(dns_config.resolv_conf)
    hosts.write_text(
        "127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n"
    )
    nsswitch_conf.write_text(
        "passwd:         files\n"
        "group:          files\n"
        "shadow:         files\n"
        "hosts:          files dns\n"
        "networks:       files\n"
        "protocols:      files\n"
        "services:       files\n"
    )

    return SandboxNetworkFiles(
        resolv_conf=resolv_conf,
        hosts=hosts,
        nsswitch_conf=nsswitch_conf,
        dns_routes=dns_config.routes,
    )


def sandbox_dns_mount_config_lines(files: SandboxNetworkFiles) -> list[str]:
    """Return read-only resolver and hostname mounts for a networked jail."""
    return [
        "",
        "# Network name resolution for the isolated namespace",
        f'mount {{ src: "{files.resolv_conf}" dst: "/etc/resolv.conf" is_bind: true rw: false }}',
        f'mount {{ src: "{files.hosts}" dst: "/etc/hosts" is_bind: true rw: false }}',
        f'mount {{ src: "{files.nsswitch_conf}" dst: "/etc/nsswitch.conf" is_bind: true rw: false }}',
    ]
