"""Cgroup v2 preparation and agent executor memory-budget helpers."""

from __future__ import annotations

import errno
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tracecat import config
from tracecat.logger import logger

PROC_SELF_CGROUP = Path("/proc/self/cgroup")
CGROUPFS = Path("/sys/fs/cgroup")
BYTES_PER_MEBIBYTE = 1024 * 1024


class CgroupAvailability(StrEnum):
    """Process-wide availability of agent sandbox cgroup limits."""

    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


class CgroupMemoryLimitKind(StrEnum):
    """Kind of container memory limit reported by cgroup v2."""

    LIMITED = "limited"
    UNLIMITED = "unlimited"
    UNAVAILABLE = "unavailable"


class AgentExecutorMemoryBudgetError(RuntimeError):
    """Raised when the container memory budget cannot fit one sandbox."""


@dataclass(frozen=True, slots=True)
class PreparedCgroup:
    """Process-wide agent sandbox cgroup preparation result."""

    availability: CgroupAvailability
    root: Path | None
    cgroupfs: Path = CGROUPFS

    @property
    def sandbox_mount(self) -> Path | None:
        """Return the nsjail cgroup mount when preparation succeeded."""
        if self.availability is CgroupAvailability.AVAILABLE:
            return self.root
        return None


@dataclass(frozen=True, slots=True)
class CgroupMemoryLimit:
    """Parsed cgroup v2 container memory limit."""

    kind: CgroupMemoryLimitKind
    limit_bytes: int | None = None


_prepared_cgroup: PreparedCgroup | None = None


def detect_cgroup_root(
    proc_cgroup_path: Path = PROC_SELF_CGROUP,
    cgroupfs: Path = CGROUPFS,
) -> Path | None:
    """Detect the executor's cgroup v2 directory from /proc/self/cgroup."""
    try:
        lines = proc_cgroup_path.read_text().splitlines()
    except OSError:
        return None

    for line in lines:
        fields = line.split(":", maxsplit=2)
        if len(fields) != 3:
            continue
        hierarchy_id, controllers, cgroup_path = fields
        if hierarchy_id != "0" or controllers:
            continue

        return cgroupfs if cgroup_path == "/" else cgroupfs / cgroup_path.lstrip("/")
    return None


def _write_cgroup_file(path: Path, value: str) -> None:
    """Write a value to a cgroup control file."""
    path.write_text(value)


def _remove_probe_cgroup(path: Path) -> None:
    """Remove a probe cgroup, including its synthetic file in unit tests."""
    try:
        path.rmdir()
    except OSError as exc:
        if exc.errno != errno.ENOTEMPTY:
            raise
        (path / "memory.max").unlink()
        path.rmdir()


def _warn_cgroup_unavailable(
    *,
    step: str,
    error: OSError | ValueError,
) -> None:
    """Log the single startup warning for a failed cgroup preparation."""
    if isinstance(error, OSError):
        logger.warning(
            "Agent sandbox cgroup memory limits unavailable; "
            "continuing without per-sandbox cgroup limits",
            step=step,
            errno=error.errno,
            error=str(error),
        )
        return
    logger.warning(
        "Agent sandbox cgroup memory limits unavailable; "
        "continuing without per-sandbox cgroup limits",
        step=step,
        error=str(error),
    )


def prepare_agent_sandbox_cgroup(
    *,
    proc_cgroup_path: Path = PROC_SELF_CGROUP,
    cgroupfs: Path = CGROUPFS,
    enabled: bool = config.TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED,
) -> PreparedCgroup:
    """Prepare the cgroup v2 root for nsjail child memory cgroups.

    The result is cached process-wide. Setup failures are reduced to one warning
    and an unavailable result so agent sandbox launches can continue without
    cgroup limits.
    """
    global _prepared_cgroup

    if _prepared_cgroup is not None:
        return _prepared_cgroup
    if not enabled:
        _prepared_cgroup = PreparedCgroup(CgroupAvailability.DISABLED, None)
        return _prepared_cgroup

    cgroup_root = detect_cgroup_root(
        proc_cgroup_path=proc_cgroup_path,
        cgroupfs=cgroupfs,
    )
    if cgroup_root is None:
        _warn_cgroup_unavailable(
            step="detect cgroup v2 root",
            error=ValueError(f"no cgroup v2 entry found in {proc_cgroup_path}"),
        )
        _prepared_cgroup = PreparedCgroup(
            CgroupAvailability.UNAVAILABLE,
            None,
            cgroupfs,
        )
        return _prepared_cgroup

    probe_cgroup: Path | None = None
    step = "read cgroup v2 controllers"
    try:
        controllers = (cgroup_root / "cgroup.controllers").read_text().split()
        step = "verify memory controller"
        if "memory" not in controllers:
            raise ValueError("memory controller is not available")

        step = "create main cgroup"
        main_cgroup = cgroup_root / "main"
        main_cgroup.mkdir(exist_ok=True)

        step = "read root cgroup processes"
        root_pids = (cgroup_root / "cgroup.procs").read_text().splitlines()
        for raw_pid in root_pids:
            if not raw_pid:
                continue
            pid = int(raw_pid)
            step = f"move PID {pid} to main cgroup"
            try:
                _write_cgroup_file(main_cgroup / "cgroup.procs", f"{pid}\n")
            except OSError as exc:
                if exc.errno in {errno.ENOENT, errno.ESRCH}:
                    continue
                raise

        step = "enable memory controller"
        _write_cgroup_file(cgroup_root / "cgroup.subtree_control", "+memory\n")

        step = "create verification cgroup"
        probe_cgroup = cgroup_root / f"tracecat-agent-probe-{uuid.uuid4().hex}"
        probe_cgroup.mkdir()

        step = "write verification memory.max"
        _write_cgroup_file(probe_cgroup / "memory.max", "max\n")

        step = "remove verification cgroup"
        _remove_probe_cgroup(probe_cgroup)
        probe_cgroup = None
    except (OSError, ValueError) as exc:
        if probe_cgroup is not None:
            try:
                _remove_probe_cgroup(probe_cgroup)
            except OSError:
                pass
        _warn_cgroup_unavailable(step=step, error=exc)
        _prepared_cgroup = PreparedCgroup(
            CgroupAvailability.UNAVAILABLE,
            cgroup_root,
            cgroupfs,
        )
        return _prepared_cgroup

    _prepared_cgroup = PreparedCgroup(
        CgroupAvailability.AVAILABLE,
        cgroup_root,
        cgroupfs,
    )
    return _prepared_cgroup


