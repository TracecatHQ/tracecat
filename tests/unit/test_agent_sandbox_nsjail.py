from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.sandbox.config import AgentSandboxConfig
from tracecat.agent.sandbox.nsjail import (
    SESSION_HOME_ENV_VAR,
    SESSION_WORK_DIR_ENV_VAR,
    cleanup_spawned_runtime,
    spawn_jailed_runtime,
)


@pytest.mark.anyio
async def test_spawn_direct_runtime_sets_explicit_agent_session_paths(
    tmp_path: Path,
) -> None:
    session_home_dir = tmp_path / "agent-home"
    session_work_dir = tmp_path / "agent-work-dir"
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    mock_process = MagicMock()

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ) as create_subprocess_exec,
    ):
        await spawn_jailed_runtime(
            socket_dir=socket_dir,
            llm_socket_path=socket_dir / "llm.sock",
            mcp_socket_path=socket_dir / "mcp.sock",
            init_payload_path=tmp_path / "init.json",
            control_socket_required=True,
            pipe_stdin=False,
            job_dir=tmp_path,
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
    assert env["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
    assert env["UV_LINK_MODE"] == "copy"
    assert env["TRACECAT__AGENT_MCP_SOCKET_PATH"] == str(
        tmp_path / "sockets" / "mcp.sock"
    )
    assert create_subprocess_exec.await_args.kwargs["pass_fds"] == ()
    assert env[SESSION_HOME_ENV_VAR] == str(session_home_dir)
    assert env[SESSION_WORK_DIR_ENV_VAR] == str(session_work_dir)
    assert session_home_dir.is_dir()
    assert session_work_dir.is_dir()
    assert (tmp_path / "uv-cache").is_dir()


@pytest.mark.anyio
async def test_spawn_direct_runtime_passes_inherited_fds(tmp_path: Path) -> None:
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    mock_process = MagicMock()

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ) as create_subprocess_exec,
    ):
        await spawn_jailed_runtime(
            socket_dir=socket_dir,
            llm_socket_path=socket_dir / "llm.sock",
            mcp_socket_path=socket_dir / "mcp.sock",
            init_payload_path=tmp_path / "init.json",
            control_socket_required=False,
            pipe_stdin=True,
            job_dir=tmp_path,
            session_home_dir=None,
            session_work_dir=None,
            skills_dir=None,
            inherited_fds=(42,),
        )

    assert create_subprocess_exec.await_args is not None
    assert create_subprocess_exec.await_args.kwargs["pass_fds"] == (42,)


@pytest.mark.anyio
async def test_spawn_direct_runtime_owns_implicit_job_directory(
    tmp_path: Path,
) -> None:
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    mock_process = MagicMock()

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ) as create_subprocess_exec,
    ):
        result = await spawn_jailed_runtime(
            socket_dir=socket_dir,
            init_payload_path=tmp_path / "init.json",
            control_socket_required=False,
            pipe_stdin=True,
        )

    assert result.job_dir is not None
    owned_job_dir = result.job_dir
    assert owned_job_dir.is_dir()
    assert (owned_job_dir / "uv-cache").is_dir()
    assert create_subprocess_exec.await_args is not None
    env = create_subprocess_exec.await_args.kwargs["env"]
    assert env["UV_CACHE_DIR"] == str(owned_job_dir / "uv-cache")

    cleanup_spawned_runtime(result)
    assert not owned_job_dir.exists()


@pytest.mark.anyio
async def test_spawn_direct_runtime_cleans_implicit_job_directory_on_failure(
    tmp_path: Path,
) -> None:
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    owned_job_dir = tmp_path / "owned-job"

    def create_job_dir(*_args: object, **_kwargs: object) -> str:
        owned_job_dir.mkdir()
        return str(owned_job_dir)

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.tempfile.mkdtemp",
            side_effect=create_job_dir,
        ),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(side_effect=OSError("spawn failed")),
        ),
        pytest.raises(OSError, match="spawn failed"),
    ):
        await spawn_jailed_runtime(
            socket_dir=socket_dir,
            init_payload_path=tmp_path / "init.json",
            control_socket_required=False,
            pipe_stdin=True,
        )

    assert not owned_job_dir.exists()


@pytest.mark.anyio
async def test_spawn_nsjail_runtime_mounts_job_scoped_uv_cache(
    tmp_path: Path,
) -> None:
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    nsjail_path = tmp_path / "nsjail"
    nsjail_path.touch()
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    llm_socket_path = socket_dir / "llm.sock"
    llm_socket_path.touch()
    mcp_socket_path = socket_dir / "mcp.sock"
    mcp_socket_path.touch()
    init_payload_path = tmp_path / "source-init.json"
    init_payload_path.write_text("{}")
    job_dir = tmp_path / "job"
    mock_process = MagicMock()

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", False),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ) as create_subprocess_exec,
    ):
        result = await spawn_jailed_runtime(
            socket_dir=socket_dir,
            llm_socket_path=llm_socket_path,
            mcp_socket_path=mcp_socket_path,
            init_payload_path=init_payload_path,
            config=AgentSandboxConfig(),
            nsjail_path=str(nsjail_path),
            rootfs_path=str(rootfs),
            control_socket_required=False,
            pipe_stdin=False,
            job_dir=job_dir,
            session_home_dir=None,
            session_work_dir=None,
        )

    uv_cache_dir = job_dir / "uv-cache"
    assert uv_cache_dir.is_dir()
    assert result.job_dir is None
    assert create_subprocess_exec.await_args is not None
    env = create_subprocess_exec.await_args.kwargs["env"]
    assert env["UV_CACHE_DIR"] == "/run/tracecat/uv-cache"
    assert env["UV_LINK_MODE"] == "copy"
    assert (
        f'src: "{uv_cache_dir}" dst: "/run/tracecat/uv-cache" is_bind: true rw: true'
    ) in (job_dir / "nsjail.cfg").read_text()
