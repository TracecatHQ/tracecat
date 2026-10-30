"""Returning an action's result before NsJail has torn the jail down."""

from __future__ import annotations

import asyncio
import stat
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor import action_runner
from tracecat.executor.action_runner import (
    ActionRunner,
    drain_deferred_sandbox_cleanups,
)
from tracecat.executor.schemas import ActionImplementation, ResolvedContext
from tracecat.executor.secret_preprocessors import SecretEnvProjection
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.types import RegistryLock
from tracecat.sandbox import executor as executor_module
from tracecat.sandbox import nsjail_protocol
from tracecat.sandbox.executor import (
    _WORKLOAD_DONE_MARKER,
    _WORKLOAD_STARTED_MARKER,
    ActionSandboxConfig,
    NsjailExecutor,
)
from tracecat.sandbox.nsjail_protocol import NsjailCompletedProcess, invoke_nsjail
from tracecat.sandbox.types import SandboxResult


def _fake_nsjail(tmp_path: Path, script: str) -> Path:
    """An executable standing in for NsJail; it ignores its arguments."""
    path = tmp_path / "fake-nsjail"
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _marker(marker: bytes) -> str:
    return "".join(f"\\{byte:03o}" for byte in marker)


async def _invoke(
    tmp_path: Path, script: str, *, grace: float = 5.0
) -> NsjailCompletedProcess:
    job_dir = tmp_path / "job"
    job_dir.mkdir(exist_ok=True)
    return await invoke_nsjail(
        nsjail_path=_fake_nsjail(tmp_path, script),
        job_dir=job_dir,
        config_text="mode: ONCE",
        env={},
        timeout_seconds=10,
        timeout_message="synthetic timeout",
        workload_started_marker=_WORKLOAD_STARTED_MARKER,
        workload_done_marker=_WORKLOAD_DONE_MARKER,
        done_exit_grace_seconds=grace,
    )


@pytest.mark.anyio
async def test_done_marker_returns_before_nsjail_exits(tmp_path: Path) -> None:
    """The host has the output as soon as the runner says done; teardown follows."""
    script = (
        f"printf '{_marker(_WORKLOAD_STARTED_MARKER)}workload says hi'  >&2\n"
        f"printf '{_marker(_WORKLOAD_DONE_MARKER)}' >&2\n"
        "sleep 3  # NsJail tearing the jail down"
    )
    started = time.monotonic()
    completed = await _invoke(tmp_path, script)
    returned_after = time.monotonic() - started

    assert completed.finish is not None
    assert returned_after < 2
    assert completed.returncode is None
    assert completed.workload_started
    assert completed.stderr == b"workload says hi"
    assert (tmp_path / "job" / "nsjail.cfg").exists()

    final = await completed.finish
    assert final.returncode == 0
    assert final.finish is None
    assert final.stderr == b"workload says hi"
    assert not (tmp_path / "job" / "nsjail.cfg").exists()


@pytest.mark.anyio
async def test_jail_that_lingers_after_done_is_killed_after_the_grace(
    tmp_path: Path,
) -> None:
    """A forged early marker cannot keep a jail running outside its slot."""
    script = f"printf '{_marker(_WORKLOAD_DONE_MARKER)}' >&2\nsleep 30"
    completed = await _invoke(tmp_path, script, grace=0.2)
    assert completed.finish is not None

    started = time.monotonic()
    final = await asyncio.wait_for(completed.finish, timeout=5)

    assert time.monotonic() - started < 2
    assert final.returncode is not None and final.returncode != 0


