from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.sandbox.nsjail import (
    SESSION_HOME_ENV_VAR,
    SESSION_PROJECT_ENV_VAR,
    _spawn_direct_runtime,
)


@pytest.mark.anyio
async def test_spawn_direct_runtime_sets_explicit_claude_session_paths(
    tmp_path: Path,
) -> None:
    session_home_dir = tmp_path / "claude-home"
    session_project_dir = tmp_path / "claude-project"
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
            session_project_dir=session_project_dir,
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
    assert env[SESSION_HOME_ENV_VAR] == str(session_home_dir)
    assert env[SESSION_PROJECT_ENV_VAR] == str(session_project_dir)
    assert session_home_dir.is_dir()
    assert session_project_dir.is_dir()
