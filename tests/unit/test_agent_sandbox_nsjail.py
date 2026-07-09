from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.common.config import JAILED_OTEL_SOCKET_PATH
from tracecat.agent.sandbox.config import AgentSandboxConfig
from tracecat.agent.sandbox.nsjail import (
    SESSION_HOME_ENV_VAR,
    SESSION_WORK_DIR_ENV_VAR,
    _spawn_direct_runtime,
    _spawn_nsjail_runtime,
)


@pytest.mark.anyio
async def test_spawn_direct_runtime_sets_explicit_agent_session_paths(
    tmp_path: Path,
) -> None:
    session_home_dir = tmp_path / "agent-home"
    session_work_dir = tmp_path / "agent-work-dir"
    mock_process = MagicMock()

    with patch(
        "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_process),
    ) as create_subprocess_exec:
        await _spawn_direct_runtime(
            socket_dir=tmp_path / "sockets",
            llm_socket_path=tmp_path / "sockets" / "llm.sock",
            mcp_socket_path=tmp_path / "sockets" / "mcp.sock",
            init_payload_path=tmp_path / "init.json",
            control_socket_required=True,
            pipe_stdin=False,
            session_home_dir=session_home_dir,
            session_work_dir=session_work_dir,
            skills_dir=None,
        )

    assert create_subprocess_exec.await_args is not None
    cmd = create_subprocess_exec.await_args.args
    assert cmd[0].endswith("python")
    assert cmd[1].endswith("tracecat/agent/sandbox/shim_entrypoint.py")
    env = create_subprocess_exec.await_args.kwargs["env"]
    assert env["HOME"] == str(session_home_dir)
    assert env["TRACECAT__AGENT_MCP_SOCKET_PATH"] == str(
        tmp_path / "sockets" / "mcp.sock"
    )
    assert create_subprocess_exec.await_args.kwargs["pass_fds"] == ()
    assert env[SESSION_HOME_ENV_VAR] == str(session_home_dir)
    assert env[SESSION_WORK_DIR_ENV_VAR] == str(session_work_dir)
    assert session_home_dir.is_dir()
    assert session_work_dir.is_dir()


@pytest.mark.anyio
async def test_spawn_direct_runtime_passes_inherited_fds(tmp_path: Path) -> None:
    mock_process = MagicMock()

    with patch(
        "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_process),
    ) as create_subprocess_exec:
        await _spawn_direct_runtime(
            socket_dir=tmp_path / "sockets",
            llm_socket_path=tmp_path / "sockets" / "llm.sock",
            mcp_socket_path=tmp_path / "sockets" / "mcp.sock",
            init_payload_path=tmp_path / "init.json",
            control_socket_required=False,
            pipe_stdin=True,
            session_home_dir=None,
            session_work_dir=None,
            skills_dir=None,
            inherited_fds=(42,),
        )

    assert create_subprocess_exec.await_args is not None
    assert create_subprocess_exec.await_args.kwargs["pass_fds"] == (42,)


@pytest.mark.anyio
async def test_spawn_nsjail_runtime_uses_mounted_otel_socket_path(
    tmp_path: Path,
) -> None:
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    nsjail = tmp_path / "nsjail"
    nsjail.touch()
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    llm_socket_path = socket_dir / "llm.sock"
    llm_socket_path.touch()
    mcp_socket_path = socket_dir / "mcp.sock"
    mcp_socket_path.touch()
    otel_socket_path = socket_dir / "otel.sock"
    otel_socket_path.touch()
    init_payload_path = tmp_path / "init.json"
    init_payload_path.write_text("{}")
    job_dir = tmp_path / "job"
    mock_process = MagicMock()

    with patch(
        "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
        AsyncMock(return_value=mock_process),
    ) as create_subprocess_exec:
        await _spawn_nsjail_runtime(
            socket_dir=socket_dir,
            llm_socket_path=llm_socket_path,
            mcp_socket_path=mcp_socket_path,
            init_payload_path=init_payload_path,
            config=AgentSandboxConfig(),
            nsjail_path=str(nsjail),
            rootfs_path=str(rootfs),
            control_socket_required=False,
            pipe_stdin=False,
            job_dir=job_dir,
            session_home_dir=None,
            session_work_dir=None,
            skills_dir=None,
            otel_socket_path=otel_socket_path,
        )

    assert create_subprocess_exec.await_args is not None
    cmd = create_subprocess_exec.await_args.args
    jailed_socket_env = f"TRACECAT__AGENT_OTEL_SOCKET_PATH={JAILED_OTEL_SOCKET_PATH}"
    assert jailed_socket_env in cmd
    assert create_subprocess_exec.await_args.kwargs["env"][
        "TRACECAT__AGENT_OTEL_SOCKET_PATH"
    ] == str(JAILED_OTEL_SOCKET_PATH)
    assert (
        f'mount {{ src: "{otel_socket_path}" dst: "{JAILED_OTEL_SOCKET_PATH}" '
        "is_bind: true rw: false }"
    ) in (job_dir / "nsjail.cfg").read_text()
