from __future__ import annotations

import shutil
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.agent.common.config import build_agent_runtime_uv_env
from tracecat.agent.runtime.session_paths import (
    JAILED_AGENT_UV_STATE_DIR,
    job_uv_state_dir,
)
from tracecat.agent.sandbox import nsjail as nsjail_module
from tracecat.agent.sandbox.config import AgentSandboxConfig
from tracecat.agent.sandbox.nsjail import (
    SESSION_HOME_ENV_VAR,
    SESSION_WORK_DIR_ENV_VAR,
    SpawnedRuntime,
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
    expected_uv_env = build_agent_runtime_uv_env(tmp_path / "uv-state")
    assert {key: env[key] for key in expected_uv_env} == expected_uv_env
    assert env["TRACECAT__AGENT_MCP_SOCKET_PATH"] == str(
        tmp_path / "sockets" / "mcp.sock"
    )
    assert create_subprocess_exec.await_args.kwargs["pass_fds"] == ()
    assert env[SESSION_HOME_ENV_VAR] == str(session_home_dir)
    assert env[SESSION_WORK_DIR_ENV_VAR] == str(session_work_dir)
    assert session_home_dir.is_dir()
    assert session_work_dir.is_dir()
    assert (tmp_path / "uv-state").is_dir()


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
    assert (owned_job_dir / "uv-state").is_dir()
    assert create_subprocess_exec.await_args is not None
    env = create_subprocess_exec.await_args.kwargs["env"]
    expected_uv_env = build_agent_runtime_uv_env(owned_job_dir / "uv-state")
    assert {key: env[key] for key in expected_uv_env} == expected_uv_env

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
async def test_spawn_nsjail_runtime_mounts_job_scoped_uv_state(
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

    uv_state_dir = job_dir / "uv-state"
    assert uv_state_dir.is_dir()
    assert result.job_dir is None
    assert create_subprocess_exec.await_args is not None
    env = create_subprocess_exec.await_args.kwargs["env"]
    expected_uv_env = build_agent_runtime_uv_env(Path("/run/tracecat/uv-state"))
    assert {key: env[key] for key in expected_uv_env} == expected_uv_env
    assert (
        f'src: "{uv_state_dir}" dst: "/run/tracecat/uv-state" is_bind: true rw: true'
    ) in (job_dir / "nsjail.cfg").read_text()


@pytest.mark.anyio
async def test_direct_runtime_isolates_uv_state_across_two_jobs(
    tmp_path: Path,
) -> None:
    """Direct runtimes point all seven UV paths at distinct per-job state trees."""
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    job_dirs = (tmp_path / "job-a", tmp_path / "job-b")
    mock_process = MagicMock()

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ) as create_subprocess_exec,
    ):
        for job_dir in job_dirs:
            await spawn_jailed_runtime(
                socket_dir=socket_dir,
                init_payload_path=tmp_path / "init.json",
                control_socket_required=False,
                pipe_stdin=True,
                job_dir=job_dir,
            )

    assert len(create_subprocess_exec.await_args_list) == 2
    for job_dir, call in zip(
        job_dirs, create_subprocess_exec.await_args_list, strict=True
    ):
        uv_state_dir = job_uv_state_dir(job_dir)
        env = call.kwargs["env"]
        expected_uv_env = build_agent_runtime_uv_env(uv_state_dir)
        assert {key: env[key] for key in expected_uv_env} == expected_uv_env
        uv_path_values = [
            Path(value)
            for key, value in expected_uv_env.items()
            if key != "UV_LINK_MODE"
        ]
        assert len(uv_path_values) == 7
        assert all(path.is_relative_to(uv_state_dir) for path in uv_path_values)

    job_a_marker = job_uv_state_dir(job_dirs[0]) / "cache" / "job-a.marker"
    job_a_marker.parent.mkdir(parents=True, exist_ok=True)
    job_a_marker.write_text("job a")
    job_b_marker = job_uv_state_dir(job_dirs[1]) / "cache" / job_a_marker.name

    assert job_uv_state_dir(job_dirs[0]) != job_uv_state_dir(job_dirs[1])
    assert job_a_marker.is_file()
    assert not job_b_marker.exists()


