"""Cgroup v2 preparation and agent executor memory-budget helpers."""

from __future__ import annotations

import errno
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tracecat import config
from tracecat.logger import logger

CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP_MEMORY_MAX_PATH = CGROUP_ROOT / "memory.max"
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


@dataclass(frozen=True, slots=True)
class CgroupMemoryLimit:
    """Parsed cgroup v2 container memory limit."""

    kind: CgroupMemoryLimitKind
    limit_bytes: int | None = None


_cgroup_availability: CgroupAvailability | None = None


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
    cgroup_root: Path = CGROUP_ROOT,
    enabled: bool = config.TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED,
) -> CgroupAvailability:
    """Prepare the cgroup v2 root for nsjail child memory cgroups.

    The result is cached process-wide. Setup failures are reduced to one warning
    and an unavailable result so agent sandbox launches can continue without
    cgroup limits.
    """
    global _cgroup_availability

    if _cgroup_availability is not None:
        return _cgroup_availability
    if not enabled:
        _cgroup_availability = CgroupAvailability.DISABLED
        return _cgroup_availability

    probe_cgroup: Path | None = None
    step = "read cgroup v2 controllers"
    try:
        (cgroup_root / "cgroup.controllers").read_text()

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
        _cgroup_availability = CgroupAvailability.UNAVAILABLE
        return _cgroup_availability

    _cgroup_availability = CgroupAvailability.AVAILABLE
    return _cgroup_availability


def agent_sandbox_cgroup_is_available() -> bool:
    """Return whether the startup probe enabled agent sandbox cgroups."""
    return _cgroup_availability is CgroupAvailability.AVAILABLE


def read_cgroup_memory_limit(
    memory_max_path: Path = CGROUP_MEMORY_MAX_PATH,
) -> CgroupMemoryLimit:
    """Read and parse the cgroup v2 container memory limit."""
    try:
        raw_limit = memory_max_path.read_text().strip()
    except FileNotFoundError:
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNAVAILABLE)
    except OSError as exc:
        logger.warning(
            "Unable to read cgroup memory limit; skipping worker memory-budget "
            "validation",
            path=str(memory_max_path),
            errno=exc.errno,
            error=str(exc),
        )
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNAVAILABLE)

    if raw_limit == "max":
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNLIMITED)

    try:
        limit_bytes = int(raw_limit)
        if limit_bytes < 0:
            raise ValueError("memory limit cannot be negative")
    except ValueError as exc:
        logger.warning(
            "Invalid cgroup memory limit; skipping worker memory-budget validation",
            path=str(memory_max_path),
            value=raw_limit,
            error=str(exc),
        )
        return CgroupMemoryLimit(CgroupMemoryLimitKind.UNAVAILABLE)

    return CgroupMemoryLimit(
        CgroupMemoryLimitKind.LIMITED,
        limit_bytes=limit_bytes,
    )


def clamp_agent_executor_concurrency(
    max_concurrent: int,
    *,
    reserve_mb: int,
    sandbox_memory_mb: int,
    memory_max_path: Path = CGROUP_MEMORY_MAX_PATH,
) -> int:
    """Clamp agent executor concurrency to the container memory budget."""
    memory_limit = read_cgroup_memory_limit(memory_max_path)
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
    if max_concurrent <= allowed:
        return max_concurrent

    clamped = max(allowed, 1)
    logger.error(
        "Agent executor concurrency exceeds the container memory budget; clamping",
        container_limit_mb=limit_mb,
        reserve_mb=reserve_mb,
        sandbox_memory_mb=sandbox_memory_mb,
        configured_max_concurrent_activities=max_concurrent,
        clamped_max_concurrent_activities=clamped,
    )
    return clamped
