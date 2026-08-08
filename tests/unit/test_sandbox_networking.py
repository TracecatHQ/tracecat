from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_network
from pathlib import Path
from typing import cast

import pytest

from tracecat.agent.sandbox.config import AgentSandboxConfig, build_agent_nsjail_config
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.networking import (
    NSTUN_GATEWAY_IP4,
    NSTUN_GATEWAY_IP6,
    SandboxDnsRoute,
    build_sandbox_dns_config,
    nstun_user_net_config_lines,
    write_sandbox_network_files,
)
from tracecat.sandbox.types import (
    SandboxConfig,
    SandboxNetworkMode,
    SandboxNetworkPolicy,
)


def test_build_sandbox_dns_config_preserves_non_loopback_resolver(
    tmp_path: Path,
) -> None:
    host_resolv = tmp_path / "host-resolv.conf"
    host_resolv.write_text(
        "\n".join(
            [
                "# managed by runtime",
                "nameserver 10.0.0.10",
                "search default.svc.cluster.local svc.cluster.local",
                "options ndots:5 timeout:2 attempts:3",
                "",
            ]
        )
    )

    dns_config = build_sandbox_dns_config(host_resolv)

    assert dns_config.resolv_conf == (
        "nameserver 10.0.0.10\n"
        "search default.svc.cluster.local svc.cluster.local\n"
        "options ndots:5 timeout:2 attempts:3\n"
    )
    assert dns_config.routes == (
        SandboxDnsRoute(
            guest_address=IPv4Address("10.0.0.10"),
            host_address=IPv4Address("10.0.0.10"),
        ),
    )


def test_build_sandbox_dns_config_redirects_parent_loopback_resolvers(
    tmp_path: Path,
) -> None:
    host_resolv = tmp_path / "host-resolv.conf"
    host_resolv.write_text(
        "nameserver 127.0.0.11\nnameserver ::1\nsearch svc.cluster.local\n"
    )

    dns_config = build_sandbox_dns_config(host_resolv)

    assert dns_config.resolv_conf == (
        f"nameserver {NSTUN_GATEWAY_IP4}\n"
        f"nameserver {NSTUN_GATEWAY_IP6}\n"
        "search svc.cluster.local\n"
    )
    assert dns_config.routes == (
        SandboxDnsRoute(
            guest_address=IPv4Address(NSTUN_GATEWAY_IP4),
            host_address=IPv4Address("127.0.0.11"),
        ),
        SandboxDnsRoute(
            guest_address=IPv6Address(NSTUN_GATEWAY_IP6),
            host_address=IPv6Address("::1"),
        ),
    )


def test_build_sandbox_dns_config_omits_unusable_nameservers(
    tmp_path: Path,
) -> None:
    host_resolv = tmp_path / "host-resolv.conf"
    host_resolv.write_text(
        "nameserver invalid\nnameserver 0.0.0.0\nnameserver fe80::1%eth0\n"
        "options attempts:1\n"
    )

    dns_config = build_sandbox_dns_config(host_resolv)

    assert dns_config.resolv_conf == "options attempts:1\n"
    assert dns_config.routes == ()


def test_write_sandbox_network_files_writes_hostname_resolution_files(
    tmp_path: Path,
) -> None:
    host_resolv = tmp_path / "host-resolv.conf"
    host_resolv.write_text("nameserver 127.0.0.11\n")

    network_files = write_sandbox_network_files(tmp_path / "job", host_resolv)

    assert network_files.resolv_conf.read_text() == (
        f"nameserver {NSTUN_GATEWAY_IP4}\n"
    )
    assert "127.0.0.1\tlocalhost" in network_files.hosts.read_text()
    assert "hosts:          files dns" in network_files.nsswitch_conf.read_text()
    assert len(network_files.dns_routes) == 1


def test_filtered_nstun_policy_orders_dns_and_exceptions_before_blocks() -> None:
    policy = SandboxNetworkPolicy(
        mode=SandboxNetworkMode.FILTERED,
        allowed_cidrs=(ip_network("10.42.0.0/16"),),
        blocked_cidrs=(ip_network("8.8.8.0/24"),),
    )
    dns_route = SandboxDnsRoute(
        guest_address=IPv4Address("10.96.0.10"),
        host_address=IPv4Address("10.96.0.10"),
    )

    config_text = "\n".join(nstun_user_net_config_lines(policy, (dns_route,)))

    assert "backend: NSTUN" in config_text
    assert config_text.count('dst_ip: "10.96.0.10/32"') == 2
    assert "proto: UDP" in config_text
    assert "proto: TCP" in config_text
    assert "dport: 53" in config_text
    assert config_text.index('dst_ip: "10.96.0.10/32"') < config_text.index(
        'dst_ip: "10.0.0.0/8"'
    )
    assert config_text.index('dst_ip: "10.42.0.0/16"') < config_text.index(
        'dst_ip: "10.0.0.0/8"'
    )
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "8.8.8.0/24"' in config_text
    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "0.0.0.0/0"' in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "::/0"' in config_text
    assert "PASTA" not in config_text
    assert "map_gw" not in config_text


def test_filtered_nstun_policy_redirects_only_dns_to_parent_loopback() -> None:
    dns_route = SandboxDnsRoute(
        guest_address=IPv4Address(NSTUN_GATEWAY_IP4),
        host_address=IPv4Address("127.0.0.11"),
    )

    config_text = "\n".join(
        nstun_user_net_config_lines(SandboxNetworkPolicy(), (dns_route,))
    )

    assert config_text.count("action: REDIRECT") == 2
    assert config_text.count('redirect_ip: "127.0.0.11"') == 2
    assert config_text.count("redirect_port: 53") == 2
    assert config_text.count("dport: 53") == 2


