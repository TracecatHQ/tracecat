from __future__ import annotations

from pathlib import Path

import pytest

from tracecat.agent.common.config import AGENT_RUNTIME_PROTECTED_ENV_VARS
from tracecat.agent.common.exceptions import AgentSandboxValidationError
from tracecat.agent.sandbox.config import (
    AgentSandboxConfig,
    build_agent_env_map,
    build_agent_nsjail_config,
)


def test_build_agent_nsjail_config_separates_job_and_agent_dirs() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        session_home_dir=Path("/tmp/tracecat-agent-session/agent-home"),
        session_work_dir=Path("/tmp/tracecat-agent-session/agent-work-dir"),
    )

    assert (
        'mount { dst: "/run/tracecat" fstype: "tmpfs" rw: true options: "size=1M" }'
        in config_text
    )
    assert 'dst: "/run/tracecat/job" is_bind: true rw: false' in config_text
    assert (
        'src: "/tmp/agent-job/uv-state" dst: "/run/tracecat/uv-state" '
        "is_bind: true rw: true"
    ) in config_text
    assert 'dst: "/work" is_bind: true rw: true' in config_text
    assert 'dst: "/home/agent" is_bind: true rw: true' in config_text
    assert 'src: "/tmp/agent-job" dst: "/work"' not in config_text


def test_build_agent_env_map_protects_job_scoped_uv_state() -> None:
    env = build_agent_env_map(AgentSandboxConfig())

    expected_uv_env = {
        "UV_CACHE_DIR": "/run/tracecat/uv-state/cache",
        "UV_CREDENTIALS_DIR": "/run/tracecat/uv-state/credentials",
        "UV_LINK_MODE": "copy",
        "UV_PYTHON_BIN_DIR": "/run/tracecat/uv-state/bin",
        "UV_PYTHON_CACHE_DIR": "/run/tracecat/uv-state/python-cache",
        "UV_PYTHON_INSTALL_DIR": "/run/tracecat/uv-state/python",
        "UV_TOOL_BIN_DIR": "/run/tracecat/uv-state/bin",
        "UV_TOOL_DIR": "/run/tracecat/uv-state/tools",
    }
    assert {key: env[key] for key in expected_uv_env} == expected_uv_env
    assert AGENT_RUNTIME_PROTECTED_ENV_VARS == frozenset(expected_uv_env)


@pytest.mark.parametrize("env_key", sorted(AGENT_RUNTIME_PROTECTED_ENV_VARS))
def test_build_agent_env_map_rejects_uv_state_overrides(env_key: str) -> None:
    with pytest.raises(
        AgentSandboxValidationError,
        match=f"Cannot override protected env var: {env_key}",
    ):
        build_agent_env_map(AgentSandboxConfig(env_vars={env_key: "/tmp/shared"}))


def test_build_agent_nsjail_config_mounts_workspace_skills_into_agent_home() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        session_home_dir=Path("/tmp/tracecat-agent-session/agent-home"),
        session_work_dir=Path("/tmp/tracecat-agent-session/agent-work-dir"),
        skills_dir=Path("/tmp/agent-job/home/.claude/skills"),
    )

    assert (
        'src: "/tmp/agent-job/home/.claude/skills" '
        'dst: "/home/agent/.claude/skills" is_bind: true rw: false'
    ) in config_text


def test_build_agent_nsjail_config_can_skip_control_socket_mount() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        mount_control_socket=False,
    )

    assert 'dst: "/run/tracecat/control.sock"' not in config_text


def test_build_agent_nsjail_config_mounts_trusted_mcp_socket() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        mcp_socket_path=Path("/tmp/agent-job/sockets/mcp.sock"),
    )

    assert (
        'src: "/tmp/agent-job/sockets/mcp.sock" dst: "/run/tracecat/mcp.sock" '
        "is_bind: true rw: false"
    ) in config_text


def test_build_agent_nsjail_config_uses_reduced_broker_shim_mounts() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        mount_control_socket=False,
    )

    assert 'dst: "/site-packages" fstype: "tmpfs"' in config_text
    assert (
        'src: "/app/.venv/lib/python3.12/site-packages/claude_agent_sdk" '
        'dst: "/site-packages/claude_agent_sdk" is_bind: true rw: false'
    ) in config_text
    assert (
        'src: "/app/.venv/lib/python3.12/site-packages" dst: "/site-packages"'
        not in config_text
    )
    assert 'dst: "/app" fstype: "tmpfs"' not in config_text
    assert 'dst: "/run/tracecat" is_bind: true rw: false' not in config_text
    assert (
        'exec_bin { path: "/usr/local/bin/python3" arg: "/run/tracecat/job/shim_entrypoint.py" }'
        in config_text
    )
    assert 'cwd: "/run/tracecat/job"' in config_text


def test_build_agent_nsjail_config_mounts_fresh_procfs() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
    )

    assert 'src: "/proc"' not in config_text
    assert 'mount { dst: "/proc" fstype: "proc" rw: false }' in config_text
