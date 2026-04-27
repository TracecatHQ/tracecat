from __future__ import annotations

from pathlib import Path

from tracecat.agent.sandbox.config import AgentSandboxConfig, build_agent_nsjail_config
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.networking import (
    PASTA_GATEWAY_IP,
    build_pasta_resolv_conf,
    write_pasta_network_files,
)
from tracecat.sandbox.types import SandboxConfig


def test_build_pasta_resolv_conf_preserves_search_and_options(tmp_path: Path) -> None:
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

    resolv_conf = build_pasta_resolv_conf(host_resolv)

    assert resolv_conf == (
        f"nameserver {PASTA_GATEWAY_IP}\n"
        "search default.svc.cluster.local svc.cluster.local\n"
        "options ndots:5 timeout:2 attempts:3\n"
    )


def test_write_pasta_network_files_writes_hostname_resolution_files(
    tmp_path: Path,
) -> None:
    network_files = write_pasta_network_files(tmp_path)

    assert network_files.resolv_conf.read_text().startswith(
        f"nameserver {PASTA_GATEWAY_IP}\n"
    )
    assert "127.0.0.1\tlocalhost" in network_files.hosts.read_text()
    assert "hosts:          files dns" in network_files.nsswitch_conf.read_text()


def test_agent_nsjail_config_keeps_network_isolated_without_pasta(
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


def test_agent_nsjail_config_enables_pasta_for_internet_access(
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
    assert "user_net {" in config_text
    assert f'gw: "{PASTA_GATEWAY_IP}"' in config_text
    assert f'src: "{tmp_path}/socket/resolv.conf"' in config_text
    assert 'src: "/etc/resolv.conf"' not in config_text


def test_python_sandbox_install_phase_enables_pasta(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_config(
        job_dir=tmp_path / "job",
        phase="install",
        config=SandboxConfig(network_enabled=False),
    )

    assert "clone_newnet: true" in config_text
    assert "user_net {" in config_text
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
    assert "user_net {" in networked_config
    assert 'src: "/proc"' not in networked_config
    assert 'dst: "/proc" fstype: "proc"' in networked_config


def test_action_sandbox_config_enables_pasta(tmp_path: Path) -> None:
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_action_config(
        job_dir=tmp_path / "job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
        ),
    )

    assert "clone_newnet: true" in config_text
    assert "user_net {" in config_text
    assert f'src: "{tmp_path}/job/resolv.conf"' in config_text
    assert 'src: "/proc"' not in config_text
    assert 'dst: "/proc" fstype: "proc"' in config_text