def get_agent_sandbox_cgroup() -> PreparedCgroup:
    """Return the cached process-wide cgroup preparation result."""
    if _prepared_cgroup is not None:
        return _prepared_cgroup
    if config.TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED:
        return PreparedCgroup(CgroupAvailability.UNAVAILABLE, None)
    return PreparedCgroup(CgroupAvailability.DISABLED, None)


def read_cgroup_memory_limit(
    cgroup_root: Path | None,
    cgroupfs: Path = CGROUPFS,
) -> CgroupMemoryLimit:
    """Read the effective cgroup v2 memory limit for the executor's cgroup."""
    if cgroup_root is None:
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNAVAILABLE)

    # The effective limit can live on an ancestor while the leaf reads "max"
    # (e.g. an ECS task-level limit with no container-level limit), so take
    # the minimum finite memory.max across every hierarchy level visible
    # between the leaf and the cgroupfs mount.
    directories = [cgroup_root]
    if cgroup_root != cgroupfs and cgroupfs in cgroup_root.parents:
        for parent in cgroup_root.parents:
            directories.append(parent)
            if parent == cgroupfs:
                break

    finite_limits: list[int] = []
    unreadable = False
    unlimited_seen = False
    for directory in directories:
        memory_max_path = directory / "memory.max"
        try:
            raw_limit = memory_max_path.read_text().strip()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning(
                "Unable to read cgroup memory limit; skipping worker "
                "memory-budget validation for this level",
                path=str(memory_max_path),
                errno=exc.errno,
                error=str(exc),
            )
            unreadable = True
            continue

        if raw_limit == "max":
            unlimited_seen = True
            continue

        try:
            limit_bytes = int(raw_limit)
            if limit_bytes < 0:
                raise ValueError("memory limit cannot be negative")
        except ValueError as exc:
            logger.warning(
                "Invalid cgroup memory limit; skipping worker memory-budget "
                "validation for this level",
                path=str(memory_max_path),
                value=raw_limit,
                error=str(exc),
            )
            unreadable = True
            continue

        finite_limits.append(limit_bytes)

    if finite_limits:
        return CgroupMemoryLimit(
            CgroupMemoryLimitKind.LIMITED,
            limit_bytes=min(finite_limits),
        )
    if unreadable:
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNAVAILABLE)
    if unlimited_seen:
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNLIMITED)
    return CgroupMemoryLimit(CgroupMemoryLimitKind.UNAVAILABLE)


def clamp_agent_executor_concurrency(
    max_concurrent: int,
    prepared_cgroup: PreparedCgroup,
    *,
    reserve_mb: int,
    sandbox_memory_mb: int,
) -> int:
    """Clamp agent executor concurrency to the container memory budget."""
    memory_limit = read_cgroup_memory_limit(
        prepared_cgroup.root,
        prepared_cgroup.cgroupfs,
    )
    if memory_limit.kind is CgroupMemoryLimitKind.UNLIMITED:
        logger.debug(
            "Container memory is unlimited; skipping worker memory-budget validation"
        )
        return max_concurrent
    if memory_limit.kind is CgroupMemoryLimitKind.UNAVAILABLE:
        return max_concurrent

    limit_bytes = memory_limit.limit_bytes
    if limit_bytes is None:
        return max_concurrent

    limit_mb = limit_bytes // BYTES_PER_MEBIBYTE
    allowed = (limit_mb - reserve_mb) // sandbox_memory_mb
    if allowed < 1:
        raise AgentExecutorMemoryBudgetError(
            "Agent executor memory budget cannot fit one sandbox: "
            f"container_limit_mb={limit_mb}, reserve_mb={reserve_mb}, "
            f"sandbox_memory_mb={sandbox_memory_mb}. Increase the container "
            "memory limit or reduce "
            "TRACECAT__AGENT_EXECUTOR_MEMORY_RESERVE_MB or "
            "TRACECAT__AGENT_SANDBOX_MEMORY_MB."
        )
    if max_concurrent <= allowed:
        return max_concurrent

    logger.error(
        "Agent executor concurrency exceeds the container memory budget; clamping",
        container_limit_mb=limit_mb,
        reserve_mb=reserve_mb,
        sandbox_memory_mb=sandbox_memory_mb,
        configured_max_concurrent_activities=max_concurrent,
        clamped_max_concurrent_activities=allowed,
    )
    return allowed
