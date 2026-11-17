"""The worker must reject missing delegation before accepting activities."""

import errno
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, call

import pytest

from tracecat.agent import executor_worker as agent_worker
from tracecat.agent.sandbox import cgroup as agent_cgroup
from tracecat.executor import worker as executor_worker
from tracecat.executor.enums import ExecutorBackendType
from tracecat.sandbox import cgroup
from tracecat.sandbox.cgroup import CGROUP_PATH_ENV, sandbox_cgroup


def test_missing_delegation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        sandbox_cgroup()


@pytest.mark.anyio
@pytest.mark.parametrize("backend", ["nsjail", "ephemeral"])
@pytest.mark.parametrize(
    "worker", [executor_worker, agent_worker], ids=["executor", "agent"]
)
async def test_worker_rejects_missing_delegation(
    backend: str,
    worker: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = tmp_path / "ready"
    ready.touch()
    monkeypatch.setattr("tracecat.executor.startup.WORKER_READY_FILE", ready)
    monkeypatch.setattr(
        "tracecat.executor.startup.validate_execution_backend", lambda: None
    )
    monkeypatch.setattr(
        cgroup.config,
        "TRACECAT__EXECUTOR_BACKEND",
        ExecutorBackendType.from_config(backend),
    )
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    monkeypatch.delenv(agent_cgroup.CGROUP_PATH_ENV, raising=False)
    initialize_tracing = Mock()
    monkeypatch.setattr(worker, "initialize_platform_tracing", initialize_tracing)
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        await worker.main()
    initialize_tracing.assert_not_called()
    assert not ready.exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "worker", [executor_worker, agent_worker], ids=["executor", "agent"]
)
async def test_direct_worker_does_not_require_delegation(
    worker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tracecat.executor.startup.WORKER_READY_FILE", tmp_path / "ready"
    )
    monkeypatch.setattr(
        cgroup.config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.DIRECT
    )
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "false")
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    monkeypatch.delenv(agent_cgroup.CGROUP_PATH_ENV, raising=False)
    # Stop at the first service initialization, after startup validation.
    initialize_tracing = Mock(side_effect=RuntimeError("reached service startup"))
    monkeypatch.setattr(worker, "initialize_platform_tracing", initialize_tracing)
    with pytest.raises(RuntimeError, match="reached service startup"):
        await worker.main()
    initialize_tracing.assert_called_once()


def test_memory_controller_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CGROUP_PATH_ENV, str(tmp_path))
    (tmp_path / "cgroup.subtree_control").write_text("cpu pids")
    with pytest.raises(RuntimeError, match="Memory controller is not enabled"):
        sandbox_cgroup()


