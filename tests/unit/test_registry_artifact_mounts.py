"""Tests for SquashFS mount recovery and helper protocol handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.executor import registry_artifact_mounts


def _process(*, returncode: int) -> AsyncMock:
    process = AsyncMock()
    process.returncode = returncode
    return process


@pytest.mark.anyio
async def test_sync_loop_device_nodes_parses_valid_counts() -> None:
    """The helper's three-field output is converted to a typed result."""
    process = _process(returncode=0)

    with (
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ) as create_subprocess_exec,
        patch(
            "tracecat.executor.registry_artifact_mounts.communicate_process_group",
            new_callable=AsyncMock,
            return_value=(b"7 2 5\n", b""),
        ),
    ):
        result = await registry_artifact_mounts._sync_loop_device_nodes()

    assert result == registry_artifact_mounts.LoopDeviceSyncResult(
        kernel_devices=7,
        created_nodes=2,
        existing_nodes=5,
    )
    create_subprocess_exec.assert_awaited_once_with(
        registry_artifact_mounts.LOOP_DEVICE_SYNC_HELPER,
        stdout=registry_artifact_mounts.asyncio.subprocess.PIPE,
        stderr=registry_artifact_mounts.asyncio.subprocess.PIPE,
        start_new_session=True,
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "message"),
    [
        (1, b"", b"permission denied", "permission denied"),
        (0, b"1 1\n", b"", "invalid result"),
        (0, b"one two three\n", b"", "invalid result"),
        (0, b"2 2 1\n", b"", "inconsistent counts"),
        (0, b"-1 0 -1\n", b"", "inconsistent counts"),
    ],
)
@pytest.mark.anyio
async def test_sync_loop_device_nodes_rejects_helper_failures(
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    message: str,
) -> None:
    """Nonzero, malformed, and inconsistent helper results fail closed."""
    process = _process(returncode=returncode)

    with (
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts.communicate_process_group",
            new_callable=AsyncMock,
            return_value=(stdout, stderr),
        ),
        pytest.raises(
            registry_artifact_mounts.LoopDeviceSyncCommandError,
            match=message,
        ),
    ):
        await registry_artifact_mounts._sync_loop_device_nodes()


@pytest.mark.anyio
async def test_sync_loop_device_nodes_wraps_startup_failure() -> None:
    """An unavailable capability helper becomes a structured sync failure."""
    with (
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=PermissionError("not permitted"),
        ),
        pytest.raises(
            registry_artifact_mounts.LoopDeviceSyncCommandError,
            match="could not start",
        ) as raised,
    ):
        await registry_artifact_mounts._sync_loop_device_nodes()

    assert isinstance(raised.value.__cause__, PermissionError)


@pytest.mark.anyio
async def test_mount_squashfs_recovers_after_synchronizing_nodes(
    tmp_path: Path,
) -> None:
    """A failed mount synchronizes nodes, waits briefly, and then succeeds."""
    image_path = tmp_path / "image.squashfs"
    target_dir = tmp_path / "mount"
    processes = [_process(returncode=1), _process(returncode=0)]
    sync_result = registry_artifact_mounts.LoopDeviceSyncResult(3, 1, 2)

    with (
        patch(
            "tracecat.executor.registry_artifact_mounts.is_mount",
            return_value=False,
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=processes,
        ) as create_subprocess_exec,
        patch(
            "tracecat.executor.registry_artifact_mounts.communicate_process_group",
            new_callable=AsyncMock,
            side_effect=[(b"", b"failed"), (b"", b"")],
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts._sync_loop_device_nodes",
            new_callable=AsyncMock,
            return_value=sync_result,
        ) as sync_nodes,
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
    ):
        await registry_artifact_mounts.mount_squashfs(image_path, target_dir)

    assert create_subprocess_exec.await_count == 2
    sync_nodes.assert_awaited_once_with()
    sleep.assert_awaited_once()


@pytest.mark.anyio
async def test_mount_squashfs_preserves_mount_error_when_sync_fails(
    tmp_path: Path,
) -> None:
    """A helper failure keeps the original mount diagnostic and stops retrying."""
    process = _process(returncode=1)
    sync_error = registry_artifact_mounts.LoopDeviceSyncCommandError("sync failed")

    with (
        patch(
            "tracecat.executor.registry_artifact_mounts.is_mount",
            return_value=False,
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ) as create_subprocess_exec,
        patch(
            "tracecat.executor.registry_artifact_mounts.communicate_process_group",
            new_callable=AsyncMock,
            return_value=(b"", b"mount failed"),
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts._sync_loop_device_nodes",
            new_callable=AsyncMock,
            side_effect=sync_error,
        ),
        pytest.raises(
            registry_artifact_mounts.SquashfsMountCommandError,
            match="mount failed",
        ) as raised,
    ):
        await registry_artifact_mounts.mount_squashfs(
            tmp_path / "image.squashfs",
            tmp_path / "mount",
        )

    create_subprocess_exec.assert_awaited_once()
    assert raised.value.__cause__ is sync_error


@pytest.mark.anyio
async def test_mount_squashfs_stops_after_bounded_retries(tmp_path: Path) -> None:
    """Persistent mount failure performs exactly the configured attempt count."""
    processes = [
        _process(returncode=1)
        for _ in range(registry_artifact_mounts.SQUASHFS_MOUNT_MAX_ATTEMPTS)
    ]
    sync_result = registry_artifact_mounts.LoopDeviceSyncResult(1, 0, 1)

    with (
        patch(
            "tracecat.executor.registry_artifact_mounts.is_mount",
            return_value=False,
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=processes,
        ) as create_subprocess_exec,
        patch(
            "tracecat.executor.registry_artifact_mounts.communicate_process_group",
            new_callable=AsyncMock,
            return_value=(b"", b"still failing"),
        ),
        patch(
            "tracecat.executor.registry_artifact_mounts._sync_loop_device_nodes",
            new_callable=AsyncMock,
            return_value=sync_result,
        ) as sync_nodes,
        patch(
            "tracecat.executor.registry_artifact_mounts.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
        pytest.raises(
            registry_artifact_mounts.SquashfsMountCommandError,
            match="still failing",
        ),
    ):
        await registry_artifact_mounts.mount_squashfs(
            tmp_path / "image.squashfs",
            tmp_path / "mount",
        )

    assert (
        create_subprocess_exec.await_count
        == registry_artifact_mounts.SQUASHFS_MOUNT_MAX_ATTEMPTS
    )
    assert (
        sync_nodes.await_count
        == registry_artifact_mounts.SQUASHFS_MOUNT_MAX_ATTEMPTS - 1
    )
    assert sleep.await_count == registry_artifact_mounts.SQUASHFS_MOUNT_MAX_ATTEMPTS - 1
