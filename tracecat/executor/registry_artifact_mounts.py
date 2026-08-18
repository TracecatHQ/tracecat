"""SquashFS mount lifecycle and loop-device recovery."""

from __future__ import annotations

import asyncio
import os
import random
import stat
from dataclasses import dataclass
from pathlib import Path

from tracecat.logger import logger
from tracecat.sandbox.utils import communicate_process_group

SQUASHFS_MOUNT_OPTIONS = "loop,ro,nodev,nosuid"
"""Mount options for executor-managed SquashFS registry artifacts.

The image must stay read-only and should not expose device nodes or setuid bits
from registry package contents. Avoid ``noexec`` because Python packages may
include native extension modules loaded from the mounted artifact.
"""

LOOP_DEVICE_SYNC_HELPER = "/usr/local/bin/tracecat-loop-device-sync"
"""Fixed-purpose helper that mirrors kernel-confirmed loop nodes into ``/dev``."""

SQUASHFS_MOUNT_MAX_ATTEMPTS = 3
SQUASHFS_MOUNT_RETRY_MIN_SECONDS = 0.05
SQUASHFS_MOUNT_RETRY_MAX_SECONDS = 0.2


class SquashfsMountCommandError(RuntimeError):
    """The ``mount`` command itself failed for a SquashFS registry artifact.

    Only this error drives SquashFS mount policy. Download, directory creation,
    and other preparation failures must not be mistaken for a missing mount
    capability or loop-device exhaustion.
    """


class LoopDeviceSyncCommandError(RuntimeError):
    """The restricted loop-device synchronization helper failed."""


@dataclass(frozen=True, slots=True)
class LoopDeviceSyncResult:
    """Counts reported after synchronizing kernel loop devices into ``/dev``."""

    kernel_devices: int
    created_nodes: int
    existing_nodes: int


def is_mount(path: Path) -> bool:
    """Return whether a path is mounted without hiding inspection failures."""
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return False

    if stat.S_ISLNK(path_stat.st_mode):
        return False

    parent = Path(os.path.realpath(path / "..", strict=True))
    parent_stat = parent.lstat()
    return (
        path_stat.st_dev != parent_stat.st_dev or path_stat.st_ino == parent_stat.st_ino
    )


async def _sync_loop_device_nodes() -> LoopDeviceSyncResult:
    """Create missing ``/dev/loopN`` nodes already present in kernel sysfs."""
    try:
        proc = await asyncio.create_subprocess_exec(
            LOOP_DEVICE_SYNC_HELPER,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise LoopDeviceSyncCommandError(
            "loop-device synchronization helper could not start"
        ) from error

    stdout, stderr = await communicate_process_group(proc)
    if proc.returncode != 0:
        output = (stderr or stdout).decode(errors="replace").strip()
        raise LoopDeviceSyncCommandError(
            output or "loop-device synchronization helper failed"
        )

    fields = stdout.decode(errors="replace").split()
    if len(fields) != 3:
        raise LoopDeviceSyncCommandError(
            "loop-device synchronization helper returned an invalid result"
        )
    try:
        kernel_devices, created_nodes, existing_nodes = map(int, fields)
    except ValueError as error:
        raise LoopDeviceSyncCommandError(
            "loop-device synchronization helper returned an invalid result"
        ) from error
    if (
        min(kernel_devices, created_nodes, existing_nodes) < 0
        or created_nodes + existing_nodes != kernel_devices
    ):
        raise LoopDeviceSyncCommandError(
            "loop-device synchronization helper returned inconsistent counts"
        )

    return LoopDeviceSyncResult(
        kernel_devices=kernel_devices,
        created_nodes=created_nodes,
        existing_nodes=existing_nodes,
    )


async def mount_squashfs(image_path: Path, target_dir: Path) -> None:
    """Mount a SquashFS image, repairing stale container loop nodes on failure.

    The first attempt preserves the normal fast path. A failed attempt invokes
    the fixed-purpose helper and retries with short jitter, covering both stale
    ``/dev`` snapshots and concurrent node-level loop allocation.

    Raises:
        SquashfsMountCommandError: The mount command failed after recovery.
    """
    for attempt in range(1, SQUASHFS_MOUNT_MAX_ATTEMPTS + 1):
        if is_mount(target_dir):
            return

        proc = await asyncio.create_subprocess_exec(
            "mount",
            "-t",
            "squashfs",
            "-o",
            SQUASHFS_MOUNT_OPTIONS,
            str(image_path),
            str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = await communicate_process_group(proc)

        if proc.returncode == 0 or is_mount(target_dir):
            return

        output = (stderr or stdout).decode(errors="replace").strip()
        mount_error = SquashfsMountCommandError(output or "mount command failed")
        if attempt == SQUASHFS_MOUNT_MAX_ATTEMPTS:
            raise mount_error

        try:
            sync_result = await _sync_loop_device_nodes()
        except LoopDeviceSyncCommandError as sync_error:
            logger.warning(
                "Failed to synchronize loop device nodes after mount failure",
                mount_attempt=attempt,
                mount_attempts=SQUASHFS_MOUNT_MAX_ATTEMPTS,
                mount_error=str(mount_error),
                error=str(sync_error),
            )
            raise mount_error from sync_error

        retry_delay = random.uniform(
            SQUASHFS_MOUNT_RETRY_MIN_SECONDS,
            SQUASHFS_MOUNT_RETRY_MAX_SECONDS,
        )
        logger.warning(
            "Retrying SquashFS mount after synchronizing loop device nodes",
            mount_attempt=attempt,
            mount_attempts=SQUASHFS_MOUNT_MAX_ATTEMPTS,
            mount_error=str(mount_error),
            kernel_loop_devices=sync_result.kernel_devices,
            created_loop_nodes=sync_result.created_nodes,
            existing_loop_nodes=sync_result.existing_nodes,
            retry_delay_ms=f"{retry_delay * 1000:.1f}",
        )
        await asyncio.sleep(retry_delay)

    raise AssertionError("SquashFS mount retry loop exhausted unexpectedly")
