from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_network
from pathlib import Path
from typing import cast

import pytest

from tracecat import config as tracecat_config
from tracecat.agent.sandbox.config import AgentSandboxConfig, build_agent_nsjail_config
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.networking import (
    NSTUN_GATEWAY_IP4,
    NSTUN_GATEWAY_IP6,
    SandboxDnsRoute,
    build_sandbox_dns_config,
    configured_sandbox_network_policy,
    nstun_user_net_config_lines,
    resolve_sandbox_network_plan,
    write_sandbox_network_files,
)
from tracecat.sandbox.types import (
    SandboxBindMount,
    SandboxConfig,
    SandboxEgressRule,
    SandboxNetworkMode,
    SandboxNetworkPolicy,
    SandboxNetworkProtocol,
    SandboxNetworkPurpose,
    SandboxNetworkRequest,
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


def test_build_sandbox_dns_config_preserves_ipv4_link_local_resolver(
    tmp_path: Path,
) -> None:
    host_resolv = tmp_path / "host-resolv.conf"
    host_resolv.write_text("nameserver 169.254.20.10\n")

    dns_config = build_sandbox_dns_config(host_resolv)

    assert dns_config.resolv_conf == "nameserver 169.254.20.10\n"
    assert dns_config.routes == (
        SandboxDnsRoute(
            guest_address=IPv4Address("169.254.20.10"),
            host_address=IPv4Address("169.254.20.10"),
        ),
    )

    config_text = "\n".join(
        nstun_user_net_config_lines(SandboxNetworkPolicy(), dns_config.routes)
    )
    assert config_text.count('dst_ip: "169.254.20.10/32"') == 2
    assert config_text.index('dst_ip: "169.254.20.10/32"') < config_text.index(
        'dst_ip: "169.254.0.0/16"'
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
        allowed_rules=(
            SandboxEgressRule(
                destination=ip_network("10.42.0.0/16"),
                protocol=SandboxNetworkProtocol.TCP,
                destination_port=8443,
            ),
        ),
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
    assert (
        'action: ALLOW\n    proto: TCP\n    dst_ip: "10.42.0.0/16"\n    dport: 8443'
    ) in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "8.8.8.0/24"' in config_text
    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "0.0.0.0/0"' in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "::/0"' in config_text
    assert "PASTA" not in config_text
    assert "map_gw" not in config_text


def test_filtered_nstun_policy_rejects_all_ipv6_by_default() -> None:
    config_text = "\n".join(nstun_user_net_config_lines(SandboxNetworkPolicy()))

    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "::/0"' in config_text
    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "::/0"' not in config_text
    assert 'dst_ip: "fc00::/7"' not in config_text


@pytest.mark.parametrize(
    ("allow_public_ipv6", "expected_action"),
    [(False, "REJECT"), (True, "ALLOW")],
)
def test_filtered_nstun_policy_applies_ipv6_policy_to_public_dns(
    allow_public_ipv6: bool,
    expected_action: str,
) -> None:
    resolver = IPv6Address("2001:4860:4860::8888")
    dns_route = SandboxDnsRoute(
        guest_address=resolver,
        host_address=resolver,
    )
    policy = SandboxNetworkPolicy(allow_public_ipv6=allow_public_ipv6)

    config_text = "\n".join(nstun_user_net_config_lines(policy, (dns_route,)))

    assert f'dst_ip: "{resolver}/128"' not in config_text
    assert (
        f'action: {expected_action}\n    proto: ANY\n    dst_ip: "::/0"' in config_text
    )


def test_filtered_nstun_policy_allows_public_ipv6_when_opted_in() -> None:
    policy = SandboxNetworkPolicy(
        mode=SandboxNetworkMode.FILTERED,
        allowed_rules=(
            SandboxEgressRule(
                destination=ip_network("2001:db8:42::/48"),
                protocol=SandboxNetworkProtocol.TCP,
                destination_port=8443,
            ),
        ),
        allow_public_ipv6=True,
    )

    config_text = "\n".join(nstun_user_net_config_lines(policy))

    # Transition mechanisms embedding IPv4 stay blocked ahead of the allow.
    for blocked in (
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "2002::/16",
        "2001::/32",
        "fc00::/7",
        "fe80::/10",
    ):
        assert f'action: REJECT\n    proto: ANY\n    dst_ip: "{blocked}"' in (
            config_text
        )
    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "::/0"' in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "::/0"' not in config_text
    # Administrator exceptions still precede every reject.
    assert config_text.index('dst_ip: "2001:db8:42::/48"') < config_text.index(
        "action: REJECT"
    )
    # The catch-all IPv6 allow is evaluated after every reject.
    assert config_text.rindex("action: REJECT") < config_text.rindex(
        'action: ALLOW\n    proto: ANY\n    dst_ip: "::/0"'
    )


def test_configured_network_policy_reads_public_ipv6_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for flag in (True, False):
        monkeypatch.setattr(
            tracecat_config, "TRACECAT__SANDBOX_ALLOW_PUBLIC_IPV6_EGRESS", flag
        )

        policy = configured_sandbox_network_policy(SandboxNetworkPurpose.SCRIPT)

        assert policy.allow_public_ipv6 is flag


def test_non_filtered_policy_rejects_public_ipv6_flag() -> None:
    with pytest.raises(ValueError, match="only valid for filtered"):
        SandboxNetworkPolicy(
            mode=SandboxNetworkMode.UNRESTRICTED,
            allow_public_ipv6=True,
        )


def test_network_request_uses_absence_as_the_only_disabled_state() -> None:
    with pytest.raises(ValueError, match="omit the network request"):
        SandboxNetworkRequest(
            purpose=SandboxNetworkPurpose.SCRIPT,
            policy=SandboxNetworkPolicy(mode=SandboxNetworkMode.DISABLED),
        )


def test_resolve_network_plan_owns_policy_dns_and_mount_assembly(
    tmp_path: Path,
) -> None:
    host_resolv = tmp_path / "host-resolv.conf"
    host_resolv.write_text("nameserver 127.0.0.1\n")
    plan = resolve_sandbox_network_plan(
        tmp_path / "network",
        SandboxNetworkRequest(
            purpose=SandboxNetworkPurpose.SCRIPT,
            policy=SandboxNetworkPolicy(mode=SandboxNetworkMode.UNRESTRICTED),
        ),
        host_resolv_path=host_resolv,
    )

    config_text = "\n".join(plan.user_net_lines)
    mounts_text = "\n".join(plan.dns_mount_lines)
    assert "backend: NSTUN" in config_text
    assert f'dst_ip: "{NSTUN_GATEWAY_IP4}/32"' in config_text
    assert 'action: ALLOW\n    proto: ANY\n    dst_ip: "0.0.0.0/0"' in config_text
    assert f'src: "{tmp_path}/network/resolv.conf"' in mounts_text


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


def test_non_filtered_policy_rejects_egress_rules() -> None:
    with pytest.raises(ValueError, match="only valid for filtered"):
        SandboxNetworkPolicy(
            mode=SandboxNetworkMode.UNRESTRICTED,
            allowed_rules=(SandboxEgressRule(destination=IPv4Network("10.0.0.0/8")),),
        )


def test_network_policy_rejects_untyped_mode_values() -> None:
    with pytest.raises(ValueError, match="mode must be a SandboxNetworkMode"):
        SandboxNetworkPolicy(mode=cast(SandboxNetworkMode, "filtered"))


def test_nstun_policy_fails_closed_when_rule_limit_is_exceeded() -> None:
    allowed_rules = tuple(
        SandboxEgressRule(destination=IPv4Network(f"11.0.0.{index}/32"))
        for index in range(120)
    )
    policy = SandboxNetworkPolicy(allowed_rules=allowed_rules)

    with pytest.raises(ValueError, match="maximum is 128"):
        nstun_user_net_config_lines(policy)


def test_configured_network_policies_are_scoped_by_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose_config = {
        SandboxNetworkPurpose.INSTALL: (
            "TRACECAT__SANDBOX_INSTALL_ALLOWED_EGRESS_CIDRS",
            "TRACECAT__SANDBOX_INSTALL_ALLOWED_EGRESS_TCP_PORTS",
            IPv4Network("10.1.0.0/16"),
            8001,
        ),
        SandboxNetworkPurpose.SCRIPT: (
            "TRACECAT__SANDBOX_SCRIPT_ALLOWED_EGRESS_CIDRS",
            "TRACECAT__SANDBOX_SCRIPT_ALLOWED_EGRESS_TCP_PORTS",
            IPv4Network("10.2.0.0/16"),
            8002,
        ),
        SandboxNetworkPurpose.ACTION: (
            "TRACECAT__SANDBOX_ACTION_ALLOWED_EGRESS_CIDRS",
            "TRACECAT__SANDBOX_ACTION_ALLOWED_EGRESS_TCP_PORTS",
            IPv4Network("10.3.0.0/16"),
            8003,
        ),
        SandboxNetworkPurpose.AGENT: (
            "TRACECAT__SANDBOX_AGENT_ALLOWED_EGRESS_CIDRS",
            "TRACECAT__SANDBOX_AGENT_ALLOWED_EGRESS_TCP_PORTS",
            IPv4Network("10.4.0.0/16"),
            8004,
        ),
    }
    for _, (cidr_name, ports_name, network, port) in purpose_config.items():
        monkeypatch.setattr(tracecat_config, cidr_name, (network,))
        monkeypatch.setattr(tracecat_config, ports_name, (port,))

    for purpose, (_, _, network, port) in purpose_config.items():
        policy = configured_sandbox_network_policy(purpose)

        assert policy.allowed_rules == (
            SandboxEgressRule(
                destination=network,
                protocol=SandboxNetworkProtocol.TCP,
                destination_port=port,
            ),
        )


def test_configured_network_policy_rejects_untyped_purpose() -> None:
    with pytest.raises(ValueError, match="purpose must be a SandboxNetworkPurpose"):
        configured_sandbox_network_policy(cast(SandboxNetworkPurpose, "install"))


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
        network=SandboxNetworkRequest(SandboxNetworkPurpose.AGENT),
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
        config=SandboxConfig(
            network=SandboxNetworkRequest(SandboxNetworkPurpose.INSTALL)
        ),
    )

    assert "clone_newnet: true" in config_text
    assert "backend: NSTUN" in config_text
    assert 'action: REJECT\n    proto: ANY\n    dst_ip: "10.0.0.0/8"' in config_text
    assert f'src: "{tmp_path}/job/resolv.conf"' in config_text


def test_python_sandbox_execute_phase_respects_network_request(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    isolated_config = executor._build_config(
        job_dir=tmp_path / "isolated-job",
        phase="execute",
        config=SandboxConfig(),
    )
    networked_config = executor._build_config(
        job_dir=tmp_path / "networked-job",
        phase="execute",
        config=SandboxConfig(
            network=SandboxNetworkRequest(SandboxNetworkPurpose.SCRIPT)
        ),
    )

    assert "clone_newnet: true" in isolated_config
    assert "user_net {" not in isolated_config
    assert 'src: "/proc"' not in isolated_config
    assert 'dst: "/proc" fstype: "proc"' in isolated_config
    assert "clone_newnet: true" in networked_config
    assert "backend: NSTUN" in networked_config
    assert 'src: "/proc"' not in networked_config
    assert 'dst: "/proc" fstype: "proc"' in networked_config


def test_python_sandbox_config_mounts_phase_capabilities_read_only(
    tmp_path: Path,
) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()

    config_text = executor._build_config(
        job_dir=tmp_path / "job",
        phase="execute",
        config=SandboxConfig(
            bind_mounts=[
                SandboxBindMount(
                    source=agent_dir,
                    destination=Path("/run/registry-agent"),
                ),
                SandboxBindMount(
                    source=known_hosts,
                    destination=Path("/run/registry-ssh/known_hosts"),
                ),
            ]
        ),
    )

    assert (
        f'mount {{ src: "{agent_dir}" dst: "/run/registry-agent" '
        "is_bind: true rw: false }"
    ) in config_text
    assert (
        f'mount {{ src: "{known_hosts}" dst: "/run/registry-ssh/known_hosts" '
        "is_bind: true rw: false }"
    ) in config_text


def test_python_sandbox_scopes_install_and_script_private_egress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_INSTALL_ALLOWED_EGRESS_CIDRS",
        (IPv4Network("10.10.0.0/16"),),
    )
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_INSTALL_ALLOWED_EGRESS_TCP_PORTS",
        (8080,),
    )
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_SCRIPT_ALLOWED_EGRESS_CIDRS",
        (IPv4Network("10.20.0.0/16"),),
    )
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_SCRIPT_ALLOWED_EGRESS_TCP_PORTS",
        (8443,),
    )
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    install_config = executor._build_config(
        job_dir=tmp_path / "install-job",
        phase="install",
        config=SandboxConfig(
            network=SandboxNetworkRequest(SandboxNetworkPurpose.INSTALL)
        ),
    )
    script_config = executor._build_config(
        job_dir=tmp_path / "script-job",
        phase="execute",
        config=SandboxConfig(
            network=SandboxNetworkRequest(SandboxNetworkPurpose.SCRIPT)
        ),
    )

    assert 'dst_ip: "10.10.0.0/16"' in install_config
    assert "dport: 8080" in install_config
    assert 'dst_ip: "10.20.0.0/16"' not in install_config
    assert 'dst_ip: "10.20.0.0/16"' in script_config
    assert "dport: 8443" in script_config
    assert 'dst_ip: "10.10.0.0/16"' not in script_config


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


