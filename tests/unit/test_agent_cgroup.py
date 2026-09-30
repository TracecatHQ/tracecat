"""The worker must reject missing delegation before accepting activities."""

from pathlib import Path
from unittest.mock import Mock, call

import pytest

from tracecat.agent import executor_worker as agent_worker
from tracecat.agent.sandbox import cgroup as agent_cgroup
from tracecat.executor import worker as executor_worker
from tracecat.sandbox import cgroup
from tracecat.sandbox.cgroup import CGROUP_PATH_ENV, sandbox_cgroup


def test_missing_delegation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        sandbox_cgroup()


@pytest.mark.anyio
async def test_worker_rejects_missing_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(executor_worker.config, "TRACECAT__DISABLE_NSJAIL", False)
    monkeypatch.delenv(CGROUP_PATH_ENV, raising=False)
    monkeypatch.delenv(agent_cgroup.CGROUP_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        await executor_worker.main()
    with pytest.raises(RuntimeError, match="requires cgroup v2 delegation"):
        await agent_worker.main()


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
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "false")
    monkeypatch.setenv(CGROUP_PATH_ENV, "")
    for name in ("HOME", "USER", "LOGNAME"):
        monkeypatch.setenv(name, "original")
    return process


@pytest.mark.parametrize("configured", [None, "", "explicit"])
def test_bootstrap_selects_and_validates_subtree_as_apiuser(
    configured: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bootstrap_process: Mock,
) -> None:
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
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")
    delegate = Mock()
    monkeypatch.setattr(cgroup, "delegate_cgroup", delegate)

    cgroup.main()

    delegate.assert_not_called()
    bootstrap_process.validate.assert_not_called()
    bootstrap_process.execvp.assert_called_once_with(
        "python", ["python", "-m", "worker"]
    )


def test_agent_bootstrap_preserves_its_delegation_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bootstrap_process: Mock
) -> None:
    monkeypatch.setenv(agent_cgroup.CGROUP_PATH_ENV, "")
    (tmp_path / "cgroup.subtree_control").write_text("memory")
    (tmp_path / "cgroup.procs").touch()
    monkeypatch.setattr(cgroup, "delegate_cgroup", Mock(return_value=tmp_path))
    agent_cgroup.main()
    assert agent_cgroup.sandbox_cgroup() == tmp_path
    assert sandbox_cgroup() == tmp_path
    bootstrap_process.execvp.assert_called_once_with(
        "python", ["python", "-m", "worker"]
    )
