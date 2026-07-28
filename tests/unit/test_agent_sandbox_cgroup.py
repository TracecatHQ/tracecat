from __future__ import annotations

import errno
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest

import tracecat.agent.sandbox.cgroup as cgroup_module
from tracecat.agent.sandbox.cgroup import (
    BYTES_PER_MEBIBYTE,
    CgroupAvailability,
    CgroupMemoryLimitKind,
    clamp_agent_executor_concurrency,
    prepare_agent_sandbox_cgroup,
    read_cgroup_memory_limit,
)


@pytest.fixture(autouse=True)
def reset_cgroup_availability(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(cgroup_module, "_cgroup_availability", None)
    yield


def _create_fake_cgroup_root(
    tmp_path: Path,
    *,
    pids: str = "101\n202\n",
) -> Path:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text("cpu memory pids\n")
    (cgroup_root / "cgroup.procs").write_text(pids)
    (cgroup_root / "cgroup.subtree_control").write_text("")
    return cgroup_root


def test_prepare_agent_sandbox_cgroup_returns_cached_disabled_state(
    tmp_path: Path,
) -> None:
    cgroup_root = tmp_path / "missing"

    assert (
        prepare_agent_sandbox_cgroup(cgroup_root=cgroup_root, enabled=False)
        is CgroupAvailability.DISABLED
    )
    assert (
        prepare_agent_sandbox_cgroup(cgroup_root=cgroup_root, enabled=True)
        is CgroupAvailability.DISABLED
    )


def test_prepare_agent_sandbox_cgroup_happy_path(tmp_path: Path) -> None:
    cgroup_root = _create_fake_cgroup_root(tmp_path)

    result = prepare_agent_sandbox_cgroup(cgroup_root=cgroup_root, enabled=True)

    assert result is CgroupAvailability.AVAILABLE
    assert (cgroup_root / "main" / "cgroup.procs").read_text() == "202\n"
    assert (cgroup_root / "cgroup.subtree_control").read_text() == "+memory\n"
    assert not list(cgroup_root.glob("tracecat-agent-probe-*"))


def test_prepare_agent_sandbox_cgroup_without_v2_controllers_warns_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = prepare_agent_sandbox_cgroup(cgroup_root=cgroup_root, enabled=True)

    assert result is CgroupAvailability.UNAVAILABLE
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["step"] == "read cgroup v2 controllers"
    assert mock_logger.warning.call_args.kwargs["errno"] == errno.ENOENT


def test_prepare_agent_sandbox_cgroup_subtree_permission_error_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroup_root = _create_fake_cgroup_root(tmp_path)
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)
    write_cgroup_file = cgroup_module._write_cgroup_file

    def deny_subtree_control(path: Path, value: str) -> None:
        if path == cgroup_root / "cgroup.subtree_control":
            raise PermissionError(errno.EACCES, "permission denied")
        write_cgroup_file(path, value)

    monkeypatch.setattr(
        cgroup_module,
        "_write_cgroup_file",
        deny_subtree_control,
    )

    result = prepare_agent_sandbox_cgroup(cgroup_root=cgroup_root, enabled=True)

    assert result is CgroupAvailability.UNAVAILABLE
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["step"] == "enable memory controller"
    assert mock_logger.warning.call_args.kwargs["errno"] == errno.EACCES


def test_prepare_agent_sandbox_cgroup_tolerates_pid_vanishing_during_move(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroup_root = _create_fake_cgroup_root(tmp_path)
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)
    write_cgroup_file = cgroup_module._write_cgroup_file

    def vanish_first_pid(path: Path, value: str) -> None:
        if path == cgroup_root / "main" / "cgroup.procs" and value == "101\n":
            raise ProcessLookupError(errno.ESRCH, "process vanished")
        write_cgroup_file(path, value)

    monkeypatch.setattr(cgroup_module, "_write_cgroup_file", vanish_first_pid)

    result = prepare_agent_sandbox_cgroup(cgroup_root=cgroup_root, enabled=True)

    assert result is CgroupAvailability.AVAILABLE
    assert (cgroup_root / "main" / "cgroup.procs").read_text() == "202\n"
    mock_logger.warning.assert_not_called()


@pytest.mark.parametrize(
    ("contents", "expected_kind", "expected_bytes"),
    [
        pytest.param("max\n", CgroupMemoryLimitKind.UNLIMITED, None, id="max"),
        pytest.param(
            f"{16 * 1024**3}\n",
            CgroupMemoryLimitKind.LIMITED,
            16 * 1024**3,
            id="numeric",
        ),
    ],
)
def test_read_cgroup_memory_limit_parses_valid_values(
    tmp_path: Path,
    contents: str,
    expected_kind: CgroupMemoryLimitKind,
    expected_bytes: int | None,
) -> None:
    memory_max_path = tmp_path / "memory.max"
    memory_max_path.write_text(contents)

    result = read_cgroup_memory_limit(memory_max_path)

    assert result.kind is expected_kind
    assert result.limit_bytes == expected_bytes


def test_read_cgroup_memory_limit_missing_is_unavailable(tmp_path: Path) -> None:
    result = read_cgroup_memory_limit(tmp_path / "memory.max")

    assert result.kind is CgroupMemoryLimitKind.UNAVAILABLE
    assert result.limit_bytes is None


def test_read_cgroup_memory_limit_garbage_warns_and_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    memory_max_path = tmp_path / "memory.max"
    memory_max_path.write_text("not-a-limit\n")
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = read_cgroup_memory_limit(memory_max_path)

    assert result.kind is CgroupMemoryLimitKind.UNAVAILABLE
    assert result.limit_bytes is None
    mock_logger.warning.assert_called_once()


@pytest.mark.parametrize(
    (
        "memory_max_contents",
        "wanted",
        "expected",
        "expected_error",
    ),
    [
        pytest.param(f"{16 * 1024**3}\n", 10, 3, True, id="over-budget"),
        pytest.param("max\n", 10, 10, False, id="unlimited"),
        pytest.param(None, 10, 10, False, id="missing"),
        pytest.param(f"{4 * 1024**3}\n", 10, 1, True, id="allowed-zero"),
        pytest.param(f"{16 * 1024**3}\n", 2, 2, False, id="under-budget"),
    ],
)
def test_clamp_agent_executor_concurrency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    memory_max_contents: str | None,
    wanted: int,
    expected: int,
    expected_error: bool,
) -> None:
    memory_max_path = tmp_path / "memory.max"
    if memory_max_contents is not None:
        memory_max_path.write_text(memory_max_contents)
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = clamp_agent_executor_concurrency(
        wanted,
        reserve_mb=4096,
        sandbox_memory_mb=4096,
        memory_max_path=memory_max_path,
    )

    assert result == expected
    assert mock_logger.error.called is expected_error
    if memory_max_contents == f"{16 * 1024**3}\n" and wanted == 10:
        assert mock_logger.error.call_args.kwargs == {
            "container_limit_mb": 16 * 1024**3 // BYTES_PER_MEBIBYTE,
            "reserve_mb": 4096,
            "sandbox_memory_mb": 4096,
            "configured_max_concurrent_activities": 10,
            "clamped_max_concurrent_activities": 3,
        }
