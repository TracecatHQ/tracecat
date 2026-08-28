"""Tests for nsjail seccomp policy generation."""

from __future__ import annotations

from pathlib import Path

from tracecat.agent.sandbox.config import (
    AgentResourceLimits,
    AgentSandboxConfig,
    build_agent_env_map,
    build_agent_nsjail_config,
)
from tracecat.sandbox.executor import ActionSandboxConfig, NsjailExecutor
from tracecat.sandbox.seccomp import build_untrusted_seccomp_policy
from tracecat.sandbox.types import ResourceLimits, SandboxConfig
from tracecat.sandbox.wrapper import INSTALL_SCRIPT, WRAPPER_SCRIPT

NPROC_ENV_VAR = "TRACECAT__SANDBOX_RLIMIT_NPROC"

_EXPECTED_BLOCKED_SYSCALLS = (
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "mount",
    "setns",
    "unshare",
    "keyctl",
    "bpf",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
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
    # clone3 is denied with ENOSYS so glibc's clone3->clone fallback triggers
    # cleanly (single-errno-per-policy Kafel limitation: comma-separated rule).
    assert "ERRNO(38) { clone3 }" in policy
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


def test_python_install_uses_job_local_uv_cache(tmp_path: Path) -> None:
    """Install sandboxes must not share a globally writable uv cache."""
    job_dir = tmp_path / "job"
    executor = NsjailExecutor(
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "shared-cache"),
    )

    config_text = executor._build_config(
        job_dir=job_dir,
        phase="install",
        config=SandboxConfig(),
    )
    env_map = executor._build_env_map(SandboxConfig(), "install")

    assert str(tmp_path / "shared-cache" / "uv-cache") not in config_text
    assert (
        f'mount {{ src: "{job_dir}" dst: "/work" is_bind: true rw: false }}'
        in config_text
    )
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


def test_python_execute_env_injects_nproc_limit(tmp_path: Path) -> None:
    """Both phases must inject the process cap for their trusted entrypoints.

    nsjail cannot enforce rlimit_nproc under clone_newuser, so the wrapper
    (execute) and install script (install) apply it from this injected value
    before untrusted code runs. Install needs it too: uv executes arbitrary
    build backends of user-selected source dependencies.
    """
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    config = SandboxConfig(resources=ResourceLimits(max_processes=64))

    execute_env = executor._build_env_map(config, "execute", cache_key=None)
    install_env = executor._build_env_map(config, "install", cache_key=None)

    assert execute_env[NPROC_ENV_VAR] == "64"
    assert install_env[NPROC_ENV_VAR] == "64"


def test_action_env_injects_nproc_limit(tmp_path: Path) -> None:
    """Action sandboxes must inject the process cap for minimal_runner."""
    executor = NsjailExecutor(rootfs_path=str(tmp_path / "rootfs"))
    config = ActionSandboxConfig(
        registry_paths=[tmp_path / "registry"],
        tracecat_app_dir=tmp_path / "app",
        resources=ResourceLimits(max_processes=77),
    )

    env_map = executor._build_action_env_map(config)

    assert env_map[NPROC_ENV_VAR] == "77"


def test_agent_env_map_injects_nproc_limit(tmp_path: Path) -> None:
    """Agent sandboxes must inject the process cap for the jailed shim."""
    config = AgentSandboxConfig(resources=AgentResourceLimits(max_processes=123))

    env_map = build_agent_env_map(config)

    assert env_map[NPROC_ENV_VAR] == "123"


def test_trusted_entrypoints_enforce_nproc_limit() -> None:
    """All jailed entrypoints must apply the injected process cap."""
    from tracecat.agent.sandbox import shim_entrypoint
    from tracecat.executor import minimal_runner

    assert "setrlimit" in WRAPPER_SCRIPT
    assert NPROC_ENV_VAR in WRAPPER_SCRIPT
    assert "setrlimit" in INSTALL_SCRIPT
    assert NPROC_ENV_VAR in INSTALL_SCRIPT

    runner_source = Path(minimal_runner.__file__).read_text()
    assert "setrlimit" in runner_source
    assert NPROC_ENV_VAR in runner_source

    shim_source = Path(shim_entrypoint.__file__).read_text()
    assert "setrlimit" in shim_source
    assert NPROC_ENV_VAR in shim_source