@pytest.mark.anyio
async def test_done_marker_hands_the_jail_to_finish_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing the caller could be cancelled at sits between the marker and finish.

    The early record skips the NsJail log scan, so a slow scan cannot hold the
    hand-off open; the finish task still scans before it completes.
    """
    scans: list[float] = []

    def slow_scan(log_file: object) -> nsjail_protocol.NsjailLogScan:
        time.sleep(0.5)
        scans.append(time.monotonic())
        return nsjail_protocol.NsjailLogScan(tail=b"", nsjail_signaled=False)

    monkeypatch.setattr(nsjail_protocol, "scan_nsjail_log", slow_scan)
    script = f"printf '{_marker(_WORKLOAD_DONE_MARKER)}' >&2\nsleep 0.1"
    completed = await _invoke(tmp_path, script)

    assert completed.finish is not None
    assert scans == []
    await completed.finish
    assert len(scans) == 1


@pytest.mark.anyio
async def test_exit_without_done_marker_completes_normally(tmp_path: Path) -> None:
    completed = await _invoke(tmp_path, "echo out; exit 3")

    assert completed.finish is None
    assert completed.returncode == 3
    assert completed.stdout == b"out\n"
    assert not (tmp_path / "job" / "nsjail.cfg").exists()


# --- NsjailExecutor.execute_action ---


def _early(finish: asyncio.Task[NsjailCompletedProcess]) -> NsjailCompletedProcess:
    return NsjailCompletedProcess(
        returncode=None,
        stdout=b"",
        stderr=b"",
        workload_started=True,
        nsjail_log=b"",
        nsjail_signaled=False,
        finish=finish,
    )


def _exited(returncode: int) -> NsjailCompletedProcess:
    return NsjailCompletedProcess(
        returncode=returncode,
        stdout=b"",
        stderr=b"",
        workload_started=True,
        nsjail_log=b"",
        nsjail_signaled=False,
    )


async def _execute_with_early_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_file: bytes | None,
    final_returncode: int,
) -> tuple[NsjailExecutor, SandboxResult, asyncio.Event]:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    if result_file is not None:
        (job_dir / "result.json").write_bytes(result_file)
    exited = asyncio.Event()

    async def wait_for_exit() -> NsjailCompletedProcess:
        await exited.wait()
        return _exited(final_returncode)

    async def fake_invoke_nsjail(**_kwargs: object) -> NsjailCompletedProcess:
        return _early(asyncio.create_task(wait_for_exit()))

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )
    config = ActionSandboxConfig(registry_paths=[], tracecat_app_dir=tmp_path)
    task = asyncio.create_task(executor.execute_action(job_dir, config))
    await asyncio.sleep(0.05)
    if not task.done():
        exited.set()
    return executor, await task, exited


@pytest.mark.anyio
async def test_valid_early_result_is_returned_with_teardown_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, result, exited = await _execute_with_early_done(
        tmp_path,
        monkeypatch,
        orjson.dumps({"success": True, "result": {"probe": 1}}),
        final_returncode=0,
    )

    assert result.success and result.output == {"probe": 1}
    assert not exited.is_set()
    assert executor.pending_finish is not None and not executor.pending_finish.done()
    exited.set()
    await executor.pending_finish


@pytest.mark.anyio
async def test_early_marker_without_a_valid_result_waits_for_the_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forged marker gets today's classification, from the real exit status."""
    executor, result, exited = await _execute_with_early_done(
        tmp_path, monkeypatch, result_file=None, final_returncode=1
    )

    assert exited.is_set()
    assert not result.success
    assert result.exit_code == 1
    assert executor.pending_finish is None


