"""Single writer for registry artifacts mounted read-only by executor containers.

One socket connection owns one lease. Only an explicit release after action
cleanup unpins it. A lost connection retains its lease until task replacement;
a durable marker prevents a restarted manager from forgetting those pins.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pwd
import socket
import sys
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import IO
from uuid import uuid4

from pydantic import BaseModel, Field

from tracecat import config
from tracecat.concurrency import rejoin_future_through_cancellation
from tracecat.executor.registry_artifacts import RegistryArtifactCache
from tracecat.logger import logger

SOCKET_NAME = "manager.sock"
_RELEASE = b"release\n"
_RELEASED = b"released\n"


class LeaseRequest(BaseModel):
    artifact_uris: list[str] = Field(max_length=256)


class LeaseResponse(BaseModel):
    paths: list[str] = Field(default_factory=list)
    error: str | None = None


class RegistryCacheManager:
    """Own materialization and pins for one shared artifact volume."""

    def __init__(self, cache_dir: Path):
        self.cache = RegistryArtifactCache(cache_dir, immutable=True)
        # Mounts made inside the writer container are not visible in readers.
        self.cache._squashfs_mount_policy.record_mount_unavailable()
        self.lease_dir = cache_dir / "leases"
        self.leases: dict[Path, AsyncExitStack] = {}
        self._lock: IO[bytes] | None = None

    async def start(self) -> asyncio.Server:
        """Refuse recovery when a previous worker may still use cached paths."""
        self.lease_dir.mkdir(parents=True, exist_ok=True)
        self._lock = (self.cache.cache_dir / "manager.lock").open("ab")
        fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(self.lease_dir.iterdir()):
            raise RuntimeError(
                "Registry cache has unreleased leases; replace the executor task "
                "and its cache volume before restarting the cache manager"
            )
        await self.cache.ensure_swept()
        socket_path = self.cache.cache_dir / SOCKET_NAME
        socket_path.unlink(missing_ok=True)
        return await asyncio.start_unix_server(self.handle, path=socket_path)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Keep disconnected leases pinned; never infer release from EOF."""
        marker: Path | None = None
        try:
            request = await reader.readline()
            if request == b"health\n":
                writer.write(b"ready\n")
                await writer.drain()
                return
            parsed = LeaseRequest.model_validate_json(request)
            marker = self.lease_dir / uuid4().hex
            marker.touch(exist_ok=False)
            stack = AsyncExitStack()
            self.leases[marker] = stack
            try:
                paths = await stack.enter_async_context(
                    self.cache.lease(parsed.artifact_uris)
                )
            except Exception as error:
                logger.warning(
                    "Registry artifact acquisition failed",
                    error_type=type(error).__name__,
                )
                await self.release(marker)
                marker = None
                writer.write(
                    LeaseResponse(error="Registry artifact acquisition failed")
                    .model_dump_json()
                    .encode()
                    + b"\n"
                )
                await writer.drain()
                return
            writer.write(
                LeaseResponse(paths=[str(path) for path in paths])
                .model_dump_json()
                .encode()
                + b"\n"
            )
            await writer.drain()
            if await reader.readline() == _RELEASE:
                await rejoin_future_through_cancellation(
                    asyncio.create_task(self.release(marker))
                )
                marker = None
                writer.write(_RELEASED)
                await writer.drain()
        except Exception as error:
            logger.warning(
                "Registry cache connection failed", error_type=type(error).__name__
            )
        finally:
            if marker is not None:
                logger.error("Registry cache lease retained after connection loss")
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def release(self, marker: Path) -> None:
        """Clear the recovery marker only after the underlying lease closes."""
        await self.leases[marker].aclose()
        marker.unlink()
        del self.leases[marker]


class RegistryCacheClient:
    """Lease artifacts without giving action processes a writable cache mount."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    async def ensure_swept(self) -> None:
        """Require the writer's startup reconciliation before polling activities."""
        self._require_readonly_mount()
        reader, writer = await asyncio.open_unix_connection(
            self.cache_dir / SOCKET_NAME
        )
        try:
            async with asyncio.timeout(5):
                writer.write(b"health\n")
                await writer.drain()
                if await reader.readline() != b"ready\n":
                    raise RuntimeError("Registry cache manager is not ready")
        finally:
            writer.close()
            await writer.wait_closed()

    def _require_readonly_mount(self) -> None:
        if not os.statvfs(self.cache_dir).f_flag & os.ST_RDONLY:
            raise RuntimeError("Remote registry cache requires a read-only mount")

    @asynccontextmanager
    async def lease(
        self,
        artifact_uris: list[str] | None,
        *,
        paths_may_be_modified: bool = False,
    ) -> AsyncGenerator[list[Path]]:
        # The mount, rather than the caller's backend, enforces immutability.
        del paths_may_be_modified
        if not artifact_uris:
            yield []
            return
        self._require_readonly_mount()
        reader, writer = await asyncio.open_unix_connection(
            self.cache_dir / SOCKET_NAME
        )

        release_required = True

        async def acquire() -> list[Path]:
            nonlocal release_required
            writer.write(
                LeaseRequest(artifact_uris=artifact_uris).model_dump_json().encode()
                + b"\n"
            )
            await writer.drain()
            response = LeaseResponse.model_validate_json(await reader.readline())
            if response.error is not None:
                release_required = False
                raise RuntimeError(response.error)
            return [Path(path) for path in response.paths]

        async def release() -> None:
            try:
                if not release_required:
                    return
                writer.write(_RELEASE)
                await writer.drain()
                async with asyncio.timeout(30):
                    if await reader.readline() != _RELEASED:
                        raise RuntimeError(
                            "Registry cache release was not acknowledged"
                        )
            except (OSError, TimeoutError, RuntimeError):
                # The manager keeps a pin and recovery marker on ambiguous release.
                logger.error(
                    "Registry cache release failed; task replacement may be required"
                )
            finally:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

        try:
            paths = await rejoin_future_through_cancellation(
                asyncio.create_task(
                    asyncio.wait_for(
                        acquire(), timeout=config.TRACECAT__EXECUTOR_CLIENT_TIMEOUT
                    )
                )
            )
            yield paths
        finally:
            await rejoin_future_through_cancellation(asyncio.create_task(release()))


async def serve(cache_dir: Path) -> None:
    manager = RegistryCacheManager(cache_dir)
    async with await manager.start() as server:
        await server.serve_forever()


if __name__ == "__main__":
    cache_dir = Path(config.TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR)
    if "--health" in sys.argv:
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(3)
            probe.connect(str(cache_dir / SOCKET_NAME))
            probe.sendall(b"health\n")
            if probe.recv(32) != b"ready\n":
                raise SystemExit(1)
        raise SystemExit(0)
    # Fargate creates an empty task volume owned by root. Initialize only its
    # root, then run the manager as the same unprivileged UID as the executor.
    cache_dir.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        account = pwd.getpwnam("apiuser")
        os.chown(cache_dir, account.pw_uid, account.pw_gid)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    asyncio.run(serve(cache_dir))
