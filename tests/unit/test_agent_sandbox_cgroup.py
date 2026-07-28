from __future__ import annotations

import errno
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest

import tracecat.agent.sandbox.cgroup as cgroup_module
from tracecat.agent.sandbox.cgroup import (
    BYTES_PER_MEBIBYTE,
    AgentExecutorMemoryBudgetError,
    CgroupAvailability,
    CgroupMemoryLimitKind,
    PreparedCgroup,
    clamp_agent_executor_concurrency,
    detect_cgroup_root,
    get_agent_sandbox_cgroup,
    prepare_agent_sandbox_cgroup,
    read_cgroup_memory_limit,
)


@pytest.fixture(autouse=True)
def reset_prepared_cgroup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(cgroup_module, "_prepared_cgroup", None)
    yield


def _write_proc_cgroup(tmp_path: Path, contents: str) -> Path:
    proc_cgroup_path = tmp_path / "proc-self-cgroup"
    proc_cgroup_path.write_text(contents)
    return proc_cgroup_path


def _create_fake_cgroup_root(
    tmp_path: Path,
    *,
    relative_path: str = "",
    controllers: str = "cpu memory pids\n",
    pids: str = "101\n202\n",
    memory_max: str = f"{16 * 1024**3}\n",
) -> tuple[Path, Path]:
    cgroupfs = tmp_path / "cgroupfs"
    cgroup_root = cgroupfs / relative_path
    cgroup_root.mkdir(parents=True)
    (cgroup_root / "cgroup.controllers").write_text(controllers)
    (cgroup_root / "cgroup.procs").write_text(pids)
    (cgroup_root / "cgroup.subtree_control").write_text("")
    (cgroup_root / "memory.max").write_text(memory_max)
    return cgroupfs, cgroup_root


@pytest.mark.parametrize(
    ("contents", "relative_root"),
    [
        pytest.param("0::/\n", "", id="private-cgroup-namespace"),
        pytest.param(
            "0::/kubepods.slice/kubepods-burstable.slice/"
            "kubepods-burstable-podabc.slice/"
            "cri-containerd-deadbeef.scope\n",
            "kubepods.slice/kubepods-burstable.slice/"
            "kubepods-burstable-podabc.slice/"
            "cri-containerd-deadbeef.scope",
            id="containerd-host-namespace",
        ),
        pytest.param(
            "9:cpu,cpuacct:/kubepods/legacy\n"
            "7:memory:/kubepods/legacy\n"
            "0::/user.slice/tracecat.scope\n",
            "user.slice/tracecat.scope",
            id="hybrid-ignores-v1",
        ),
    ],
)
def test_detect_cgroup_root_from_v2_entry(
    tmp_path: Path,
    contents: str,
    relative_root: str,
) -> None:
    proc_cgroup_path = _write_proc_cgroup(tmp_path, contents)
    cgroupfs = tmp_path / "cgroupfs"

    result = detect_cgroup_root(proc_cgroup_path, cgroupfs)

    assert result == cgroupfs / relative_root


def test_detect_cgroup_root_without_v2_entry_returns_none(tmp_path: Path) -> None:
    proc_cgroup_path = _write_proc_cgroup(
        tmp_path,
        "9:cpu,cpuacct:/kubepods/legacy\n7:memory:/kubepods/legacy\n",
    )

    assert detect_cgroup_root(proc_cgroup_path, tmp_path / "cgroupfs") is None


def test_detect_cgroup_root_missing_file_returns_none(tmp_path: Path) -> None:
    assert detect_cgroup_root(tmp_path / "missing", tmp_path / "cgroupfs") is None


