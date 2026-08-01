"""Shared fixtures and fakes for registry artifact cache tests."""

from __future__ import annotations

import asyncio
import io
import os
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    RegistryArtifactMaterializationContext,
    SquashfsMountCommandError,
)

MAX_ENTRIES_CONFIG = (
    "tracecat.executor.registry_artifacts.config"
    ".TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_ENTRIES"
)
MAX_BYTES_CONFIG = (
    "tracecat.executor.registry_artifacts.config"
    ".TRACECAT__EXECUTOR_REGISTRY_CACHE_MAX_BYTES"
)
SQUASHFS_ENABLED_CONFIG = (
    "tracecat.executor.registry_artifacts.config"
    ".TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED"
)


def write_tarball_entry(cache_dir: Path, cache_key: str) -> Path:
    """Create a materialized tarball cache entry on disk."""
    target_dir = cache_dir / "entries" / cache_key / "tarball"
    target_dir.mkdir(parents=True)
    (target_dir / "module.py").write_text("VALUE = 1")
    return target_dir


def write_image_entry(
    cache_dir: Path, cache_key: str, *, size: int, mtime: float
) -> Path:
    """Create a downloaded SquashFS image cache entry with a fixed mtime."""
    entry_dir = cache_dir / "entries" / cache_key
    entry_dir.mkdir(parents=True, exist_ok=True)
    image_path = entry_dir / "image.squashfs"
    image_path.write_bytes(b"x" * size)
    os.utime(image_path, (mtime, mtime))
    os.utime(entry_dir, (mtime, mtime))
    return image_path


def tarball_payload(*, size: int) -> bytes:
    """Return a gzip tarball containing one synthetic regular file."""
    payload = b"x" * size
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        member = tarfile.TarInfo("module.py")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return output.getvalue()


@dataclass(slots=True)
class SquashfsMountHarness:
    """Model mount ownership without consuming host loop devices."""

    cache: RegistryArtifactCache
    failed_mount_keys: set[str] = field(default_factory=set)
    mounted: set[Path] = field(default_factory=set)
    mount_attempts: list[str] = field(default_factory=list)
    extraction_attempts: list[str] = field(default_factory=list)
    unmounts: list[Path] = field(default_factory=list)

    def seed_mount(self, cache_key: str) -> Path:
        """Create one reusable image and mark its mount directory active."""
        paths = self.cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True, exist_ok=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir(exist_ok=True)
        (paths.squashfs_mount_dir / "module.py").write_text("VALUE = 1")
        self.mounted.add(paths.squashfs_mount_dir)
        return paths.squashfs_mount_dir

    async def mount(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> Path:
        """Record a mount attempt, failing selected keys like loop exhaustion."""
        self.mount_attempts.append(ctx.cache_key)
        ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"squashfs")
        if ctx.cache_key in self.failed_mount_keys:
            raise SquashfsMountCommandError("no free loop device")
        ctx.paths.squashfs_mount_dir.mkdir(exist_ok=True)
        (ctx.paths.squashfs_mount_dir / "module.py").write_text("VALUE = 1")
        self.mounted.add(ctx.paths.squashfs_mount_dir)
        return ctx.paths.squashfs_mount_dir

    async def extract(
        self,
        ctx: RegistryArtifactMaterializationContext,
        image_path: Path,
    ) -> Path:
        """Publish an extracted fallback from the image retained by mount."""
        assert image_path.is_file()
        self.extraction_attempts.append(ctx.cache_key)
        ctx.paths.squashfs_extract_dir.mkdir(parents=True, exist_ok=True)
        (ctx.paths.squashfs_extract_dir / "module.py").write_text("VALUE = 1")
        return ctx.paths.squashfs_extract_dir

    async def unmount(self, mount_dir: Path) -> bool:
        """Record final-release unmounts and release the modeled loop device."""
        self.unmounts.append(mount_dir)
        self.mounted.discard(mount_dir)
        return True


async def lease_paths(
    cache: RegistryArtifactCache,
    artifact_uri: str,
) -> list[Path]:
    """Return paths from the cache's public lease API."""
    async with cache.lease([artifact_uri]) as paths:
        return paths


class BlockingSubprocess:
    """Fake subprocess that blocks in communicate until it is cancelled."""

    def __init__(self, *, block_wait: bool = False) -> None:
        self.communicate_started = asyncio.Event()
        self.wait_started = asyncio.Event()
        self.release_wait = asyncio.Event()
        self.cleanup_calls: list[str] = []
        self.returncode: int | None = None
        self._block_wait = block_wait

    async def communicate(self) -> tuple[bytes, bytes]:
        """Block until the task awaiting subprocess completion is cancelled."""
        self.communicate_started.set()
        await asyncio.Event().wait()
        return b"", b""

    def kill(self) -> None:
        """Record that the subprocess was killed."""
        self.cleanup_calls.append("kill")
        self.returncode = -9

    async def wait(self) -> int:
        """Record that the killed subprocess was reaped."""
        self.cleanup_calls.append("wait")
        self.wait_started.set()
        if self._block_wait:
            await self.release_wait.wait()
        return -9


class CapturedSubprocess:
    """Capture cleanup of a real subprocess used by cancellation tests."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.killed = False
        self.reaped = False

    @property
    def returncode(self) -> int | None:
        """Return the wrapped subprocess exit status."""
        return self.process.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        """Wait for the wrapped subprocess and collect its output."""
        return await self.process.communicate()

    def kill(self) -> None:
        """Kill the wrapped subprocess and record the signal."""
        self.killed = True
        self.process.kill()

    async def wait(self) -> int:
        """Reap the wrapped subprocess and record completion."""
        returncode = await self.process.wait()
        self.reaped = True
        return returncode