def test_unrestricted_nstun_policy_explicitly_allows_both_address_families() -> None:
    policy = SandboxNetworkPolicy(mode=SandboxNetworkMode.UNRESTRICTED)

    config_text = "\n".join(nstun_user_net_config_lines(policy))

    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "0.0.0.0/0"' in config_text
    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "::/0"' in config_text
    assert "action: REJECT" not in config_text


def test_disabled_nstun_policy_omits_user_network() -> None:
    policy = SandboxNetworkPolicy(mode=SandboxNetworkMode.DISABLED)

    assert nstun_user_net_config_lines(policy) == []


def test_non_filtered_policy_rejects_cidr_rules() -> None:
    with pytest.raises(ValueError, match="only valid for filtered"):
        SandboxNetworkPolicy(
            mode=SandboxNetworkMode.UNRESTRICTED,
            allowed_cidrs=(IPv4Network("10.0.0.0/8"),),
        )


def test_network_policy_rejects_untyped_mode_values() -> None:
    with pytest.raises(ValueError, match="mode must be a SandboxNetworkMode"):
        SandboxNetworkPolicy(mode=cast(SandboxNetworkMode, "filtered"))


def test_nstun_policy_fails_closed_when_rule_limit_is_exceeded() -> None:
    allowed_cidrs = tuple(IPv4Network(f"11.0.0.{index}/32") for index in range(120))
    policy = SandboxNetworkPolicy(allowed_cidrs=allowed_cidrs)

    with pytest.raises(ValueError, match="maximum is 128"):
        nstun_user_net_config_lines(policy)


def test_agent_nsjail_config_keeps_network_isolated_without_user_net(
    tmp_path: Path,
) -> None:
    config_text = build_agent_nsjail_config(
        rootfs=tmp_path / "rootfs",
        job_dir=tmp_path / "job",
        socket_dir=tmp_path / "socket",
        config=AgentSandboxConfig(),
        site_packages_dir=tmp_path / "site-packages",
        llm_socket_path=tmp_path / "llm.sock",
    )

    assert "clone_newnet: true" in config_text
    assert "user_net {" not in config_text
    assert 'src: "/proc"' not in config_text
    assert 'dst: "/proc" fstype: "proc"' in config_text


def test_agent_nsjail_config_enables_filtered_nstun_for_internet_access(
    tmp_path: Path,
) -> None:
    config_text = build_agent_nsjail_config(
        rootfs=tmp_path / "rootfs",
        job_dir=tmp_path / "job",
        socket_dir=tmp_path / "socket",
        config=AgentSandboxConfig(),
        site_packages_dir=tmp_path / "site-packages",
        llm_socket_path=tmp_path / "llm.sock",
        enable_internet_access=True,
    )

    assert "clone_newnet: true" in config_text
    assert "backend: NSTUN" in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "10.0.0.0/8"' in config_text
    assert f'gw4: "{NSTUN_GATEWAY_IP4}"' in config_text
    assert f'src: "{tmp_path}/socket/resolv.conf"' in config_text
    assert 'src: "/etc/resolv.conf"' not in config_text


def test_python_sandbox_install_phase_enables_filtered_nstun(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_config(
        job_dir=tmp_path / "job",
        phase="install",
        config=SandboxConfig(network_enabled=False),
    )

    assert "clone_newnet: true" in config_text
    assert "backend: NSTUN" in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "10.0.0.0/8"' in config_text
    assert f'src: "{tmp_path}/job/resolv.conf"' in config_text


def test_python_sandbox_execute_phase_respects_network_flag(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    isolated_config = executor._build_config(
        job_dir=tmp_path / "isolated-job",
        phase="execute",
        config=SandboxConfig(network_enabled=False),
    )
    networked_config = executor._build_config(
        job_dir=tmp_path / "networked-job",
        phase="execute",
        config=SandboxConfig(network_enabled=True),
    )

    assert "clone_newnet: true" in isolated_config
    assert "user_net {" not in isolated_config
    assert 'src: "/proc"' not in isolated_config
    assert 'dst: "/proc" fstype: "proc"' in isolated_config
    assert "clone_newnet: true" in networked_config
    assert "backend: NSTUN" in networked_config
    assert 'src: "/proc"' not in networked_config
    assert 'dst: "/proc" fstype: "proc"' in networked_config


def test_action_sandbox_config_enables_filtered_nstun(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_action_config(
        job_dir=tmp_path / "job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
        ),
    )

    assert "clone_newnet: true" in config_text
    assert "backend: NSTUN" in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "10.0.0.0/8"' in config_text
    assert f'src: "{tmp_path}/job/resolv.conf"' in config_text
    assert 'src: "/proc"' not in config_text
    assert 'dst: "/proc" fstype: "proc"' in config_text


def test_action_sandbox_can_explicitly_disable_networking(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_action_config(
        job_dir=tmp_path / "job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
            network_policy=SandboxNetworkPolicy(
                mode=SandboxNetworkMode.DISABLED,
            ),
        ),
    )

    assert "clone_newnet: true" in config_text
    assert "user_net {" not in config_text
    assert f'src: "{tmp_path}/job/resolv.conf"' not in config_text


def test_action_sandbox_config_mounts_action_gateway_socket(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    action_gateway_socket = tmp_path / "action-gateway.sock"

    config_text = executor._build_action_config(
        job_dir=tmp_path / "job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
            action_gateway_socket=action_gateway_socket,
            action_gateway_socket_mount_path=Path(
                "/var/run/tracecat/action-gateway.sock"
            ),
        ),
    )

    assert (
        f'mount {{ src: "{action_gateway_socket}" dst: "/var/run/tracecat/action-gateway.sock" '
        "is_bind: true rw: false }"
    ) in config_text