def test_detect_cgroup_root_unreadable_file_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    proc_cgroup_path = _write_proc_cgroup(tmp_path, "0::/\n")

    def deny_read(
        self: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        del self, encoding, errors
        raise PermissionError(errno.EACCES, "permission denied")

    monkeypatch.setattr(Path, "read_text", deny_read)

    assert detect_cgroup_root(proc_cgroup_path, tmp_path / "cgroupfs") is None


def test_detect_cgroup_root_keeps_cgroup_legitimately_named_main(
    tmp_path: Path,
) -> None:
    # A runtime-assigned cgroup that happens to be named "main" is the
    # container's real boundary; escaping to its parent would prepare cgroups
    # beside the container's memory limit instead of beneath it.
    proc_cgroup_path = _write_proc_cgroup(
        tmp_path,
        "0::/kubepods.slice/pod.scope/main\n",
    )
    cgroupfs = tmp_path / "cgroupfs"

    result = detect_cgroup_root(proc_cgroup_path, cgroupfs)

    assert result == cgroupfs / "kubepods.slice/pod.scope/main"


def test_get_agent_sandbox_cgroup_returns_unprepared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cgroup_module.config,
        "TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED",
        True,
    )

    result = get_agent_sandbox_cgroup()

    assert result == PreparedCgroup(CgroupAvailability.UNAVAILABLE, None)
    assert result.sandbox_mount is None


def test_prepare_agent_sandbox_cgroup_returns_cached_disabled_state(
    tmp_path: Path,
) -> None:
    proc_cgroup_path = tmp_path / "missing"
    cgroupfs = tmp_path / "cgroupfs"

    first = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=False,
    )
    second = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=True,
    )

    assert first == PreparedCgroup(CgroupAvailability.DISABLED, None)
    assert second is first
    assert get_agent_sandbox_cgroup() is first


def test_prepare_agent_sandbox_cgroup_beneath_detected_root(
    tmp_path: Path,
) -> None:
    relative_root = "kubepods.slice/pod.scope/container.scope"
    cgroupfs, cgroup_root = _create_fake_cgroup_root(
        tmp_path,
        relative_path=relative_root,
    )
    proc_cgroup_path = _write_proc_cgroup(tmp_path, f"0::/{relative_root}\n")

    result = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=True,
    )

    assert result == PreparedCgroup(
        CgroupAvailability.AVAILABLE,
        cgroup_root,
        cgroupfs,
    )
    assert result.sandbox_mount == cgroup_root
    assert (cgroup_root / "main" / "cgroup.procs").read_text() == "202\n"
    assert (cgroup_root / "cgroup.subtree_control").read_text() == "+memory\n"
    assert not list(cgroup_root.glob("tracecat-agent-probe-*"))


def test_prepare_agent_sandbox_cgroup_detection_failure_warns_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=tmp_path / "missing",
        cgroupfs=tmp_path / "cgroupfs",
        enabled=True,
    )

    assert result == PreparedCgroup(
        CgroupAvailability.UNAVAILABLE,
        None,
        tmp_path / "cgroupfs",
    )
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["step"] == "detect cgroup v2 root"


def test_prepare_agent_sandbox_cgroup_requires_memory_controller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroupfs, cgroup_root = _create_fake_cgroup_root(
        tmp_path,
        controllers="cpu pids\n",
    )
    proc_cgroup_path = _write_proc_cgroup(tmp_path, "0::/\n")
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=True,
    )

    assert result == PreparedCgroup(
        CgroupAvailability.UNAVAILABLE,
        cgroup_root,
        cgroupfs,
    )
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["step"] == "verify memory controller"


def test_prepare_agent_sandbox_cgroup_retains_root_on_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroupfs, cgroup_root = _create_fake_cgroup_root(tmp_path)
    proc_cgroup_path = _write_proc_cgroup(tmp_path, "0::/\n")
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

    result = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=True,
    )

    assert result == PreparedCgroup(
        CgroupAvailability.UNAVAILABLE,
        cgroup_root,
        cgroupfs,
    )
    assert result.sandbox_mount is None
    assert read_cgroup_memory_limit(result.root).kind is CgroupMemoryLimitKind.LIMITED
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.kwargs["step"] == "enable memory controller"
    assert mock_logger.warning.call_args.kwargs["errno"] == errno.EACCES


