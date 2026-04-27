from __future__ import annotations

from pathlib import Path

from tracecat.agent.sandbox.config import (
    AgentSandboxConfig,
    build_agent_nsjail_config,
)


def test_build_agent_nsjail_config_mounts_stable_claude_session_dirs() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        session_home_dir=Path("/tmp/tracecat-agent-session/claude-home"),
        session_project_dir=Path("/tmp/tracecat-agent-session/claude-project"),
    )

    assert 'dst: "/work/claude-home" is_bind: true rw: true' in config_text
    assert 'dst: "/work/claude-project" is_bind: true rw: true' in config_text


def test_build_agent_nsjail_config_mounts_workspace_skills_into_claude_home() -> None:
    config_text = build_agent_nsjail_config(
        rootfs=Path("/var/lib/tracecat/sandbox-rootfs"),
        job_dir=Path("/tmp/agent-job"),
        socket_dir=Path("/tmp/agent-job/sockets"),
        config=AgentSandboxConfig(),
        site_packages_dir=Path("/app/.venv/lib/python3.12/site-packages"),
        llm_socket_path=Path("/tmp/agent-job/sockets/llm.sock"),
        session_home_dir=Path("/tmp/tracecat-agent-session/claude-home"),
        session_project_dir=Path("/tmp/tracecat-agent-session/claude-project"),
        skills_dir=Path("/tmp/agent-job/home/.claude/skills"),
    )

    assert (
        'src: "/tmp/agent-job/home/.claude/skills" '
        'dst: "/work/claude-home/.claude/skills" is_bind: true rw: false'
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

    assert 'dst: "/var/run/tracecat/control.sock"' not in config_text


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
        'src: "/tmp/agent-job/sockets/mcp.sock" dst: "/var/run/tracecat/mcp.sock" '
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
    assert 'dst: "/var/run/tracecat" is_bind: true rw: false' not in config_text
    assert (
        'exec_bin { path: "/usr/local/bin/python3" arg: "/work/shim_entrypoint.py" }'
        in config_text
    )


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