@pytest.fixture
def bootstrap_process(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace privilege changes and exec while keeping real subtree validation."""
    process = Mock()
    monkeypatch.setattr(cgroup.os, "getuid", lambda: 0)
    for name in ("setgroups", "setgid", "setuid", "execvp"):
        operation = Mock()
        process.attach_mock(operation, name)
        monkeypatch.setattr(cgroup.os, name, operation)
    validation = Mock(wraps=sandbox_cgroup)
    process.attach_mock(validation, "validate")
    monkeypatch.setattr(cgroup, "sandbox_cgroup", validation)
    monkeypatch.setattr(cgroup.sys, "argv", ["cgroup", "python", "-m", "worker"])
    monkeypatch.setattr(
        cgroup.config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.NSJAIL
    )
    # The removed flag must not disable cgroup delegation.
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")
    monkeypatch.setenv(CGROUP_PATH_ENV, "")
    for name in ("HOME", "USER", "LOGNAME"):
        monkeypatch.setenv(name, "original")
    return process


@pytest.mark.parametrize("backend", ["nsjail", "ephemeral"])
@pytest.mark.parametrize("configured", [None, "", "explicit"])
def test_bootstrap_selects_and_validates_subtree_as_apiuser(
    backend: str,
    configured: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_process: Mock,
) -> None:
    monkeypatch.setattr(
        cgroup.config,
        "TRACECAT__EXECUTOR_BACKEND",
        ExecutorBackendType.from_config(backend),
    )
    (tmp_path / "cgroup.subtree_control").write_text("memory")
    (tmp_path / "cgroup.procs").touch()
    if configured is not None:
        monkeypatch.setenv(CGROUP_PATH_ENV, str(tmp_path) if configured else "")
    delegate = Mock(return_value=tmp_path)
    monkeypatch.setattr(cgroup, "delegate_cgroup", delegate)

    cgroup.main()

    if configured:
        delegate.assert_not_called()
    else:
        delegate.assert_called_once_with(cgroup.APIUSER_ID, cgroup.APIUSER_ID)
    assert cgroup.os.environ[CGROUP_PATH_ENV] == str(tmp_path)
    assert bootstrap_process.mock_calls == [
        call.setgroups([]),
        call.setgid(cgroup.APIUSER_ID),
        call.setuid(cgroup.APIUSER_ID),
        call.validate(CGROUP_PATH_ENV),
        call.execvp("python", ["python", "-m", "worker"]),
    ]


@pytest.mark.parametrize("memory_enabled", [False, True])
def test_bootstrap_rejects_unusable_explicit_subtree(
    memory_enabled: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_process: Mock,
) -> None:
    monkeypatch.setenv(CGROUP_PATH_ENV, str(tmp_path))
    (tmp_path / "cgroup.subtree_control").write_text(
        "memory" if memory_enabled else "cpu"
    )
    delegate = Mock()
    monkeypatch.setattr(cgroup, "delegate_cgroup", delegate)
    monkeypatch.setattr(cgroup.os, "access", lambda path, mode: False)

    expected_error = PermissionError if memory_enabled else RuntimeError
    with pytest.raises(expected_error):
        cgroup.main()

    delegate.assert_not_called()
    assert cgroup.os.environ[CGROUP_PATH_ENV] == str(tmp_path)
    assert bootstrap_process.mock_calls == [
        call.setgroups([]),
        call.setgid(cgroup.APIUSER_ID),
        call.setuid(cgroup.APIUSER_ID),
        call.validate(CGROUP_PATH_ENV),
    ]


def test_bootstrap_skips_cgroups_in_direct_mode(
    monkeypatch: pytest.MonkeyPatch, bootstrap_process: Mock
) -> None:
    monkeypatch.setattr(
        cgroup.config, "TRACECAT__EXECUTOR_BACKEND", ExecutorBackendType.DIRECT
    )
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "false")
    delegate = Mock()
    monkeypatch.setattr(cgroup, "delegate_cgroup", delegate)

    cgroup.main()

    delegate.assert_not_called()
    bootstrap_process.validate.assert_not_called()
    bootstrap_process.execvp.assert_called_once_with(
        "python", ["python", "-m", "worker"]
    )


@pytest.mark.parametrize("shared_path", [None, "", "explicit"])
def test_agent_bootstrap_preserves_its_delegation_contract(
    shared_path: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_process: Mock,
) -> None:
    monkeypatch.setenv(agent_cgroup.CGROUP_PATH_ENV, "")
    (tmp_path / "cgroup.subtree_control").write_text("memory")
    (tmp_path / "cgroup.procs").touch()
    shared_root = tmp_path / "python"
    shared_root.mkdir()
    (shared_root / "cgroup.subtree_control").write_text("memory")
    (shared_root / "cgroup.procs").touch()
    if shared_path is None:
        monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    else:
        monkeypatch.setenv(CGROUP_PATH_ENV, str(shared_root) if shared_path else "")
    monkeypatch.setattr(cgroup, "delegate_cgroup", Mock(return_value=tmp_path))

    agent_cgroup.main()

    assert cgroup.os.environ[agent_cgroup.CGROUP_PATH_ENV] == str(tmp_path)
    assert cgroup.os.environ[CGROUP_PATH_ENV] == str(
        shared_root if shared_path else tmp_path
    )
    assert bootstrap_process.mock_calls == [
        call.setgroups([]),
        call.setgid(cgroup.APIUSER_ID),
        call.setuid(cgroup.APIUSER_ID),
        call.validate(agent_cgroup.CGROUP_PATH_ENV),
        call.validate(CGROUP_PATH_ENV),
        call.execvp("python", ["python", "-m", "worker"]),
    ]


@pytest.mark.parametrize("memory_enabled", [False, True])
def test_agent_bootstrap_rejects_unusable_shared_subtree(
    memory_enabled: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_process: Mock,
) -> None:
    monkeypatch.setenv(agent_cgroup.CGROUP_PATH_ENV, str(tmp_path))
    (tmp_path / "cgroup.subtree_control").write_text("memory")
    (tmp_path / "cgroup.procs").touch()
    shared_root = tmp_path / "python"
    shared_root.mkdir()
    (shared_root / "cgroup.subtree_control").write_text(
        "memory" if memory_enabled else "cpu"
    )
    (shared_root / "cgroup.procs").touch()
    monkeypatch.setenv(CGROUP_PATH_ENV, str(shared_root))
    monkeypatch.setattr(cgroup.os, "access", lambda path, mode: path != shared_root)

    expected_error = PermissionError if memory_enabled else RuntimeError
    with pytest.raises(expected_error):
        agent_cgroup.main()

    assert cgroup.os.environ[CGROUP_PATH_ENV] == str(shared_root)
    assert bootstrap_process.mock_calls == [
        call.setgroups([]),
        call.setgid(cgroup.APIUSER_ID),
        call.setuid(cgroup.APIUSER_ID),
        call.validate(agent_cgroup.CGROUP_PATH_ENV),
        call.validate(CGROUP_PATH_ENV),
    ]


def fake_cgroupfs(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, late_pids: list[str]
) -> list[str]:
    """Emulate cgroupfs: moves leave the root, and late PIDs make enabling EBUSY."""
    (root / "cgroup.procs").write_text("123")
    write_text = Path.write_text
    moved: list[str] = []

    def write(path: Path, data: str) -> int:
        procs = root / "cgroup.procs"
        if path == root / "worker" / "cgroup.procs":
            moved.append(data)
            procs.write_bytes(
                " ".join(p for p in procs.read_text().split() if p != data).encode()
            )
        elif path == root / "cgroup.subtree_control":
            if procs.read_text().split():
                raise OSError(errno.EBUSY, "Device or resource busy")
            if late_pids:
                # A probe exec lands in the root right after it was emptied.
                procs.write_bytes(late_pids.pop(0).encode())
                raise OSError(errno.EBUSY, "Device or resource busy")
        return write_text(path, data)

    monkeypatch.setattr(Path, "write_text", write)
    return moved


def test_enable_memory_retries_when_a_process_joins_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moved = fake_cgroupfs(tmp_path, monkeypatch, late_pids=["456"])
    cgroup.enable_memory_controller(tmp_path)
    assert moved == ["123", "456"]
    assert (tmp_path / "cgroup.subtree_control").read_text() == "+memory"


def test_enable_memory_gives_up_after_bounded_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    late_pids = [str(pid) for pid in range(1000, 1000 + cgroup.ENABLE_MEMORY_ATTEMPTS)]
    moved = fake_cgroupfs(tmp_path, monkeypatch, late_pids=late_pids)
    with pytest.raises(OSError) as exc_info:
        cgroup.enable_memory_controller(tmp_path)
    assert exc_info.value.errno == errno.EBUSY
    assert len(moved) == cgroup.ENABLE_MEMORY_ATTEMPTS
    assert not (tmp_path / "cgroup.subtree_control").exists()