def test_prepare_agent_sandbox_cgroup_tolerates_pid_vanishing_during_move(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cgroupfs, cgroup_root = _create_fake_cgroup_root(tmp_path)
    proc_cgroup_path = _write_proc_cgroup(tmp_path, "0::/\n")
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)
    write_cgroup_file = cgroup_module._write_cgroup_file

    def vanish_first_pid(path: Path, value: str) -> None:
        if path == cgroup_root / "main" / "cgroup.procs" and value == "101\n":
            raise ProcessLookupError(errno.ESRCH, "process vanished")
        write_cgroup_file(path, value)

    monkeypatch.setattr(cgroup_module, "_write_cgroup_file", vanish_first_pid)

    result = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=True,
    )

    assert result.availability is CgroupAvailability.AVAILABLE
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
def test_read_cgroup_memory_limit_from_detected_root(
    tmp_path: Path,
    contents: str,
    expected_kind: CgroupMemoryLimitKind,
    expected_bytes: int | None,
) -> None:
    cgroupfs, cgroup_root = _create_fake_cgroup_root(
        tmp_path,
        memory_max=contents,
    )
    proc_cgroup_path = _write_proc_cgroup(tmp_path, "0::/\n")
    detected_root = detect_cgroup_root(proc_cgroup_path, cgroupfs)

    result = read_cgroup_memory_limit(detected_root)

    assert detected_root == cgroup_root
    assert result.kind is expected_kind
    assert result.limit_bytes == expected_bytes


def test_read_cgroup_memory_limit_honors_ancestor_limit(tmp_path: Path) -> None:
    # Task-scoped hierarchies (e.g. ECS) can enforce the budget on an ancestor
    # while the container leaf reads "max"; the effective limit is the minimum
    # finite value across the visible hierarchy.
    cgroupfs = tmp_path / "cgroupfs"
    leaf = cgroupfs / "ecs-task" / "container"
    leaf.mkdir(parents=True)
    (cgroupfs / "memory.max").write_text("max\n")
    (cgroupfs / "ecs-task" / "memory.max").write_text(f"{8 * 1024**3}\n")
    (leaf / "memory.max").write_text("max\n")

    result = read_cgroup_memory_limit(leaf, cgroupfs)

    assert result.kind is CgroupMemoryLimitKind.LIMITED
    assert result.limit_bytes == 8 * 1024**3


def test_read_cgroup_memory_limit_takes_minimum_across_levels(
    tmp_path: Path,
) -> None:
    cgroupfs = tmp_path / "cgroupfs"
    leaf = cgroupfs / "task" / "container"
    leaf.mkdir(parents=True)
    (cgroupfs / "task" / "memory.max").write_text(f"{16 * 1024**3}\n")
    (leaf / "memory.max").write_text(f"{4 * 1024**3}\n")

    result = read_cgroup_memory_limit(leaf, cgroupfs)

    assert result.kind is CgroupMemoryLimitKind.LIMITED
    assert result.limit_bytes == 4 * 1024**3


def test_read_cgroup_memory_limit_without_root_is_unavailable() -> None:
    result = read_cgroup_memory_limit(None)

    assert result.kind is CgroupMemoryLimitKind.UNAVAILABLE
    assert result.limit_bytes is None


@pytest.mark.parametrize("contents", ["not-a-limit\n", "-1\n"])
def test_read_cgroup_memory_limit_invalid_value_warns_and_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    contents: str,
) -> None:
    _, cgroup_root = _create_fake_cgroup_root(tmp_path, memory_max=contents)
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = read_cgroup_memory_limit(cgroup_root)

    assert result.kind is CgroupMemoryLimitKind.UNAVAILABLE
    assert result.limit_bytes is None
    mock_logger.warning.assert_called_once()


def test_prepare_move_does_not_change_cached_root_used_for_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    relative_root = "kubepods.slice/pod.scope/container.scope"
    cgroupfs, cgroup_root = _create_fake_cgroup_root(
        tmp_path,
        relative_path=relative_root,
        pids="101\n",
        memory_max=f"{12 * 1024**3}\n",
    )
    proc_cgroup_path = _write_proc_cgroup(tmp_path, f"0::/{relative_root}\n")
    write_cgroup_file = cgroup_module._write_cgroup_file

    def move_worker_into_main(path: Path, value: str) -> None:
        write_cgroup_file(path, value)
        if path == cgroup_root / "main" / "cgroup.procs":
            proc_cgroup_path.write_text(f"0::/{relative_root}/main\n")

    monkeypatch.setattr(
        cgroup_module,
        "_write_cgroup_file",
        move_worker_into_main,
    )

    prepared_cgroup = prepare_agent_sandbox_cgroup(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
        enabled=True,
    )
    (cgroup_root / "main" / "memory.max").write_text("max\n")

    # A fresh detection after the move resolves to the main leaf (whose
    # memory.max is unlimited) — which is exactly why budget validation must
    # consume the root captured by prepare before any PID moves.
    assert detect_cgroup_root(proc_cgroup_path, cgroupfs) == cgroup_root / "main"
    assert prepared_cgroup.root == cgroup_root
    assert (
        clamp_agent_executor_concurrency(
            10,
            prepared_cgroup,
            reserve_mb=4096,
            sandbox_memory_mb=4096,
        )
        == 2
    )