@pytest.mark.anyio
async def test_cancel_before_finish_starts_still_waits_for_the_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled caller never cancels the finish task out from under the jail.

    Cancelling a task before its first step skips its body, and with it the
    reap. The caller lands on that task in the same step the marker arrives,
    so the cancellation must wait for the jail instead.
    """
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    exited = asyncio.Event()
    started: list[bool] = []
    finishes: list[asyncio.Task[NsjailCompletedProcess]] = []

    async def wait_for_exit() -> NsjailCompletedProcess:
        started.append(True)
        await exited.wait()
        return _exited(1)

    async def fake_invoke_nsjail(**_kwargs: object) -> NsjailCompletedProcess:
        finishes.append(asyncio.create_task(wait_for_exit()))
        return _early(finishes[0])

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )
    config = ActionSandboxConfig(registry_paths=[], tracecat_app_dir=tmp_path)
    task = asyncio.create_task(executor.execute_action(job_dir, config))
    await asyncio.sleep(0)  # execute_action reaches the finish task, unstarted
    task.cancel()
    await asyncio.sleep(0.05)

    assert started == [True]
    assert not task.done()
    exited.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finishes[0].done() and not finishes[0].cancelled()


@pytest.mark.anyio
async def test_early_decode_failure_waits_for_the_exit_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error never reaches the caller while the jail is still running."""

    def broken_decode(*_args: object) -> None:
        raise RuntimeError("decode failed")

    exited = asyncio.Event()
    finishes: list[asyncio.Task[NsjailCompletedProcess]] = []

    async def wait_for_exit() -> NsjailCompletedProcess:
        await exited.wait()
        return _exited(0)

    async def fake_invoke_nsjail(**_kwargs: object) -> NsjailCompletedProcess:
        finishes.append(asyncio.create_task(wait_for_exit()))
        return _early(finishes[0])

    monkeypatch.setattr(executor_module, "invoke_nsjail", fake_invoke_nsjail)
    monkeypatch.setattr(executor_module, "_decode_action_outcome", broken_decode)
    executor = NsjailExecutor(
        nsjail_path=str(tmp_path / "nsjail"),
        rootfs_path=str(tmp_path / "rootfs"),
        cache_dir=str(tmp_path / "cache"),
    )
    config = ActionSandboxConfig(registry_paths=[], tracecat_app_dir=tmp_path)
    task = asyncio.create_task(executor.execute_action(tmp_path, config))
    await asyncio.sleep(0.05)

    assert not task.done()
    exited.set()
    with pytest.raises(RuntimeError, match="decode failed"):
        await task
    assert finishes[0].done()
    assert executor.pending_finish is None


# --- ActionRunner defers the job dir until the jail has exited ---


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-executor"],
    )


def _run_input() -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(action="core.transform.reshape", args={}, ref="a"),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "1.0.0"},
            actions={"core.transform.reshape": "tracecat_registry"},
        ),
    )


@pytest.mark.anyio
async def test_job_dir_and_leases_outlive_the_call_until_the_jail_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The jail still mounts its job dir until NsJail exits; then it goes first."""
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "cgroup.subtree_control").write_text("memory")
    (cgroup / "cgroup.procs").touch()
    monkeypatch.setenv("TRACECAT__SANDBOX_CGROUP_PATH", str(cgroup))
    exited = asyncio.Event()
    job_dirs: list[Path] = []
    order: list[str] = []

    async def wait_for_exit() -> NsjailCompletedProcess:
        await exited.wait()
        return _exited(0)

    async def execute_action(
        executor: NsjailExecutor, job_dir: Path, _config: ActionSandboxConfig
    ) -> SandboxResult:
        job_dirs.append(job_dir)
        executor.pending_finish = asyncio.create_task(wait_for_exit())
        return SandboxResult(success=True, output="ok")

    monkeypatch.setattr(action_runner.NsjailExecutor, "execute_action", execute_action)

    class RegistryLease:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: object) -> None:
            order.append(
                f"registry lease released, job dir exists={job_dirs[0].exists()}"
            )

    role = _role()
    run_input = _run_input()
    async with action_runner.contextlib.AsyncExitStack() as lease_scope:
        await lease_scope.enter_async_context(RegistryLease())
        result = await ActionRunner(cache_dir=tmp_path / "cache")._execute_sandboxed(
            input=run_input,
            role=role,
            registry_paths=[],
            secret_projection=SecretEnvProjection(env={}, mask_values=set()),
            resolved_context=ResolvedContext(
                action_impl=ActionImplementation(
                    type="udf",
                    action_name="core.transform.reshape",
                    module="tracecat_registry.core.transform",
                    name="reshape",
                ),
                evaluated_args={"value": 1},
                workspace_id=str(role.workspace_id),
                workflow_id=str(run_input.run_context.wf_id),
                run_id=str(run_input.run_context.wf_run_id),
                executor_token="token",
            ),
            lease_scope=lease_scope,
        )

    assert result == "ok"
    assert job_dirs[0].exists()
    assert order == []

    exited.set()
    await drain_deferred_sandbox_cleanups()

    assert not job_dirs[0].exists()
    assert order == ["registry lease released, job dir exists=False"]
