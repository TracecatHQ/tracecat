"""Network configuration helpers for nsjail sandboxes."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path

PASTA_GUEST_IP = "10.255.255.2"
PASTA_GATEWAY_IP = "10.255.255.1"
PASTA_GUEST_IP6 = "fc00::2"
PASTA_GATEWAY_IP6 = "fc00::1"


def _is_loopback_nameserver(line: str) -> bool:
    parts = line.split()
    if len(parts) < 2:
        return False
    address = parts[1].split("%", maxsplit=1)[0]
    try:
        return ip_address(address).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class PastaNetworkFiles:
    """Host paths for generated network files mounted into a jail."""

    resolv_conf: Path
    hosts: Path
    nsswitch_conf: Path


def pasta_user_net_config_lines() -> list[str]:
    """Return nsjail config lines for pasta-backed userspace networking."""
    return [
        "",
        "# Userspace networking via pasta - outbound access with network isolation",
        "user_net {",
        "  enable: true",
        f'  ip: "{PASTA_GUEST_IP}"',
        f'  gw: "{PASTA_GATEWAY_IP}"',
        f'  ip6: "{PASTA_GUEST_IP6}"',
        f'  gw6: "{PASTA_GATEWAY_IP6}"',
        "  enable_dns: true",
        "}",
    ]


def build_pasta_resolv_conf(
    host_resolv_path: Path = Path("/etc/resolv.conf"),
) -> str:
    """Build resolv.conf for a pasta-backed jail.

    Preserve the host resolver so Kubernetes service discovery keeps working
    inside the sandbox. Fall back to pasta's gateway resolver only when the
    host does not expose nameservers.
    """
    nameservers: list[str] = []
    options: list[str] = []
    try:
        host_resolv = host_resolv_path.read_text()
    except OSError:
        host_resolv = ""

    for line in host_resolv.splitlines():
        stripped = line.strip()
        if stripped.startswith("nameserver ") and not _is_loopback_nameserver(stripped):
            nameservers.append(stripped)
        elif stripped.startswith("search ") or stripped.startswith("options "):
            options.append(stripped)
    lines = nameservers or [f"nameserver {PASTA_GATEWAY_IP}"]
    lines.extend(options)
    return "\n".join(lines) + "\n"


def write_pasta_network_files(target_dir: Path) -> PastaNetworkFiles:
    """Write generated network config files for a pasta-enabled jail."""
    target_dir.mkdir(parents=True, exist_ok=True)

    resolv_conf = target_dir / "resolv.conf"
    hosts = target_dir / "hosts"
    nsswitch_conf = target_dir / "nsswitch.conf"

    resolv_conf.write_text(build_pasta_resolv_conf())
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

    return PastaNetworkFiles(
        resolv_conf=resolv_conf,
        hosts=hosts,
        nsswitch_conf=nsswitch_conf,
    )


def pasta_dns_mount_config_lines(files: PastaNetworkFiles) -> list[str]:
    """Return nsjail mount config lines for generated pasta DNS files."""
    return [
        "",
        "# Network config - DNS and hostname resolution via pasta",
        f'mount {{ src: "{files.resolv_conf}" dst: "/etc/resolv.conf" is_bind: true rw: false }}',
        f'mount {{ src: "{files.hosts}" dst: "/etc/hosts" is_bind: true rw: false }}',
        f'mount {{ src: "{files.nsswitch_conf}" dst: "/etc/nsswitch.conf" is_bind: true rw: false }}',
    ]