@pytest.mark.parametrize(
    ("container_limit_mb", "reserve_mb"),
    [
        pytest.param(4096, 4096, id="exactly-zero-slots"),
        pytest.param(4096, 8192, id="reserve-exceeds-limit"),
    ],
)
def test_clamp_agent_executor_concurrency_fails_when_no_sandbox_fits(
    tmp_path: Path,
    container_limit_mb: int,
    reserve_mb: int,
) -> None:
    _, cgroup_root = _create_fake_cgroup_root(
        tmp_path,
        memory_max=f"{container_limit_mb * BYTES_PER_MEBIBYTE}\n",
    )
    prepared_cgroup = PreparedCgroup(
        CgroupAvailability.UNAVAILABLE,
        cgroup_root,
    )

    with pytest.raises(AgentExecutorMemoryBudgetError) as exc_info:
        clamp_agent_executor_concurrency(
            10,
            prepared_cgroup,
            reserve_mb=reserve_mb,
            sandbox_memory_mb=4096,
        )

    message = str(exc_info.value)
    assert f"container_limit_mb={container_limit_mb}" in message
    assert f"reserve_mb={reserve_mb}" in message
    assert "sandbox_memory_mb=4096" in message
    assert "TRACECAT__AGENT_EXECUTOR_MEMORY_RESERVE_MB" in message
    assert "TRACECAT__AGENT_SANDBOX_MEMORY_MB" in message
    assert "container memory limit" in message


def test_clamp_agent_executor_concurrency_clamps_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, cgroup_root = _create_fake_cgroup_root(tmp_path)
    prepared_cgroup = PreparedCgroup(CgroupAvailability.AVAILABLE, cgroup_root)
    mock_logger = Mock()
    monkeypatch.setattr(cgroup_module, "logger", mock_logger)

    result = clamp_agent_executor_concurrency(
        10,
        prepared_cgroup,
        reserve_mb=4096,
        sandbox_memory_mb=4096,
    )

    assert result == 3
    mock_logger.error.assert_called_once()
    assert mock_logger.error.call_args.kwargs == {
        "container_limit_mb": 16 * 1024**3 // BYTES_PER_MEBIBYTE,
        "reserve_mb": 4096,
        "sandbox_memory_mb": 4096,
        "configured_max_concurrent_activities": 10,
        "clamped_max_concurrent_activities": 3,
    }


def test_clamp_agent_executor_concurrency_keeps_under_budget_value(
    tmp_path: Path,
) -> None:
    _, cgroup_root = _create_fake_cgroup_root(tmp_path)
    prepared_cgroup = PreparedCgroup(CgroupAvailability.AVAILABLE, cgroup_root)

    assert (
        clamp_agent_executor_concurrency(
            2,
            prepared_cgroup,
            reserve_mb=4096,
            sandbox_memory_mb=4096,
        )
        == 2
    )


def test_clamp_agent_executor_concurrency_passes_through_unlimited(
    tmp_path: Path,
) -> None:
    _, cgroup_root = _create_fake_cgroup_root(tmp_path, memory_max="max\n")
    prepared_cgroup = PreparedCgroup(CgroupAvailability.AVAILABLE, cgroup_root)

    assert (
        clamp_agent_executor_concurrency(
            10,
            prepared_cgroup,
            reserve_mb=4096,
            sandbox_memory_mb=4096,
        )
        == 10
    )


def test_clamp_agent_executor_concurrency_passes_through_unavailable() -> None:
    prepared_cgroup = PreparedCgroup(CgroupAvailability.UNAVAILABLE, None)

    assert (
        clamp_agent_executor_concurrency(
            10,
            prepared_cgroup,
            reserve_mb=4096,
            sandbox_memory_mb=4096,
        )
        == 10
    )
