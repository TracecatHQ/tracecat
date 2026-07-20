"""Tests for nsjail seccomp policy generation."""

from __future__ import annotations

from pathlib import Path

from tracecat.agent.sandbox.config import (
    AgentResourceLimits,
    AgentSandboxConfig,
    build_agent_nsjail_config,
)
from tracecat.executor.backends.pool import WorkerPool
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.seccomp import build_untrusted_seccomp_policy
from tracecat.sandbox.types import ResourceLimits, SandboxConfig

_EXPECTED_BLOCKED_SYSCALLS = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "mount",
    "setns",
    "unshare",
    "keyctl",
    "bpf",
)


def _assert_seccomp_config(config_text: str) -> None:
    """Assert a generated nsjail config contains Tracecat's seccomp policy."""
    policy = build_untrusted_seccomp_policy()
    assert f'seccomp_string: "{policy}"' in config_text
    for syscall in _EXPECTED_BLOCKED_SYSCALLS:
        assert syscall in config_text


def test_build_untrusted_seccomp_policy_blocks_expected_syscalls():
    """The shared seccomp policy should deny tracing and kernel-facing syscalls."""
    policy = build_untrusted_seccomp_policy()

    assert policy.startswith("POLICY tracecat_untrusted")
    assert "ERRNO(1)" in policy
    assert policy.endswith("DEFAULT ALLOW")
    for syscall in _EXPECTED_BLOCKED_SYSCALLS:
        assert syscall in policy


def test_python_sandbox_config_includes_seccomp_policy(tmp_path: Path):
    """General Python sandbox configs should emit the shared seccomp policy."""
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_config(
        job_dir=tmp_path / "job",
        phase="execute",
        config=SandboxConfig(),
    )

    _assert_seccomp_config(config_text)


def test_action_sandbox_config_includes_seccomp_policy(tmp_path: Path):
    """Action sandbox configs should emit the shared seccomp policy."""
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    config_text = executor._build_action_config(
        job_dir=tmp_path / "job",
        config=ActionSandboxConfig(
            registry_paths=[tmp_path / "registry"],
            tracecat_app_dir=tmp_path / "app",
        ),
    )

    _assert_seccomp_config(config_text)


def test_agent_sandbox_config_includes_seccomp_policy(tmp_path: Path):
    """Agent sandbox configs should emit the shared seccomp policy."""
    config_text = build_agent_nsjail_config(
        rootfs=tmp_path / "rootfs",
        job_dir=tmp_path / "job",
        socket_dir=tmp_path / "socket",
        config=AgentSandboxConfig(),
        site_packages_dir=tmp_path / "site-packages",
        llm_socket_path=tmp_path / "llm.sock",
    )

    _assert_seccomp_config(config_text)


def test_worker_pool_config_includes_seccomp_policy(tmp_path: Path):
    """Warm worker pool configs should emit the shared seccomp policy."""
    pool = WorkerPool()

    config_text = pool._build_nsjail_config(
        worker_id=1,
        work_dir=tmp_path / "work",
    )

    _assert_seccomp_config(config_text)


def test_worker_pool_config_mounts_action_gateway_socket(tmp_path: Path):
    """Warm nsjail workers should mount the action gateway socket when enabled."""
    pool = WorkerPool()
    action_gateway_socket = tmp_path / "action-gateway.sock"

    config_text = pool._build_nsjail_config(
        worker_id=1,
        work_dir=tmp_path / "work",
        action_gateway_socket=action_gateway_socket,
    )

    assert (
        f'mount {{ src: "{action_gateway_socket}" dst: "/var/run/tracecat/action-gateway.sock" '
        "is_bind: true rw: false }"
    ) in config_text


def test_python_install_uses_job_local_uv_cache(tmp_path: Path) -> None:
    """Install sandboxes must not share a globally writable uv cache."""
    executor = NsjailExecutor(
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "shared-cache"),
    )

    config_text = executor._build_config(
        job_dir=tmp_path / "job",
        phase="install",
        config=SandboxConfig(),
    )
    env_map = executor._build_env_map(SandboxConfig(), "install")

    assert str(tmp_path / "shared-cache" / "uv-cache") not in config_text
    assert env_map["UV_CACHE_DIR"] == "/cache/uv-cache"


def test_nsjail_configs_use_resource_limit_megabyte_units(tmp_path: Path) -> None:
    """nsjail's rlimit_as and rlimit_fsize protobuf fields are in MiB."""
    resources = ResourceLimits(memory_mb=321, max_file_size_mb=45)
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))

    python_config = executor._build_config(
        job_dir=tmp_path / "python-job",
        phase="execute",
        config=SandboxConfig(resources=resources),
    )
    action_config = executor._build_action_config(
        job_dir=tmp_path / "action-job",
        config=ActionSandboxConfig(
            registry_paths=[],
            tracecat_app_dir=tmp_path / "app",
            resources=resources,
        ),
    )
    agent_config = build_agent_nsjail_config(
        rootfs=tmp_path / "rootfs",
        job_dir=tmp_path / "agent-job",
        socket_dir=tmp_path / "socket",
        config=AgentSandboxConfig(
            resources=AgentResourceLimits(memory_mb=321, max_file_size_mb=45)
        ),
        site_packages_dir=tmp_path / "site-packages",
        llm_socket_path=tmp_path / "llm.sock",
    )

    for config_text in (python_config, action_config, agent_config):
        assert "rlimit_as: 321" in config_text
        assert "rlimit_fsize: 45" in config_text
        assert f"rlimit_as: {321 * 1024 * 1024}" not in config_text
        assert f"rlimit_fsize: {45 * 1024 * 1024}" not in config_text


def test_worker_pool_uses_procfs_scoped_to_pid_namespace(tmp_path: Path) -> None:
    """Warm workers must not bind the executor container's existing procfs."""
    pool = WorkerPool()

    config_text = pool._build_nsjail_config(
        worker_id=1,
        work_dir=tmp_path / "work",
    )

    assert 'mount { dst: "/proc" fstype: "proc" rw: false }' in config_text
    assert 'src: "/proc"' not in config_text