def test_action_and_agent_sandboxes_use_separate_private_egress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_ACTION_ALLOWED_EGRESS_CIDRS",
        (IPv4Network("10.30.0.0/16"),),
    )
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_ACTION_ALLOWED_EGRESS_TCP_PORTS",
        (9443,),
    )
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_AGENT_ALLOWED_EGRESS_CIDRS",
        (IPv4Network("10.40.0.0/16"),),
    )
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__SANDBOX_AGENT_ALLOWED_EGRESS_TCP_PORTS",
        (10443,),
    )
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    action_config = executor._build_action_config(
        job_dir=tmp_path / "action-job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
        ),
    )
    agent_config = build_agent_nsjail_config(
        rootfs=tmp_path / "rootfs",
        job_dir=tmp_path / "agent-job",
        socket_dir=tmp_path / "agent-socket",
        config=AgentSandboxConfig(),
        site_packages_dir=tmp_path / "site-packages",
        llm_socket_path=tmp_path / "llm.sock",
        network=SandboxNetworkRequest(SandboxNetworkPurpose.AGENT),
    )

    assert 'dst_ip: "10.30.0.0/16"' in action_config
    assert "dport: 9443" in action_config
    assert 'dst_ip: "10.40.0.0/16"' not in action_config
    assert 'dst_ip: "10.40.0.0/16"' in agent_config
    assert "dport: 10443" in agent_config
    assert 'dst_ip: "10.30.0.0/16"' not in agent_config


def test_action_sandbox_can_explicitly_disable_networking(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_action_config(
        job_dir=tmp_path / "job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
            network=None,
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
