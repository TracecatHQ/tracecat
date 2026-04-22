"""Tests for nsjail seccomp policy generation."""

from __future__ import annotations

from pathlib import Path

from tracecat.agent.sandbox.config import AgentSandboxConfig, build_agent_nsjail_config
from tracecat.executor.backends.pool import WorkerPool
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.seccomp import build_untrusted_seccomp_policy
from tracecat.sandbox.types import SandboxConfig

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