@pytest.mark.anyio
async def test_nsjail_runtime_isolates_two_jobs_by_uv_mount_source(
    tmp_path: Path,
) -> None:
    """NSJail jobs share the jailed UV path but bind distinct host sources."""
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
    init_payload_path = tmp_path / "init.json"
    init_payload_path.write_text("{}")
    job_dirs = (tmp_path / "job-a", tmp_path / "job-b")
    mock_process = MagicMock()

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", False),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=mock_process),
        ) as create_subprocess_exec,
    ):
        for job_dir in job_dirs:
            await spawn_jailed_runtime(
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
            )

    expected_jailed_env = build_agent_runtime_uv_env(JAILED_AGENT_UV_STATE_DIR)
    assert len(create_subprocess_exec.await_args_list) == 2
    for call in create_subprocess_exec.await_args_list:
        env = call.kwargs["env"]
        assert {key: env[key] for key in expected_jailed_env} == expected_jailed_env

    mount_lines: list[str] = []
    for job_dir in job_dirs:
        config_lines = (job_dir / "nsjail.cfg").read_text().splitlines()
        matching_lines = [
            line.strip()
            for line in config_lines
            if f'dst: "{JAILED_AGENT_UV_STATE_DIR}"' in line
        ]
        assert len(matching_lines) == 1
        mount_line = matching_lines[0]
        assert f'src: "{job_uv_state_dir(job_dir)}"' in mount_line
        mount_lines.append(mount_line)

    assert mount_lines[0] != mount_lines[1]


@pytest.mark.anyio
async def test_spawn_creates_uv_state_with_owner_only_permissions(
    tmp_path: Path,
) -> None:
    """A newly created per-job UV state directory has mode 0700."""
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    job_dir = tmp_path / "job"

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        await spawn_jailed_runtime(
            socket_dir=socket_dir,
            init_payload_path=tmp_path / "init.json",
            control_socket_required=False,
            pipe_stdin=True,
            job_dir=job_dir,
        )

    assert stat.S_IMODE(job_uv_state_dir(job_dir).stat().st_mode) == 0o700


@pytest.mark.anyio
async def test_spawn_normalizes_preexisting_uv_state_permissions(
    tmp_path: Path,
) -> None:
    """Spawn normalizes a pre-existing UV state directory to owner-only mode."""
    socket_dir = tmp_path / "sockets"
    socket_dir.mkdir()
    job_dir = tmp_path / "job"
    uv_state_dir = job_uv_state_dir(job_dir)
    uv_state_dir.mkdir(parents=True)
    uv_state_dir.chmod(0o777)

    with (
        patch("tracecat.agent.sandbox.nsjail.TRACECAT__DISABLE_NSJAIL", True),
        patch(
            "tracecat.agent.sandbox.nsjail.asyncio.create_subprocess_exec",
            AsyncMock(return_value=MagicMock()),
        ),
    ):
        await spawn_jailed_runtime(
            socket_dir=socket_dir,
            init_payload_path=tmp_path / "init.json",
            control_socket_required=False,
            pipe_stdin=True,
            job_dir=job_dir,
        )

    assert stat.S_IMODE(uv_state_dir.stat().st_mode) == 0o700


def test_cleanup_spawned_runtime_swallows_rmtree_error_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runtime-owned cleanup logs and swallows a raising rmtree call."""
    job_dir = tmp_path / "job"
    job_uv_state_dir(job_dir).mkdir(parents=True)
    result = SpawnedRuntime(process=MagicMock(), job_dir=job_dir)
    real_rmtree = shutil.rmtree
    warning = MagicMock()

    def fail_rmtree(path: Path) -> None:
        assert path == job_dir
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr("tracecat.agent.common.fs.shutil.rmtree", fail_rmtree)
    monkeypatch.setattr(nsjail_module.logger, "warning", warning)

    try:
        cleanup_spawned_runtime(result)

        assert job_dir.exists()
        warning.assert_called_once_with(
            "Failed to clean up job dir",
            job_dir=str(job_dir),
            error="simulated cleanup failure",
        )
    finally:
        real_rmtree(job_dir, ignore_errors=True)


def test_cleanup_spawned_runtime_removes_read_only_uv_subtree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runtime cleanup removes a sandbox-owned 0555 UV cache subtree."""
    job_dir = tmp_path / "job"
    read_only_dir = job_uv_state_dir(job_dir) / "cache" / "read-only"
    nested_file = read_only_dir / "archive" / "artifact"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("cached artifact")
    read_only_dir.chmod(0o555)
    result = SpawnedRuntime(process=MagicMock(), job_dir=job_dir)
    warning = MagicMock()
    monkeypatch.setattr(nsjail_module.logger, "warning", warning)

    try:
        cleanup_spawned_runtime(result)

        assert not job_dir.exists()
        assert not read_only_dir.exists()
        warning.assert_not_called()
    finally:
        if read_only_dir.exists():
            read_only_dir.chmod(0o700)
        shutil.rmtree(job_dir, ignore_errors=True)
