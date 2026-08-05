"""Tests for executor registry artifact materialization."""

from __future__ import annotations

import asyncio
import io
import os
import signal
import tarfile
import tempfile
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import ANY, AsyncMock, call, patch

import httpx
import pytest
import tracecat_registry

from tracecat.executor.registry_artifact_storage import (
    RegistryArtifactMaterializationContext,
    _allocated_stat_size,
    _delete_cache_path,
    _directory_footprint,
    _filesystem_allocation_unit,
    allocated_size_bound,
)
from tracecat.executor.registry_artifacts import (
    SQUASHFS_MOUNT_OPTIONS,
    RegistryArtifactCache,
    RegistryArtifactCacheCapacityError,
    RegistryArtifactCacheLoopError,
    RegistryArtifactEviction,
    RegistryArtifactExtractionError,
    RegistryArtifactFormat,
    RegistryArtifactUriError,
    SquashfsArtifact,
    SquashfsMountCommandError,
    TarballArtifact,
    _squashfs_listing_size,
    bundled_builtin_registry_uri,
    compute_registry_artifact_cache_key,
)
from tracecat.registry.artifact_keys import parse_s3_uri

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
MOUNT_CHECK = "tracecat.executor.registry_artifact_mounts.is_mount"


def _write_tarball_entry(cache_dir: Path, cache_key: str) -> Path:
    """Create a materialized tarball cache entry on disk."""
    target_dir = cache_dir / "entries" / cache_key / "tarball"
    target_dir.mkdir(parents=True)
    (target_dir / "module.py").write_text("VALUE = 1")
    return target_dir


def _write_image_entry(
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


def _tarball_payload(*, size: int) -> bytes:
    """Return a gzip tarball containing one synthetic regular file."""
    payload = b"x" * size
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        member = tarfile.TarInfo("module.py")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return output.getvalue()


@dataclass(slots=True)
class _SquashfsMountHarness:
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


async def _materialize(
    cache: RegistryArtifactCache,
    cache_key: str,
    artifact_uri: str,
) -> list[Path]:
    """Materialize through the same public lifecycle used by executors."""
    assert cache_key == compute_registry_artifact_cache_key(artifact_uri)
    async with cache.lease([artifact_uri]) as paths:
        return paths


async def _lease_and_release(
    cache: RegistryArtifactCache,
    artifact_uri: str,
) -> None:
    """Exercise one artifact through the public lease lifecycle."""
    async with cache.lease([artifact_uri]):
        pass


class _BlockingSubprocess:
    """Fake subprocess that blocks in communicate until it is cancelled."""

    def __init__(self, *, block_wait: bool = False) -> None:
        self.pid = 999_999_999
        self.communicate_started = asyncio.Event()
        self.wait_started = asyncio.Event()
        self.release_wait = asyncio.Event()
        self.cleanup_calls: list[str] = []
        self.returncode: int | None = None
        self._block_wait = block_wait

    async def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
    ) -> tuple[bytes, bytes]:
        """Block until the task awaiting subprocess completion is cancelled."""
        del input
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


class _CapturedSubprocess:
    """Capture cleanup of a real subprocess used by cancellation tests."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.killed = False
        self.reaped = False

    @property
    def returncode(self) -> int | None:
        """Return the wrapped subprocess exit status."""
        return self.process.returncode

    @property
    def pid(self) -> int:
        """Return the wrapped subprocess process-group identifier."""
        return self.process.pid

    async def communicate(
        self,
        input: bytes | None = None,  # noqa: A002
    ) -> tuple[bytes, bytes]:
        """Wait for the wrapped subprocess and collect its output."""
        return await self.process.communicate(input=input)

    def kill(self) -> None:
        """Kill the wrapped subprocess and record the signal."""
        self.killed = True
        self.process.kill()

    async def wait(self) -> int:
        """Reap the wrapped subprocess and record completion."""
        returncode = await self.process.wait()
        self.reaped = True
        return returncode


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestParseS3Uri:
    """Tests for parse_s3_uri function."""

    def test_valid_uri(self):
        """Test parsing a valid S3 URI."""
        bucket, key = parse_s3_uri("s3://my-bucket/path/to/file.tar.gz")
        assert bucket == "my-bucket"
        assert key == "path/to/file.tar.gz"

    def test_uri_with_nested_path(self):
        """Test parsing URI with deeply nested path."""
        bucket, key = parse_s3_uri("s3://bucket/a/b/c/d/e/file.tar.gz")
        assert bucket == "bucket"
        assert key == "a/b/c/d/e/file.tar.gz"

    def test_invalid_uri_no_prefix(self):
        """Test that non-S3 URIs raise ValueError."""
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            parse_s3_uri("https://bucket/key")

    def test_invalid_uri_no_key(self):
        """Test that URIs without keys raise ValueError."""
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            parse_s3_uri("s3://bucket")

    def test_invalid_uri_empty_bucket(self):
        """Test that URIs with empty bucket raise ValueError."""
        with pytest.raises(ValueError, match="Invalid S3 URI"):
            parse_s3_uri("s3:///key")


class TestRegistryArtifactCache:
    """Tests for registry artifact cache behavior."""

    def test_compute_registry_artifact_cache_key_deterministic(self):
        """Test that cache key computation is deterministic."""
        uri = "s3://bucket/path/to/registry-v1.2.3.tar.gz"

        key1 = compute_registry_artifact_cache_key(uri)
        key2 = compute_registry_artifact_cache_key(uri)

        assert key1 == key2
        assert len(key1) == 16

    def test_compute_registry_artifact_cache_key_case_sensitive(self):
        """Test that cache key is case-sensitive because S3 keys are case-sensitive."""
        key1 = compute_registry_artifact_cache_key("s3://BUCKET/PATH/FILE.tar.gz")
        key2 = compute_registry_artifact_cache_key("s3://bucket/path/file.tar.gz")

        assert key1 != key2

    def test_compute_registry_artifact_cache_key_whitespace_sensitive(self):
        """Cache identity preserves whitespace that can belong to an S3 key."""
        key = compute_registry_artifact_cache_key("s3://bucket/path/file.tar.gz")
        whitespace_key = compute_registry_artifact_cache_key(
            "s3://bucket/path/file.tar.gz "
        )

        assert key != whitespace_key

    def test_compute_registry_artifact_cache_key_empty(self):
        """Test that empty URI returns the base cache key."""
        assert compute_registry_artifact_cache_key("") == "base"

    @pytest.mark.anyio
    async def test_download_artifact_uses_blob_download_file_to_path(
        self, temp_cache_dir
    ):
        """Test that artifact downloads stay behind the blob storage helper."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key="download-test",
        )
        ctx = cache._context_for(artifact.cache_key)
        output_path = temp_cache_dir / "artifact.squashfs"

        async def mock_download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            max_bytes: int | None = None,
            ensure_capacity: Callable[[int], Awaitable[None]] | None = None,
            defer_cleanup: Callable[[Path], None] | None = None,
            redact_log_identifiers: bool = False,
        ) -> None:
            assert max_bytes is None
            assert ensure_capacity is None
            assert defer_cleanup is not None
            assert redact_log_identifiers is True
            output_path.write_bytes(b"squashfs")

        with patch(
            "tracecat.executor.registry_artifacts.blob.download_file_to_path",
            new_callable=AsyncMock,
            side_effect=mock_download_file_to_path,
        ) as download_file_to_path:
            await artifact.download(ctx, output_path)

        download_file_to_path.assert_awaited_once()
        await_args = download_file_to_path.await_args
        assert await_args is not None
        assert await_args.kwargs["key"] == "path/site-packages.squashfs"
        assert await_args.kwargs["bucket"] == "bucket"
        assert output_path.read_bytes() == b"squashfs"

    @pytest.mark.anyio
    async def test_lease_uses_bundled_current_builtin(
        self, temp_cache_dir, monkeypatch: pytest.MonkeyPatch
    ):
        """In-tree builtin registry returns only the installed site-packages."""
        version = "1.2.3"
        site_packages = temp_cache_dir / "venv" / "site-packages"
        package_dir = site_packages / "tracecat_registry"
        package_dir.mkdir(parents=True)
        package_file = package_dir / "__init__.py"
        package_file.write_text("")

        monkeypatch.setattr(tracecat_registry, "__version__", version)
        monkeypatch.setattr(tracecat_registry, "__file__", str(package_file))
        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.sysconfig.get_path",
            lambda name: str(site_packages) if name == "purelib" else None,
        )

        cache = RegistryArtifactCache(temp_cache_dir)
        async with cache.lease([bundled_builtin_registry_uri(version)]) as result:
            assert result == [site_packages.resolve()]

    @pytest.mark.anyio
    async def test_lease_exposes_editable_builtin_parent(
        self, temp_cache_dir, monkeypatch: pytest.MonkeyPatch
    ):
        """Editable builtin registry exposes the package wrapper + site-packages."""
        version = "1.2.3"
        site_packages = temp_cache_dir / "venv" / "site-packages"
        dependency_dir = site_packages / "orjson"
        dependency_dir.mkdir(parents=True)
        (dependency_dir / "__init__.py").write_text("VALUE = 1\n")
        source_root = temp_cache_dir / "src" / "tracecat-registry"
        package_dir = source_root / "tracecat_registry"
        package_dir.mkdir(parents=True)
        package_file = package_dir / "__init__.py"
        package_file.write_text("__version__ = '1.2.3'\n")

        monkeypatch.setattr(tracecat_registry, "__version__", version)
        monkeypatch.setattr(tracecat_registry, "__file__", str(package_file))
        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.sysconfig.get_path",
            lambda name: str(site_packages) if name == "purelib" else None,
        )

        cache = RegistryArtifactCache(temp_cache_dir)
        async with cache.lease([bundled_builtin_registry_uri(version)]) as result:
            assert result == [source_root.resolve(), site_packages.resolve()]

    @pytest.mark.anyio
    async def test_lease_rejects_stale_bundled_builtin(
        self, temp_cache_dir, monkeypatch: pytest.MonkeyPatch
    ):
        """Bundled pseudo-URIs must match this executor's installed package."""
        monkeypatch.setattr(tracecat_registry, "__version__", "1.2.3")

        cache = RegistryArtifactCache(temp_cache_dir)
        with pytest.raises(RuntimeError, match="does not match installed version"):
            async with cache.lease([bundled_builtin_registry_uri("1.2.4")]):
                pass

    @pytest.mark.anyio
    async def test_download_artifact_normalizes_missing_objects_to_http_404(
        self, temp_cache_dir
    ):
        """Preserve the missing-artifact error contract from presigned downloads."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact = TarballArtifact(
            uri="s3://bucket/path/site-packages.tar.gz",
            cache_key="missing-test",
        )
        ctx = cache._context_for(artifact.cache_key)
        output_path = temp_cache_dir / "artifact.tar.gz"

        with patch(
            "tracecat.executor.registry_artifacts.blob.download_file_to_path",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await artifact.download(ctx, output_path)

        assert exc_info.value.response.status_code == 404
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    @pytest.mark.anyio
    async def test_invalid_artifact_uri_suppresses_identifiers(
        self, temp_cache_dir: Path
    ) -> None:
        sensitive_uri = "https://affected.example/tenant/secret-artifact.tar.gz"
        artifact = TarballArtifact(
            uri=sensitive_uri,
            cache_key="invalid-uri",
        )
        cache = RegistryArtifactCache(temp_cache_dir)

        with pytest.raises(RegistryArtifactUriError) as raised:
            await artifact.download(
                cache._context_for(artifact.cache_key),
                temp_cache_dir / "artifact.tar.gz",
            )

        assert sensitive_uri not in str(raised.value)
        assert str(raised.value) == "Invalid registry artifact URI"

    @pytest.mark.anyio
    async def test_artifact_candidates_prefer_squashfs_sidecar(self, temp_cache_dir):
        """Test that gzip tarballs prefer a sibling SquashFS sidecar."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ) as file_exists,
            patch.object(cache, "_can_try_squashfs", return_value=True),
        ):
            cache_key = compute_registry_artifact_cache_key(
                "s3://bucket/path/site-packages.tar.gz"
            )
            ctx = cache._context_for(cache_key)
            candidates = await cache._artifact_candidates(
                ctx, "s3://bucket/path/site-packages.tar.gz"
            )

        artifact = candidates[0]
        assert len(candidates) == 2
        assert isinstance(artifact, SquashfsArtifact)
        assert isinstance(candidates[1], TarballArtifact)
        assert artifact.uri == "s3://bucket/path/site-packages.squashfs"
        assert artifact.format == RegistryArtifactFormat.SQUASHFS
        file_exists.assert_awaited_once_with(
            key="path/site-packages.squashfs",
            bucket="bucket",
        )

    @pytest.mark.anyio
    async def test_same_key_cold_fan_in_materializes_and_enforces_once(
        self, temp_cache_dir
    ):
        """Same-key waiters share candidate lookup, materialization, and enforcement."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        materialization_started = asyncio.Event()
        finish_materialization = asyncio.Event()

        async def mock_materialize(
            self: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            materialization_started.set()
            await finish_materialization.wait()
            ctx.paths.tarball_target_dir.mkdir(parents=True)
            return [ctx.paths.tarball_target_dir]

        async def take_lease() -> list[Path]:
            async with cache.lease([artifact_uri]) as paths:
                return paths

        with (
            patch.object(
                cache,
                "_sidecar_exists",
                new_callable=AsyncMock,
                return_value=False,
            ) as sidecar_exists,
            patch.object(
                cache,
                "_enforce_cache_budget",
                new_callable=AsyncMock,
                return_value=True,
            ) as enforce_cache_budget,
            patch.object(cache, "_converge_cache_budget", new_callable=AsyncMock),
            patch.object(TarballArtifact, "materialize", mock_materialize),
        ):
            leases = [asyncio.create_task(take_lease()) for _ in range(5)]
            await materialization_started.wait()
            await asyncio.sleep(0)
            finish_materialization.set()
            results = await asyncio.gather(*leases)

        expected_paths = [cache._paths_for(cache_key).tarball_target_dir]
        assert results == [expected_paths] * 5
        sidecar_exists.assert_awaited_once()
        enforce_cache_budget.assert_awaited_once_with(protected_key=cache_key)

    @pytest.mark.anyio
    async def test_different_cold_keys_serialize_materialization(
        self, temp_cache_dir: Path
    ) -> None:
        """Distinct cold keys cannot multiply staging and extraction peaks."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        uris = ["s3://bucket/first.tar.gz", "s3://bucket/second.tar.gz"]
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        started_keys: list[str] = []

        async def controlled_materialize(
            self: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            del self
            started_keys.append(ctx.cache_key)
            if len(started_keys) == 1:
                first_started.set()
                await release_first.wait()
            else:
                second_started.set()
            ctx.paths.tarball_target_dir.mkdir(parents=True)
            return [ctx.paths.tarball_target_dir]

        async def take_lease(uri: str) -> None:
            async with cache.lease([uri]):
                pass

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "materialize", controlled_materialize),
            patch.object(cache, "_enforce_cache_budget", new_callable=AsyncMock),
            patch.object(cache, "_converge_cache_budget", new_callable=AsyncMock),
        ):
            first = asyncio.create_task(take_lease(uris[0]))
            await first_started.wait()
            second = asyncio.create_task(take_lease(uris[1]))
            await asyncio.sleep(0)
            assert not second_started.is_set()

            release_first.set()
            await second_started.wait()
            await asyncio.gather(first, second)

        assert started_keys == [
            compute_registry_artifact_cache_key(uri) for uri in uris
        ]

    def test_squashfs_listing_size_sums_files_and_symlinks(self) -> None:
        listing = b"\n".join(
            [
                b"drwxr-xr-x 0/0                      64 2026-01-01 00:00 squashfs-root",
                b"-rw-r--r-- 0/0                     123 2026-01-01 00:00 squashfs-root/module.py",
                b"lrwxrwxrwx 0/0                       9 2026-01-01 00:00 squashfs-root/current -> module.py",
            ]
        )

        # Includes every inode plus directory-entry overhead, not just payload
        # bytes (123-byte file + 9-byte symlink).
        assert _squashfs_listing_size(listing) == 276

    def test_squashfs_listing_size_rejects_unparseable_files(self) -> None:
        with pytest.raises(ValueError, match="Could not parse SquashFS listing"):
            _squashfs_listing_size(b"-rw-r--r-- malformed")

    def test_directory_footprint_does_not_follow_symlinked_root(
        self, temp_cache_dir: Path
    ) -> None:
        outside = temp_cache_dir / "outside"
        outside.mkdir()
        (outside / "large.bin").write_bytes(b"x" * (1024 * 1024))
        symlinked_root = temp_cache_dir / "cache-link"
        symlinked_root.symlink_to(outside, target_is_directory=True)
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)

        assert _directory_footprint(symlinked_root) == _allocated_stat_size(
            symlinked_root.lstat(),
            allocation_unit=allocation_unit,
        )

    def test_budget_scan_rejects_symlinked_cache_root(
        self, temp_cache_dir: Path
    ) -> None:
        outside = temp_cache_dir / "outside"
        outside.mkdir()
        symlinked_root = temp_cache_dir / "cache-link"
        symlinked_root.symlink_to(outside, target_is_directory=True)
        cache = RegistryArtifactCache(symlinked_root)

        with pytest.raises(OSError, match="Unsafe registry artifact cache directory"):
            cache._scan_cache_snapshot()

    @pytest.mark.anyio
    async def test_materialization_rejects_symlinked_cache_root(
        self, temp_cache_dir: Path
    ) -> None:
        outside = temp_cache_dir / "outside"
        outside.mkdir()
        symlinked_root = temp_cache_dir / "cache-link"
        symlinked_root.symlink_to(outside, target_is_directory=True)
        cache = RegistryArtifactCache(symlinked_root)
        artifact = TarballArtifact(
            uri="s3://bucket/path/site-packages.tar.gz",
            cache_key="symlinked-root",
        )

        with pytest.raises(OSError, match="Unsafe registry artifact cache directory"):
            await artifact.materialize(cache._context_for(artifact.cache_key))

        assert list(outside.iterdir()) == []

    @pytest.mark.anyio
    async def test_artifact_candidates_direct_squashfs_include_gzip_fallback(
        self, temp_cache_dir
    ):
        """Test direct SquashFS URIs fall back to sibling gzip tarballs."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(cache, "_can_try_squashfs") as can_try_squashfs:
            cache_key = compute_registry_artifact_cache_key(
                "s3://bucket/path/site-packages.squashfs"
            )
            ctx = cache._context_for(cache_key)
            candidates = await cache._artifact_candidates(
                ctx,
                "s3://bucket/path/site-packages.squashfs",
            )

        assert isinstance(candidates[0], SquashfsArtifact)
        assert isinstance(candidates[1], TarballArtifact)
        assert [artifact.uri for artifact in candidates] == [
            "s3://bucket/path/site-packages.squashfs",
            "s3://bucket/path/site-packages.tar.gz",
        ]
        assert [artifact.format for artifact in candidates] == [
            RegistryArtifactFormat.SQUASHFS,
            RegistryArtifactFormat.TAR_GZ,
        ]
        can_try_squashfs.assert_not_called()

    @pytest.mark.anyio
    async def test_artifact_candidates_fall_back_to_gzip(self, temp_cache_dir):
        """Test that gzip tarballs are used when no sidecar exists."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(cache, "_can_try_squashfs", return_value=True),
        ):
            cache_key = compute_registry_artifact_cache_key(
                "s3://bucket/path/site-packages.tar.gz"
            )
            ctx = cache._context_for(cache_key)
            candidates = await cache._artifact_candidates(
                ctx, "s3://bucket/path/site-packages.tar.gz"
            )

        artifact = candidates[0]
        assert len(candidates) == 1
        assert isinstance(artifact, TarballArtifact)
        assert artifact.uri == "s3://bucket/path/site-packages.tar.gz"
        assert artifact.format == RegistryArtifactFormat.TAR_GZ

    @pytest.mark.anyio
    async def test_sidecar_lookup_failure_logs_only_redacted_uris(
        self, temp_cache_dir: Path
    ) -> None:
        cache = RegistryArtifactCache(temp_cache_dir)
        base_uri = "s3://affected-bucket/tenant/private/site-packages.tar.gz"
        sidecar_uri = base_uri.removesuffix(".tar.gz") + ".squashfs"

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                side_effect=RuntimeError(f"failed for {sidecar_uri}"),
            ),
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            assert (
                await cache._sidecar_exists(
                    base_uri=base_uri,
                    sidecar_uri=sidecar_uri,
                    artifact_format=RegistryArtifactFormat.SQUASHFS,
                )
                is False
            )

        warning.assert_called_once_with(
            "Failed to check for registry artifact sidecar, falling back",
            artifact_uri="s3://<redacted>",
            sidecar_uri="s3://<redacted>",
            artifact_format="squashfs",
            error_type="RuntimeError",
        )

    def test_can_try_squashfs_does_not_require_mount_binary(self, temp_cache_dir):
        """Prefer SquashFS whenever enabled; extraction may work without mounts."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value=None,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
        ):
            ctx = cache._context_for("squashfs-test")
            assert cache._can_try_squashfs() is True
            assert ctx.can_mount_squashfs() is False

    @pytest.mark.anyio
    async def test_artifact_candidates_skip_non_registry_tarballs(self, temp_cache_dir):
        """Test that arbitrary gzip tarballs do not trigger sidecar lookups."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch(
            "tracecat.executor.registry_artifacts.blob.file_exists",
            new_callable=AsyncMock,
        ) as file_exists:
            cache_key = compute_registry_artifact_cache_key(
                "s3://bucket/path/custom.tar.gz"
            )
            ctx = cache._context_for(cache_key)
            candidates = await cache._artifact_candidates(
                ctx, "s3://bucket/path/custom.tar.gz"
            )

        artifact = candidates[0]
        assert len(candidates) == 1
        assert isinstance(artifact, TarballArtifact)
        assert artifact.uri == "s3://bucket/path/custom.tar.gz"
        assert artifact.format == RegistryArtifactFormat.TAR_GZ
        file_exists.assert_not_awaited()

    @pytest.mark.anyio
    async def test_materialize_mounts_squashfs_sidecar(self, temp_cache_dir):
        """Test that a SquashFS sidecar is mounted instead of extracting tarballs."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_mount(self, ctx, image_path):
            assert image_path.name.endswith(".squashfs")
            target_dir = ctx.paths.squashfs_mount_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
            ) as tarball_materialize,
        ):
            result = await _materialize(
                cache,
                compute_registry_artifact_cache_key(
                    "s3://bucket/path/site-packages.tar.gz"
                ),
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        tarball_materialize.assert_not_awaited()

    @pytest.mark.anyio
    async def test_mount_squashfs_uses_hardened_read_only_options(
        self,
        temp_cache_dir,
    ):
        """Test that SquashFS images are mounted read-only without device/setuid bits."""
        cache_key = "cache-key"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_mount_dir
        ctx.paths.entry_dir.mkdir(parents=True)
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        process = AsyncMock()
        process.pid = 1234
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        with (
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as create_subprocess_exec,
            patch(
                "tracecat.sandbox.utils.terminate_process_group",
                new_callable=AsyncMock,
            ) as terminate_process_group,
        ):
            await artifact.mount(ctx, image_path)

        create_subprocess_exec.assert_awaited_once_with(
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
        terminate_process_group.assert_awaited_once_with(process)

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_mount_reaps_subprocess(self, temp_cache_dir):
        """Repeated cancellation cannot abandon a killed mount process."""
        cache_key = "cancelled-mount"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_mount_dir
        ctx.paths.entry_dir.mkdir(parents=True)
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        process = _BlockingSubprocess(block_wait=True)

        with (
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as create_subprocess_exec,
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            mounting = asyncio.create_task(
                artifact._mount_image(image_path, target_dir)
            )
            await process.communicate_started.wait()
            mounting.cancel()
            await process.wait_started.wait()

            mounting.cancel()
            done, _ = await asyncio.wait({mounting}, timeout=0.05)
            assert not done
            process.release_wait.set()
            with pytest.raises(asyncio.CancelledError):
                await mounting

        assert process.cleanup_calls == ["kill", "wait"]
        await_args = create_subprocess_exec.await_args
        assert await_args is not None
        assert await_args.kwargs["start_new_session"] is True
        kill_group.assert_called_once_with(process.pid, signal.SIGKILL)
        assert target_dir.is_dir()
        assert not target_dir.is_mount()

    @pytest.mark.anyio
    async def test_cancelled_squashfs_extract_kills_and_reaps_subprocess(
        self, temp_cache_dir
    ):
        """Cancellation cannot leave unsquashfs writing into scratch."""
        cache_key = "cancelled-extract"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_extract_dir
        ctx.paths.entry_dir.mkdir(parents=True)
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        real_create_subprocess_exec = asyncio.create_subprocess_exec
        process_started = asyncio.Event()
        captured_processes: list[_CapturedSubprocess] = []

        async def create_sleep_subprocess(
            *args: object, **kwargs: object
        ) -> _CapturedSubprocess:
            del args
            assert kwargs["start_new_session"] is True
            process = await real_create_subprocess_exec(
                "/bin/sleep",
                "30",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            captured = _CapturedSubprocess(process)
            captured_processes.append(captured)
            process_started.set()
            return captured

        with (
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/usr/bin/unsquashfs",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=create_sleep_subprocess,
            ),
        ):
            extracting = asyncio.create_task(
                artifact._extract_image(image_path, target_dir)
            )
            await process_started.wait()
            captured = captured_processes[0]
            extracting.cancel()

            try:
                with pytest.raises(asyncio.CancelledError):
                    await extracting
            finally:
                if captured.returncode is None:
                    captured.kill()
                    await captured.wait()

        assert captured.killed is True
        assert captured.reaped is True
        assert captured.returncode is not None

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_tarball_extract_rejoins_thread(
        self, temp_cache_dir
    ):
        """Repeated cancellation waits until the tar extractor stops writing."""
        artifact = TarballArtifact(
            uri="s3://bucket/path/site-packages.tar.gz",
            cache_key="cancelled-tarball-extract",
        )
        tarball_path = temp_cache_dir / "artifact.tar.gz"
        target_dir = temp_cache_dir / "target"
        target_dir.mkdir()
        with tarfile.open(tarball_path, "w:gz"):
            pass

        extraction_started = threading.Event()
        extraction_release = threading.Event()
        extraction_finished = threading.Event()

        def blocking_extractall(*args: object, **kwargs: object) -> None:
            del args, kwargs
            extraction_started.set()
            extraction_release.wait()
            extraction_finished.set()

        with patch.object(
            tarfile.TarFile,
            "extractall",
            new=blocking_extractall,
        ):
            extracting = asyncio.create_task(artifact.extract(tarball_path, target_dir))
            assert await asyncio.to_thread(extraction_started.wait, 1)
            extracting.cancel()
            done, _ = await asyncio.wait({extracting}, timeout=0.05)
            first_cancellation_propagated_early = bool(done)

            extracting.cancel()
            done, _ = await asyncio.wait({extracting}, timeout=0.05)
            second_cancellation_propagated_early = bool(done)
            extraction_release.set()

            with pytest.raises(asyncio.CancelledError):
                await extracting

        assert extraction_finished.is_set()
        assert first_cancellation_propagated_early is False
        assert second_cancellation_propagated_early is False

    @pytest.mark.parametrize("operation", ["extract", "size-command", "size-parse"])
    @pytest.mark.anyio
    async def test_squashfs_failures_sanitize_subprocess_output(
        self,
        temp_cache_dir: Path,
        operation: str,
    ) -> None:
        sensitive_output = b"synthetic-customer/repository/member.py"
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/custom.squashfs",
            cache_key="malformed-squashfs",
        )
        image_path = temp_cache_dir / "image.squashfs"
        image_path.write_bytes(b"squashfs")
        target_dir = temp_cache_dir / "target"
        target_dir.mkdir()
        process = AsyncMock()
        process.returncode = 0 if operation == "size-parse" else 1
        stdout = sensitive_output if operation == "size-parse" else b""
        stderr = sensitive_output if operation != "size-parse" else b""

        with (
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/usr/bin/unsquashfs",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as create_subprocess_exec,
            patch(
                "tracecat.executor.registry_artifacts.communicate_process_group",
                new_callable=AsyncMock,
                return_value=(stdout, stderr),
            ),
            pytest.raises(RegistryArtifactExtractionError) as raised,
        ):
            if operation == "extract":
                await artifact._extract_image(image_path, target_dir)
            else:
                await artifact._squashfs_extracted_size(
                    image_path,
                    allocation_unit=1,
                )

        assert str(raised.value) == "Registry artifact extraction failed"
        await_args = create_subprocess_exec.await_args
        assert await_args is not None
        assert await_args.kwargs["start_new_session"] is True
        assert sensitive_output.decode() not in str(raised.value)
        assert raised.value.__cause__ is None

    @pytest.mark.anyio
    async def test_tarball_extract_sanitizes_member_failures(
        self,
        temp_cache_dir: Path,
    ) -> None:
        sensitive_member = "../../synthetic-customer/repository/module.py"
        artifact = TarballArtifact(
            uri="s3://bucket/path/site-packages.tar.gz",
            cache_key="malformed-tarball",
        )
        tarball_path = temp_cache_dir / "artifact.tar.gz"
        target_dir = temp_cache_dir / "target"
        target_dir.mkdir()
        with tarfile.open(tarball_path, "w:gz") as tar:
            member = tarfile.TarInfo(sensitive_member)
            member.size = 1
            tar.addfile(member, io.BytesIO(b"x"))

        with pytest.raises(RegistryArtifactExtractionError) as raised:
            await artifact.extract(tarball_path, target_dir)

        assert str(raised.value) == "Registry artifact extraction failed"
        assert sensitive_member not in str(raised.value)
        assert raised.value.__cause__ is None

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_tarball_size_scan_rejoins_thread(
        self, temp_cache_dir: Path
    ) -> None:
        """Cancellation cannot unlink a tarball under an active size scan."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/slow-size-scan.tar.gz"
        scan_started = threading.Event()
        scan_release = threading.Event()
        input_present_at_finish: list[bool] = []
        downloaded_paths: list[Path] = []

        async def mock_download(self, ctx, path):
            del self, ctx
            path.write_bytes(_tarball_payload(size=1))
            downloaded_paths.append(path)

        def blocking_size_scan(path: Path, *, allocation_unit: int) -> int:
            assert allocation_unit > 0
            scan_started.set()
            scan_release.wait()
            input_present_at_finish.append(path.exists())
            return 1

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", mock_download),
            patch(
                "tracecat.executor.registry_artifacts._tarball_extracted_size",
                side_effect=blocking_size_scan,
            ),
            patch.object(TarballArtifact, "extract", new_callable=AsyncMock) as extract,
        ):
            materializing = asyncio.create_task(_lease_and_release(cache, artifact_uri))
            assert await asyncio.to_thread(scan_started.wait, 1)
            materializing.cancel()
            await asyncio.sleep(0)
            materializing.cancel()
            await asyncio.sleep(0)
            assert not materializing.done()
            assert downloaded_paths[0].exists()
            scan_release.set()

            with pytest.raises(asyncio.CancelledError):
                await materializing

        assert input_present_at_finish == [True]
        assert not downloaded_paths[0].exists()
        extract.assert_not_awaited()

    @pytest.mark.anyio
    async def test_failed_runtime_staging_cleanup_is_retried(
        self, temp_cache_dir: Path
    ) -> None:
        """A failed extraction cleanup remains visible to later budget passes."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact = TarballArtifact(
            uri="s3://bucket/path/failed-cleanup.tar.gz",
            cache_key="failed-cleanup",
        )
        ctx = cache._context_for(artifact.cache_key)

        async def mock_download(self, ctx, path):
            del self, ctx
            path.write_bytes(b"tarball")

        async def fail_extract(self, tarball_path, target_dir):
            del self, tarball_path, target_dir
            raise RuntimeError("extraction failed")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", fail_extract),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path_off_loop",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            with pytest.raises(RuntimeError, match="extraction failed"):
                await artifact.materialize(ctx)

        assert len(cache._failed_startup_cleanup) == 1
        deferred_path = next(iter(cache._failed_startup_cleanup))
        assert deferred_path.is_dir()

        assert await cache._enforce_cache_budget() is True
        assert cache._failed_startup_cleanup == {}
        assert not deferred_path.exists()

    @pytest.mark.anyio
    async def test_deferred_cleanup_does_not_delete_replacement(
        self, temp_cache_dir: Path
    ) -> None:
        """A stale cleanup record cannot delete a newly published artifact."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/replacement.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = cache._paths_for(cache_key).tarball_target_dir
        target_dir.parent.mkdir(parents=True)
        target_dir.write_text("malformed")
        cache._context_for(cache_key).defer_cleanup(target_dir)
        target_dir.unlink()

        async def mock_download(self, ctx, path):
            del self, ctx
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, output_dir):
            del self, tarball_path
            (output_dir / "module.py").write_text("VALUE = 1")

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [target_dir]
                assert (target_dir / "module.py").is_file()

        assert target_dir.is_dir()
        assert cache._failed_startup_cleanup == {}

    @pytest.mark.anyio
    async def test_materialize_extracts_squashfs_when_mount_fails(self, temp_cache_dir):
        """Test that SquashFS mount failures fall back to unsquashfs extraction."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_mount(self, ctx, image_path):
            raise SquashfsMountCommandError("operation not permitted")

        async def mock_extract(self, ctx, image_path):
            target_dir = ctx.paths.squashfs_extract_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
            ) as tarball_materialize,
        ):
            result = await _materialize(
                cache,
                compute_registry_artifact_cache_key(
                    "s3://bucket/path/site-packages.tar.gz"
                ),
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name == "extracted"
        tarball_materialize.assert_not_awaited()

    @pytest.mark.anyio
    async def test_materialize_extracts_squashfs_without_mount_binary(
        self, temp_cache_dir
    ):
        """Test that SquashFS is still preferred when only unsquashfs is available."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_extract(self, ctx, image_path):
            target_dir = ctx.paths.squashfs_extract_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which", return_value=None
            ),
            patch.object(SquashfsArtifact, "extract", mock_extract),
        ):
            result = await _materialize(
                cache,
                compute_registry_artifact_cache_key(
                    "s3://bucket/path/site-packages.tar.gz"
                ),
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name == "extracted"

    @pytest.mark.anyio
    async def test_materialize_falls_back_to_gzip_when_squashfs_extract_fails(
        self, temp_cache_dir
    ):
        """Test that legacy gzip remains the final compatibility fallback."""
        cache = RegistryArtifactCache(temp_cache_dir)
        source = temp_cache_dir / "source"
        source.mkdir()
        (source / "module.py").write_text("VALUE = 1")

        async def mock_tarball_download(self, ctx, path):
            with tarfile.open(path, "w:gz") as tar:
                tar.add(source / "module.py", arcname="module.py")

        async def mock_mount(self, ctx, image_path):
            raise SquashfsMountCommandError("operation not permitted")

        async def mock_extract(self, ctx, image_path):
            raise RuntimeError("unsquashfs unavailable")

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                side_effect=[True, False],
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
            patch.object(TarballArtifact, "download", mock_tarball_download),
        ):
            result = await _materialize(
                cache,
                compute_registry_artifact_cache_key(
                    "s3://bucket/path/site-packages.tar.gz"
                ),
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name == "tarball"

    @pytest.mark.anyio
    async def test_materialize_treats_unknown_suffix_as_gzip(self, temp_cache_dir):
        """Test that existing gzip artifacts can use arbitrary S3 key suffixes."""
        cache = RegistryArtifactCache(temp_cache_dir)
        source = temp_cache_dir / "source"
        source.mkdir()
        (source / "module.py").write_text("VALUE = 1")

        async def mock_download(self, ctx, path):
            assert path.name.endswith(".tar.gz")
            with tarfile.open(path, "w:gz") as tar:
                tar.add(source / "module.py", arcname="module.py")

        with patch.object(TarballArtifact, "download", mock_download):
            result = await _materialize(
                cache,
                compute_registry_artifact_cache_key("s3://bucket/path/custom-key"),
                "s3://bucket/path/custom-key",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"

    @pytest.mark.anyio
    async def test_materialize_caches_result(self, temp_cache_dir):
        """Test that tarball extraction is cached."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/test.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = cache._paths_for(cache_key).tarball_target_dir
        target_dir.mkdir(parents=True)

        result = await _materialize(
            cache,
            cache_key,
            artifact_uri,
        )

        assert result == [target_dir]

    @pytest.mark.anyio
    async def test_materialize_concurrent_requests(self, temp_cache_dir):
        """Test that concurrent requests for same artifact do not race."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/test.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        download_count = 0

        async def mock_download(self, ctx, path):
            nonlocal download_count
            download_count += 1
            await asyncio.sleep(0.1)
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "extracted.txt").write_text("extracted")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            results = await asyncio.gather(
                _materialize(cache, cache_key, artifact_uri),
                _materialize(cache, cache_key, artifact_uri),
                _materialize(cache, cache_key, artifact_uri),
            )

        assert all(r == results[0] for r in results)
        assert download_count == 1

    def test_runtime_for_same_key(self, temp_cache_dir):
        """The same cache key returns the same runtime state."""
        cache = RegistryArtifactCache(temp_cache_dir)

        runtime1 = cache._runtime_for("key1")
        runtime2 = cache._runtime_for("key1")

        assert runtime1 is runtime2

    def test_runtime_for_different_keys(self, temp_cache_dir):
        """Different cache keys return different runtime state."""
        cache = RegistryArtifactCache(temp_cache_dir)

        runtime1 = cache._runtime_for("key1")
        runtime2 = cache._runtime_for("key2")

        assert runtime1 is not runtime2


class TestRegistryArtifactCacheLease:
    """Tests for lease-based pinning of registry artifact cache entries."""

    @pytest.mark.anyio
    async def test_cache_rejects_use_from_a_second_event_loop(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect the process-wide cache from thread-local Temporal loops.

        The cache owns asyncio locks, tasks, and refcounts as one unit. A typed
        ownership failure prevents a future synchronous activity from silently
        sharing only part of that state across thread-local event loops, which
        previously caused intermittent cross-loop failures in storage caches.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()

        def use_cache_from_another_thread() -> None:
            asyncio.run(cache.ensure_swept())

        with pytest.raises(RegistryArtifactCacheLoopError):
            await asyncio.to_thread(use_cache_from_another_thread)

        # A rejected caller must not poison the owning loop's cache.
        async with cache.lease(None) as registry_paths:
            assert registry_paths == []

    def test_touch_entry_refreshes_tarball_root_mtime(self, temp_cache_dir):
        """Touching a tarball-only entry persists its restart-safe recency."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "tarball-only"
        _write_tarball_entry(temp_cache_dir, cache_key)
        entry_dir = cache._paths_for(cache_key).entry_dir
        os.utime(entry_dir, (100.0, 100.0))

        cache._touch_entry(cache_key)

        assert entry_dir.stat().st_mtime > 100.0

    @pytest.mark.anyio
    async def test_lease_refcounts_and_touches_image_mtime(self, temp_cache_dir):
        """A lease pins its entry and refreshes the restart-safe LRU timestamp."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/leased.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = _write_tarball_entry(temp_cache_dir, cache_key)
        image_path = _write_image_entry(temp_cache_dir, cache_key, size=16, mtime=100.0)
        entry_dir = cache._paths_for(cache_key).entry_dir

        async with cache.lease([artifact_uri]) as registry_paths:
            assert registry_paths == [target_dir]
            assert cache._refcount(cache_key) == 1
            assert entry_dir.stat().st_mtime > 100.0

        assert cache._refcount(cache_key) == 0
        assert image_path.is_file()

    @pytest.mark.anyio
    async def test_lease_releases_refcount_when_materialization_fails(
        self, temp_cache_dir
    ):
        """A failed materialization must not leak a pin or empty cache entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/broken.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            raise RuntimeError("download failed")

        with patch.object(TarballArtifact, "download", mock_download):
            with pytest.raises(RuntimeError, match="download failed"):
                async with cache.lease([artifact_uri]):
                    pass

        assert cache._refcount(cache_key) == 0
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert cache_key not in cache._discover_cache_keys()

    @pytest.mark.anyio
    async def test_failed_first_admission_converges_deposited_image(
        self, temp_cache_dir: Path
    ) -> None:
        """A failed first artifact must not strand an over-budget image."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/failed-first.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)
        empty_cache_bytes = cache._scan_cache_snapshot().total_bytes

        async def fail_after_deposit(
            artifact: SquashfsArtifact,
            ctx: RegistryArtifactMaterializationContext,
        ) -> list[Path]:
            del artifact
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
            ctx.paths.squashfs_image_path.write_bytes(b"reusable image")
            raise RuntimeError("mount failed")

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, empty_cache_bytes),
            patch.object(
                cache,
                "_artifact_candidates",
                new_callable=AsyncMock,
                return_value=[artifact],
            ),
            patch.object(SquashfsArtifact, "materialize", fail_after_deposit),
        ):
            with pytest.raises(RuntimeError, match="mount failed"):
                async with cache.lease([artifact_uri]):
                    pass

        assert cache._refcount(cache_key) == 0
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert cache._discover_cache_keys() == set()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_cancelled_lease_admission_releases_refcount(self, temp_cache_dir):
        """Cancellation during candidate lookup must not leak a permanent pin."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        lookup_started = asyncio.Event()
        finish_lookup = asyncio.Event()

        async def blocked_sidecar_lookup(**kwargs):
            lookup_started.set()
            await finish_lookup.wait()
            return False

        async def take_lease() -> None:
            async with cache.lease([artifact_uri]):
                pass

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch.object(cache, "_sidecar_exists", blocked_sidecar_lookup),
        ):
            acquisition = asyncio.create_task(take_lease())
            await lookup_started.wait()
            assert cache._refcount(cache_key) == 1
            acquisition.cancel()
            with pytest.raises(asyncio.CancelledError):
                await acquisition

        assert cache._refcount(cache_key) == 0
        assert not cache._paths_for(cache_key).entry_dir.exists()

    @pytest.mark.anyio
    async def test_repeated_cancellation_finishes_acquisition_rollback(
        self, temp_cache_dir: Path
    ) -> None:
        """A second cancellation cannot abandon final rollback cleanup."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        lookup_started = asyncio.Event()
        rollback_started = asyncio.Event()
        finish_rollback = asyncio.Event()

        async def blocked_sidecar_lookup(**kwargs):
            del kwargs
            lookup_started.set()
            await asyncio.Event().wait()

        async def blocked_unmount(requested_key: str) -> None:
            assert requested_key == cache_key
            rollback_started.set()
            await finish_rollback.wait()

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch.object(cache, "_sidecar_exists", blocked_sidecar_lookup),
            patch.object(cache, "_unmount_idle_entry", blocked_unmount),
        ):
            acquisition = asyncio.create_task(_lease_and_release(cache, artifact_uri))
            await lookup_started.wait()
            acquisition.cancel()
            await rollback_started.wait()

            acquisition.cancel()
            await asyncio.sleep(0)
            assert not acquisition.done()
            finish_rollback.set()

            with pytest.raises(asyncio.CancelledError):
                await acquisition

        assert cache._refcount(cache_key) == 0

    @pytest.mark.anyio
    async def test_cancelled_waiter_preserves_existing_same_key_lease(
        self, temp_cache_dir: Path
    ) -> None:
        """A waiter cancelled before admission cannot release another holder."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/shared.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        lock = cache._runtime_for(cache_key).lock

        async def take_lease() -> None:
            async with cache.lease([artifact_uri]):
                pytest.fail("cancelled waiter must not enter the lease context")

        unmount_idle_entry = AsyncMock()
        with patch.object(cache, "_unmount_idle_entry", unmount_idle_entry):
            async with lock:
                cache._acquire_lease(cache_key)
                try:
                    waiter = asyncio.create_task(take_lease())
                    await asyncio.sleep(0)
                    assert not waiter.done()

                    waiter.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await waiter

                    assert cache._refcount(cache_key) == 1
                    unmount_idle_entry.assert_not_awaited()
                finally:
                    cache._release_lease(cache_key)

    @pytest.mark.anyio
    async def test_mutable_lease_without_uris_returns_no_paths(self, temp_cache_dir):
        """An empty mutable lease exposes no unaccounted cache directory."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache._budget_dirty = False

        with (
            patch.object(
                cache,
                "ensure_swept",
                new_callable=AsyncMock,
                side_effect=AssertionError("cache-free leases must not sweep"),
            ) as ensure_swept,
            patch.object(
                cache,
                "_converge_cache_budget",
                new_callable=AsyncMock,
                side_effect=AssertionError("empty leases must not converge"),
            ) as converge_cache_budget,
        ):
            async with cache.lease(
                None,
                paths_may_be_modified=True,
            ) as registry_paths:
                assert registry_paths == []

        ensure_swept.assert_not_awaited()
        converge_cache_budget.assert_not_awaited()
        assert cache._budget_dirty is False
        assert cache._runtime == {}
        assert not (temp_cache_dir / "base").exists()

    @pytest.mark.anyio
    async def test_lease_preserves_uri_order(self, temp_cache_dir):
        """Multiple artifacts keep their deterministic PYTHONPATH order."""
        cache = RegistryArtifactCache(temp_cache_dir)
        uris = ["s3://bucket/first.tar.gz", "s3://bucket/second.tar.gz"]
        expected = [
            _write_tarball_entry(
                temp_cache_dir, compute_registry_artifact_cache_key(uri)
            )
            for uri in uris
        ]

        async with cache.lease(uris) as registry_paths:
            assert registry_paths == expected

    @pytest.mark.anyio
    async def test_overlapping_same_key_leases_share_one_mount_until_final_release(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect refcount and loop-device lifetime under heavy fan-in.

        Every holder must observe one published mount, intermediate releases
        must leave that mount usable, and exactly the final release may reclaim
        its loop device while retaining the downloaded image for a remount.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/shared.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = _SquashfsMountHarness(cache)
        holder_count = 32
        entered = 0
        all_entered = asyncio.Event()
        releases = [asyncio.Event() for _ in range(holder_count)]

        async def hold_lease(index: int) -> None:
            nonlocal entered
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).squashfs_mount_dir
                ]
                entered += 1
                if entered == holder_count:
                    all_entered.set()
                await releases[index].wait()

        with (
            patch(MOUNT_CHECK, lambda path: path in harness.mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            holders = [
                asyncio.create_task(hold_lease(index)) for index in range(holder_count)
            ]
            await asyncio.wait_for(all_entered.wait(), timeout=5)

            mount_dir = cache._paths_for(cache_key).squashfs_mount_dir
            assert cache._refcount(cache_key) == holder_count
            assert harness.mount_attempts == [cache_key]
            assert harness.extraction_attempts == []
            assert mount_dir in harness.mounted

            for release in releases[:-1]:
                release.set()
            await asyncio.gather(*holders[:-1])

            assert cache._refcount(cache_key) == 1
            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            releases[-1].set()
            await holders[-1]

        assert cache._refcount(cache_key) == 0
        assert harness.unmounts == [mount_dir]
        assert mount_dir not in harness.mounted
        assert cache._paths_for(cache_key).squashfs_image_path.is_file()
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())
        assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_cancelling_one_holder_preserves_sibling_leases(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect sibling actions from another holder's cancellation.

        Cancellation must release exactly one pin without unmounting the shared
        artifact beneath surviving actions; the last surviving holder remains
        solely responsible for loop-device reclamation.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/cancel-one.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = _SquashfsMountHarness(cache)
        entered = [asyncio.Event() for _ in range(3)]
        releases = [asyncio.Event() for _ in range(3)]

        async def hold_lease(index: int) -> None:
            async with cache.lease([artifact_uri]):
                entered[index].set()
                await releases[index].wait()

        with (
            patch(MOUNT_CHECK, lambda path: path in harness.mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            holders = [asyncio.create_task(hold_lease(index)) for index in range(3)]
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in entered)),
                timeout=5,
            )

            holders[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await holders[0]

            mount_dir = cache._paths_for(cache_key).squashfs_mount_dir
            assert cache._refcount(cache_key) == 2
            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            releases[1].set()
            await holders[1]
            assert cache._refcount(cache_key) == 1
            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            releases[2].set()
            await holders[2]

        assert cache._refcount(cache_key) == 0
        assert harness.mount_attempts == [cache_key]
        assert harness.unmounts == [mount_dir]

    @pytest.mark.anyio
    async def test_repeated_cancellation_finishes_all_lease_cleanup(
        self, temp_cache_dir: Path
    ) -> None:
        """Every unmount and budget pass finishes before cancellation propagates."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uris = [
            "s3://bucket/first.tar.gz",
            "s3://bucket/second.tar.gz",
        ]
        cache_keys = [compute_registry_artifact_cache_key(uri) for uri in artifact_uris]
        for cache_key in cache_keys:
            _write_tarball_entry(temp_cache_dir, cache_key)

        lease_entered = asyncio.Event()
        first_unmount_started = asyncio.Event()
        finish_first_unmount = asyncio.Event()
        cleanup_calls: list[str] = []

        async def mock_unmount_idle_entry(cache_key: str) -> None:
            cleanup_calls.append(cache_key)
            if cache_key == cache_keys[0]:
                first_unmount_started.set()
                await finish_first_unmount.wait()

        async def mock_converge_cache_budget() -> None:
            cleanup_calls.append("converge")

        async def hold_lease() -> None:
            async with cache.lease(artifact_uris):
                lease_entered.set()
                await asyncio.Event().wait()

        with (
            patch.object(cache, "_unmount_idle_entry", mock_unmount_idle_entry),
            patch.object(cache, "_converge_cache_budget", mock_converge_cache_budget),
        ):
            holder = asyncio.create_task(hold_lease())
            await lease_entered.wait()
            holder.cancel()
            await first_unmount_started.wait()

            holder.cancel()
            await asyncio.sleep(0)
            assert not holder.done()

            finish_first_unmount.set()
            with pytest.raises(asyncio.CancelledError):
                await holder

        assert cleanup_calls == [
            cache_keys[1],
            "converge",
            cache_keys[0],
            "converge",
        ]
        assert all(cache._refcount(cache_key) == 0 for cache_key in cache_keys)

    @pytest.mark.anyio
    async def test_cleanup_failure_preserves_successful_lease_outcome(
        self, temp_cache_dir: Path
    ) -> None:
        """Post-lease maintenance cannot replace a successful caller result."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/cleanup-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = _write_tarball_entry(temp_cache_dir, cache_key)

        async def fail_cleanup(idle_keys: list[str]) -> None:
            del idle_keys
            raise RuntimeError("cleanup failed with sensitive details")

        with (
            patch.object(cache, "_finish_lease_cleanup", fail_cleanup),
            patch("tracecat.executor.registry_artifacts.logger.error") as log_error,
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                result = registry_paths

        assert result == [target_dir]
        assert cache._refcount(cache_key) == 0
        log_error.assert_called_once_with(
            "Registry artifact lease cleanup failed; preserving caller outcome",
            cache_dir=str(temp_cache_dir),
            error_type="RuntimeError",
        )

    @pytest.mark.anyio
    async def test_new_lease_racing_final_release_prevents_stale_unmount(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect the zero-refcount-to-unmount handoff from a new acquisition.

        A lease can arrive after the old holder decrements to zero but before
        unmount takes the key lock. The lock-time refcount recheck must preserve
        the mount for that newcomer instead of returning a path being torn down.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/release-race.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = _SquashfsMountHarness(cache)
        mount_dir = harness.seed_mount(cache_key)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        release_reached_unmount = asyncio.Event()
        allow_unmount_recheck = asyncio.Event()
        newcomer_entered = asyncio.Event()
        release_newcomer = asyncio.Event()
        original_unmount_idle_entry = cache._unmount_idle_entry
        unmount_requests = 0

        async def pause_first_unmount_request(requested_key: str) -> None:
            nonlocal unmount_requests
            unmount_requests += 1
            if unmount_requests == 1:
                release_reached_unmount.set()
                await allow_unmount_recheck.wait()
            await original_unmount_idle_entry(requested_key)

        async def old_holder() -> None:
            async with cache.lease([artifact_uri]):
                first_entered.set()
                await release_first.wait()

        async def new_holder() -> None:
            async with cache.lease([artifact_uri]):
                newcomer_entered.set()
                await release_newcomer.wait()

        with (
            patch(MOUNT_CHECK, lambda path: path in harness.mounted),
            patch.object(cache, "_unmount", harness.unmount),
            patch.object(
                cache,
                "_unmount_idle_entry",
                pause_first_unmount_request,
            ),
        ):
            old_task = asyncio.create_task(old_holder())
            await first_entered.wait()
            release_first.set()
            await release_reached_unmount.wait()
            assert cache._refcount(cache_key) == 0

            new_task = asyncio.create_task(new_holder())
            await newcomer_entered.wait()
            assert cache._refcount(cache_key) == 1

            allow_unmount_recheck.set()
            await old_task

            assert mount_dir in harness.mounted
            assert harness.unmounts == []

            release_newcomer.set()
            await new_task

        assert cache._refcount(cache_key) == 0
        assert harness.unmounts == [mount_dir]

    @pytest.mark.anyio
    async def test_partial_multi_artifact_failure_rolls_back_prior_leases(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect sequential multi-artifact admission from partial pin leaks.

        If a middle artifact fails, every earlier acquisition must be released
        and unmounted, the failed key must leave no shell, later artifacts must
        never be acquired, and the release path must still request convergence.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        first_uri = "s3://bucket/first.squashfs"
        failed_uri = "s3://bucket/failed.tar.gz"
        untouched_uri = "s3://bucket/untouched.tar.gz"
        first_key = compute_registry_artifact_cache_key(first_uri)
        failed_key = compute_registry_artifact_cache_key(failed_uri)
        untouched_key = compute_registry_artifact_cache_key(untouched_uri)
        harness = _SquashfsMountHarness(cache)
        first_mount = harness.seed_mount(first_key)
        untouched_path = _write_tarball_entry(temp_cache_dir, untouched_key)

        async def fail_download(
            artifact: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
            output_path: Path,
        ) -> None:
            del artifact, ctx, output_path
            raise RuntimeError("download failed")

        tracked_acquire_artifact = AsyncMock(wraps=cache._acquire_artifact)
        converge_cache_budget = AsyncMock()

        with (
            patch(MOUNT_CHECK, lambda path: path in harness.mounted),
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", fail_download),
            patch.object(cache, "_unmount", harness.unmount),
            patch.object(cache, "_acquire_artifact", tracked_acquire_artifact),
            patch.object(
                cache,
                "_converge_cache_budget",
                converge_cache_budget,
            ),
        ):
            with pytest.raises(RuntimeError, match="download failed"):
                async with cache.lease([first_uri, failed_uri, untouched_uri]):
                    pass

        requested_uris = [
            await_call.args[0].artifact_uri
            for await_call in tracked_acquire_artifact.await_args_list
        ]
        assert requested_uris == [first_uri, failed_uri]
        assert cache._refcount(first_key) == 0
        assert cache._refcount(failed_key) == 0
        assert cache._refcount(untouched_key) == 0
        assert harness.unmounts == [first_mount]
        assert cache._paths_for(first_key).squashfs_image_path.is_file()
        assert not cache._paths_for(failed_key).entry_dir.exists()
        assert untouched_path.is_dir()
        assert converge_cache_budget.await_count == 2
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())
        assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_waiter_retries_after_first_same_key_materializer_fails(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect same-key waiters from a failed cold-cache publisher.

        The first materializer may fail after writing scratch. Its waiter must
        retry against a clean staging area, publish the sole valid entry, and
        leave neither a leaked pin nor an empty LRU-visible cache shell.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/retry-after-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        first_download_started = asyncio.Event()
        fail_first_download = asyncio.Event()
        retry_download_started = asyncio.Event()
        allow_retry_download = asyncio.Event()
        download_attempts = 0
        retry_saw_clean_staging = False

        async def controlled_download(
            artifact: TarballArtifact,
            ctx: RegistryArtifactMaterializationContext,
            output_path: Path,
        ) -> None:
            del artifact, ctx
            nonlocal download_attempts, retry_saw_clean_staging
            download_attempts += 1
            if download_attempts == 1:
                output_path.write_bytes(b"partial")
                first_download_started.set()
                await fail_first_download.wait()
                raise RuntimeError("first publisher failed")

            retry_saw_clean_staging = not any(cache.staging_dir.iterdir())
            retry_download_started.set()
            await allow_retry_download.wait()
            output_path.write_bytes(b"complete")

        async def mock_extract(
            artifact: TarballArtifact,
            tarball_path: Path,
            target_dir: Path,
        ) -> None:
            del artifact
            assert tarball_path.read_bytes() == b"complete"
            (target_dir / "module.py").write_text("VALUE = 1")

        async def take_lease() -> list[Path]:
            async with cache.lease([artifact_uri]) as registry_paths:
                return registry_paths

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch(
                "tracecat.executor.registry_artifacts._tarball_extracted_size",
                return_value=1,
            ),
            patch.object(TarballArtifact, "download", controlled_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            first = asyncio.create_task(take_lease())
            await first_download_started.wait()
            waiter = asyncio.create_task(take_lease())
            await asyncio.sleep(0)
            assert download_attempts == 1

            fail_first_download.set()
            await retry_download_started.wait()
            allow_retry_download.set()
            registry_paths = await waiter
            with pytest.raises(RuntimeError, match="first publisher failed"):
                await first

        target_dir = cache._paths_for(cache_key).tarball_target_dir
        assert registry_paths == [target_dir]
        assert (target_dir / "module.py").read_text() == "VALUE = 1"
        assert download_attempts == 2
        assert retry_saw_clean_staging is True
        assert cache._discover_cache_keys() == {cache_key}
        assert cache._refcount(cache_key) == 0
        assert not any(cache.staging_dir.iterdir())
        assert not cache.trash_dir.exists() or not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_duplicate_uri_balances_each_acquisition_and_release(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect list bookkeeping when one lease requests the same URI twice.

        Duplicate PYTHONPATH entries intentionally acquire two pins. Both must
        be released, while materialization and final unmount still occur once;
        accidental deduplication on only one side would leak or underflow pins.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/duplicate.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        harness = _SquashfsMountHarness(cache)

        with (
            patch(MOUNT_CHECK, lambda path: path in harness.mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            async with cache.lease([artifact_uri, artifact_uri]) as registry_paths:
                mount_dir = cache._paths_for(cache_key).squashfs_mount_dir
                assert registry_paths == [mount_dir, mount_dir]
                assert cache._refcount(cache_key) == 2
                assert harness.mount_attempts == [cache_key]

        assert cache._refcount(cache_key) == 0
        assert harness.unmounts == [mount_dir]
        assert cache._paths_for(cache_key).squashfs_image_path.is_file()

    @pytest.mark.anyio
    async def test_lease_is_never_admitted_across_an_in_flight_eviction(
        self, temp_cache_dir
    ):
        """A lease must not return a mount an in-flight eviction is deleting."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}
        umount_started = asyncio.Event()
        finish_umount = asyncio.Event()
        remounts: list[str] = []

        umount_process = AsyncMock()
        umount_process.communicate.return_value = (b"", b"")
        umount_process.returncode = 0

        async def mock_umount(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            umount_started.set()
            await finish_umount.wait()
            mounted.discard(paths.squashfs_mount_dir)
            return umount_process

        async def mock_mount(self, ctx, image_path):
            remounts.append(ctx.cache_key)
            target_dir = ctx.paths.squashfs_mount_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            mounted.add(target_dir)
            return target_dir

        leased_paths: list[Path] = []
        leased_path_exists: list[bool] = []

        async def take_lease() -> None:
            async with cache.lease([artifact_uri]) as registry_paths:
                leased_paths.extend(registry_paths)
                leased_path_exists.append(registry_paths[0].is_dir())

        with (
            patch(MOUNT_CHECK, lambda path: path in mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch(
                "tracecat.sandbox.utils.terminate_process_group",
                new_callable=AsyncMock,
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            await umount_started.wait()
            lease = asyncio.create_task(take_lease())
            # Let the lease block on the per-key lock the eviction holds.
            await asyncio.sleep(0)
            finish_umount.set()
            evicted, _ = await asyncio.gather(eviction, lease)

        assert evicted == RegistryArtifactEviction(retired=True, reclaimed=True)
        # The lease waited for the eviction and re-materialized the entry.
        assert remounts == [cache_key]
        assert leased_paths == [paths.squashfs_mount_dir]
        assert leased_path_exists == [True]
        assert (paths.squashfs_mount_dir / "module.py").read_text() == "VALUE = 1"

    @pytest.mark.anyio
    async def test_builtin_artifact_is_exempt_from_cache_accounting(
        self, temp_cache_dir, monkeypatch: pytest.MonkeyPatch
    ):
        """The bundled builtin registry is never a cache entry."""
        version = "1.2.3"
        site_packages = temp_cache_dir / "venv" / "site-packages"
        package_dir = site_packages / "tracecat_registry"
        package_dir.mkdir(parents=True)
        package_file = package_dir / "__init__.py"
        package_file.write_text("")

        monkeypatch.setattr(tracecat_registry, "__version__", version)
        monkeypatch.setattr(tracecat_registry, "__file__", str(package_file))
        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.sysconfig.get_path",
            lambda name: str(site_packages) if name == "purelib" else None,
        )

        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch.object(
                cache,
                "ensure_swept",
                new_callable=AsyncMock,
                side_effect=AssertionError("builtin leases must not sweep"),
            ) as ensure_swept,
            patch.object(
                cache,
                "_enforce_cache_budget",
                new_callable=AsyncMock,
            ) as enforce_cache_budget,
        ):
            async with cache.lease([bundled_builtin_registry_uri(version)]) as paths:
                assert paths == [site_packages.resolve()]
                assert cache._runtime == {}

        ensure_swept.assert_not_awaited()
        enforce_cache_budget.assert_not_awaited()


class TestRegistryArtifactCacheEviction:
    """Tests for bounded eviction of registry artifact cache entries."""

    def test_snapshot_accounts_for_invalid_entry_children(
        self, temp_cache_dir: Path
    ) -> None:
        """Files and symlinks directly under entries remain budget-visible."""
        cache = RegistryArtifactCache(temp_cache_dir)
        before = cache._scan_cache_snapshot()
        invalid_file = cache.entries_dir / "invalid-file"
        invalid_file.write_bytes(b"x" * 65536)
        invalid_link = cache.entries_dir / "invalid-link"
        invalid_link.symlink_to(invalid_file)
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)
        invalid_bytes = _allocated_stat_size(
            invalid_file.lstat(), allocation_unit=allocation_unit
        ) + _allocated_stat_size(invalid_link.lstat(), allocation_unit=allocation_unit)

        after = cache._scan_cache_snapshot()

        assert after.entries == {}
        assert after.structural_bytes >= before.structural_bytes + invalid_bytes

    def test_delete_cache_path_reports_directory_failure(self, temp_cache_dir):
        """Physical deletion failures are observable instead of suppressed."""
        entry_dir = temp_cache_dir / "entry"
        entry_dir.mkdir()

        with (
            patch(
                "tracecat.executor.registry_artifacts.shutil.rmtree",
                side_effect=OSError("permission denied"),
            ),
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            deleted = _delete_cache_path(entry_dir)

        assert deleted is False
        assert entry_dir.is_dir()
        warning.assert_called_once_with(
            "Failed to delete registry artifact cache path",
            path=str(entry_dir),
            error="permission denied",
        )

    @pytest.mark.anyio
    async def test_leased_entry_survives_eviction_of_idle_entry(self, temp_cache_dir):
        """Eviction must never remove an entry a live action is importing from."""
        cache = RegistryArtifactCache(temp_cache_dir)
        leased_uri = "s3://bucket/leased.tar.gz"
        idle_uri = "s3://bucket/idle.tar.gz"
        new_uri = "s3://bucket/new.tar.gz"
        leased_dir = _write_tarball_entry(
            temp_cache_dir, compute_registry_artifact_cache_key(leased_uri)
        )
        idle_dir = _write_tarball_entry(
            temp_cache_dir, compute_registry_artifact_cache_key(idle_uri)
        )

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 2),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([leased_uri]):
                await _materialize(
                    cache, compute_registry_artifact_cache_key(new_uri), new_uri
                )

        assert leased_dir.is_dir()
        assert not idle_dir.exists()

    @pytest.mark.anyio
    async def test_cold_download_reserves_space_before_writing(
        self, temp_cache_dir: Path
    ) -> None:
        """Admission evicts idle bytes before a new download enters staging."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        idle = _write_image_entry(
            temp_cache_dir,
            "idle",
            size=128 * 1024,
            mtime=100.0,
        )
        artifact_uri = "s3://bucket/new.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        payload = _tarball_payload(size=32)
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)
        structural_bytes = cache._scan_cache_snapshot().structural_bytes
        max_bytes = structural_bytes + 7 * allocation_unit
        capacity_checked = False

        async def download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            max_bytes: int,
            ensure_capacity: Callable[[int], Awaitable[None]],
            defer_cleanup: Callable[[Path], None] | None,
            redact_log_identifiers: bool,
        ) -> int:
            del key, bucket
            nonlocal capacity_checked
            assert max_bytes == structural_bytes + 7 * allocation_unit
            assert defer_cleanup is not None
            assert redact_log_identifiers is True
            await ensure_capacity(len(payload))
            capacity_checked = True
            assert not idle.exists()
            output_path.write_bytes(payload)
            return len(payload)

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifacts.blob.download_file_to_path",
                side_effect=download_file_to_path,
            ),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).tarball_target_dir
                ]

        assert capacity_checked is True
        assert not idle.exists()
        assert (registry_paths[0] / "module.py").read_bytes() == b"x" * 32

    @pytest.mark.anyio
    async def test_impossible_tarball_reservation_preserves_warm_entry(
        self, temp_cache_dir: Path
    ) -> None:
        """Impossible extraction cannot evict warm entries before rejection."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        warm = _write_image_entry(temp_cache_dir, "warm", size=64, mtime=100.0)
        artifact_uri = "s3://bucket/compression-heavy.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        payload = _tarball_payload(size=4096)
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)
        snapshot = cache._scan_cache_snapshot()
        payload_reservation = allocated_size_bound(
            len(payload),
            allocation_unit=allocation_unit,
        )
        # The download reservation includes one staging directory record.
        max_bytes = snapshot.total_bytes + allocation_unit + payload_reservation
        max_bytes += allocation_unit
        extracted_size = (
            allocated_size_bound(4096, allocation_unit=allocation_unit)
            + allocated_size_bound(0, allocation_unit=allocation_unit)
            + allocated_size_bound(
                32 + len(os.fsencode("module.py")),
                allocation_unit=allocation_unit,
            )
            # Staged and canonical directory records remain allocated after rename.
            + 2 * allocation_unit
        )

        async def download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            max_bytes: int,
            ensure_capacity: Callable[[int], Awaitable[None]],
            defer_cleanup: Callable[[Path], None] | None,
            redact_log_identifiers: bool,
        ) -> int:
            del key, bucket
            assert (
                max_bytes
                == snapshot.total_bytes + 2 * allocation_unit + payload_reservation
            )
            assert defer_cleanup is not None
            assert redact_log_identifiers is True
            await ensure_capacity(len(payload))
            output_path.write_bytes(payload)
            return len(payload)

        with (
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifacts.blob.download_file_to_path",
                side_effect=download_file_to_path,
            ),
            patch.object(
                TarballArtifact,
                "extract",
                new_callable=AsyncMock,
            ) as extract,
            patch.object(
                cache, "_evict_entry", wraps=cache._evict_entry
            ) as evict_entry,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                async with cache.lease([artifact_uri]):
                    pass

        assert raised.value.additional_bytes == extracted_size
        assert raised.value.max_bytes == max_bytes
        extract.assert_not_awaited()
        evict_entry.assert_not_awaited()
        assert warm.is_file()
        assert not cache._paths_for(cache_key).entry_dir.exists()
        assert not cache.staging_dir.exists() or not any(cache.staging_dir.iterdir())

    @pytest.mark.anyio
    async def test_squashfs_expansion_is_rejected_before_extraction(
        self, temp_cache_dir: Path
    ) -> None:
        """SquashFS metadata is accounted before unsquashfs writes scratch."""
        cache = RegistryArtifactCache(temp_cache_dir)
        structural_bytes = cache._scan_cache_snapshot().structural_bytes
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)
        reserved_expansion = allocated_size_bound(
            101,
            allocation_unit=allocation_unit,
        )
        reserved_expansion += 2 * allocation_unit
        max_bytes = structural_bytes + 2 * allocation_unit + reserved_expansion - 1
        artifact_uri = "s3://bucket/oversized.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def download(
            self: SquashfsArtifact,
            ctx: RegistryArtifactMaterializationContext,
            image_path: Path,
        ) -> float:
            del self, ctx
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"image")
            return 0.0

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch.object(
                RegistryArtifactMaterializationContext,
                "can_mount_squashfs",
                return_value=False,
            ),
            patch.object(SquashfsArtifact, "download", download),
            patch.object(
                SquashfsArtifact,
                "_squashfs_extracted_size",
                new_callable=AsyncMock,
                return_value=101,
            ),
            patch.object(
                SquashfsArtifact,
                "_extract_image",
                new_callable=AsyncMock,
            ) as extract_image,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                async with cache.lease([artifact_uri]):
                    pass

        assert raised.value.additional_bytes == reserved_expansion
        extract_image.assert_not_awaited()
        paths = cache._paths_for(cache_key)
        assert paths.squashfs_image_path.read_bytes() == b"image"
        assert not paths.squashfs_extract_dir.exists()

    @pytest.mark.anyio
    async def test_successful_admission_enforces_actual_size_before_yield(
        self, temp_cache_dir
    ):
        """Post-publication enforcement sees the new entry's actual size."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)
        idle = _write_image_entry(temp_cache_dir, "idle", size=4096, mtime=100.0)
        snapshot = cache._scan_cache_snapshot()
        max_bytes = snapshot.total_bytes + 3 * allocation_unit
        new_uri = "s3://bucket/new.tar.gz"
        new_key = compute_registry_artifact_cache_key(new_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_bytes(b"x" * (2 * allocation_unit))

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifacts._tarball_extracted_size",
                return_value=allocation_unit,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([new_uri]) as registry_paths:
                assert registry_paths == [cache._paths_for(new_key).tarball_target_dir]
                assert not idle.exists()

        assert not idle.exists()
        assert cache._paths_for(new_key).tarball_target_dir.is_dir()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_deletion_failure_does_not_block_materialization(
        self, temp_cache_dir
    ):
        """Failed cleanup is reported while cache admission remains fail-open."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        _write_image_entry(temp_cache_dir, "idle", size=4096, mtime=100.0)
        artifact_uri = "s3://bucket/new.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                return_value=False,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            registry_paths = await _materialize(cache, cache_key, artifact_uri)

        assert registry_paths == [cache._paths_for(cache_key).tarball_target_dir]
        assert registry_paths[0].is_dir()
        assert cache._budget_dirty is True
        warning.assert_any_call(
            "Registry artifact eviction remains pending physical deletion",
            cache_key="idle",
            trash_path=ANY,
        )

    @pytest.mark.anyio
    async def test_failed_physical_delete_retries_without_extra_eviction(
        self, temp_cache_dir
    ):
        """Failed byte reclamation stops eviction until trash cleanup succeeds."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest = _write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        older = _write_image_entry(
            temp_cache_dir,
            "older",
            size=16,
            mtime=200.0,
        )
        newest = _write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=300.0,
        )
        snapshot = cache._scan_cache_snapshot()
        max_bytes = snapshot.structural_bytes + snapshot.entries["newest"].size_bytes
        real_delete = _delete_cache_path
        failed_once = False

        def fail_once(path: Path) -> bool:
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                return False
            return real_delete(path)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=fail_once,
            ),
        ):
            assert await cache._enforce_cache_budget() is False
            assert not oldest.exists()
            assert older.exists()
            assert newest.exists()
            assert cache._discover_cache_keys() == {"older", "newest"}
            assert len(tuple(cache.trash_dir.iterdir())) == 1

            assert await cache._enforce_cache_budget() is True

        assert not older.exists()
        assert newest.exists()
        assert not any(cache.trash_dir.iterdir())

    @pytest.mark.anyio
    async def test_undeletable_trash_does_not_evict_warm_entries_for_admission(
        self, temp_cache_dir: Path
    ) -> None:
        """Unreclaimed trash blocks a write without cascading eviction."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        warm = _write_image_entry(temp_cache_dir, "warm", size=32, mtime=100.0)
        stale_trash = cache.trash_dir / "stale"
        stale_trash.mkdir(parents=True)
        (stale_trash / "image.squashfs").write_bytes(b"x" * 32)
        snapshot = cache._scan_cache_snapshot()
        allocation_unit = _filesystem_allocation_unit(temp_cache_dir)
        real_delete = _delete_cache_path

        def fail_stale_trash(path: Path) -> bool:
            if path == stale_trash:
                return False
            return real_delete(path)

        with patch(
            "tracecat.executor.registry_artifact_storage._delete_cache_path",
            side_effect=fail_stale_trash,
        ):
            with pytest.raises(RegistryArtifactCacheCapacityError) as raised:
                async with cache._admission_lock:
                    await cache._ensure_cache_capacity(
                        additional_bytes=allocation_unit,
                        protected_key="new",
                        max_bytes=snapshot.total_bytes,
                    )

        assert raised.value.current_bytes == snapshot.total_bytes
        assert warm.exists()
        assert stale_trash.exists()

    @pytest.mark.anyio
    async def test_rename_failure_does_not_block_materialization(self, temp_cache_dir):
        """A failed atomic retirement keeps the old entry and admits new work."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        idle = _write_image_entry(temp_cache_dir, "idle", size=16, mtime=100.0)
        artifact_uri = "s3://bucket/new-after-rename-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._move_entry_to_trash",
                side_effect=OSError("rename failed"),
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            registry_paths = await _materialize(cache, cache_key, artifact_uri)

        assert registry_paths == [cache._paths_for(cache_key).tarball_target_dir]
        assert registry_paths[0].is_dir()
        assert idle.is_file()
        assert cache._budget_dirty is True

    @pytest.mark.anyio
    async def test_cache_scan_failure_does_not_block_materialization(
        self, temp_cache_dir
    ):
        """Maintenance errors are observable while artifact admission stays open."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/new-after-scan-failure.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch.object(
                cache,
                "_enforce_cache_budget",
                new_callable=AsyncMock,
                side_effect=PermissionError("denied"),
            ),
            patch(
                "tracecat.executor.registry_artifacts._tarball_extracted_size",
                return_value=9,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [
                    cache._paths_for(cache_key).tarball_target_dir
                ]

    @pytest.mark.anyio
    async def test_releasing_a_lease_skips_the_scan_for_a_cache_hit(
        self, temp_cache_dir
    ):
        """Steady-state cache hits must not pay for a full cache scan."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/cached.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        _write_tarball_entry(temp_cache_dir, cache_key)
        cache._budget_dirty = False

        with patch.object(
            cache,
            "_scan_cache_entries",
            side_effect=AssertionError("cache hits must not scan the cache dir"),
        ):
            async with cache.lease([artifact_uri]):
                pass

        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_mutable_cache_hit_rescans_unknown_entry_growth(
        self, temp_cache_dir: Path
    ) -> None:
        """Writable direct actions cannot grow a warm entry outside the cap."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/mutable-cached.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = _write_tarball_entry(temp_cache_dir, cache_key)
        entry_dir = cache._paths_for(cache_key).entry_dir
        max_bytes = cache._scan_cache_snapshot().total_bytes
        cache._budget_dirty = False

        with (
            patch(MAX_ENTRIES_CONFIG, 10),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch.object(
                cache,
                "_scan_cache_snapshot",
                wraps=cache._scan_cache_snapshot,
            ) as scan_cache_snapshot,
        ):
            async with cache.lease(
                [artifact_uri],
                paths_may_be_modified=True,
            ) as registry_paths:
                assert registry_paths == [target_dir]
                (entry_dir / "action-output.bin").write_bytes(b"x" * 4096)

        assert scan_cache_snapshot.call_count == 1
        assert not entry_dir.exists()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_failed_cold_admission_keeps_warm_lru(self, temp_cache_dir):
        """A missing cold artifact must not evict an existing warm entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        warm_uri = "s3://bucket/warm.tar.gz"
        warm_key = compute_registry_artifact_cache_key(warm_uri)
        warm_dir = _write_tarball_entry(temp_cache_dir, warm_key)
        missing_uri = "s3://bucket/missing.tar.gz"
        missing_key = compute_registry_artifact_cache_key(missing_uri)

        async def mock_download(self, ctx, path):
            raise FileNotFoundError("missing artifact")

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(SQUASHFS_ENABLED_CONFIG, False),
            patch.object(TarballArtifact, "download", mock_download),
        ):
            with pytest.raises(FileNotFoundError, match="missing artifact"):
                async with cache.lease([missing_uri]):
                    pytest.fail("failed admission must not yield a lease")

        assert warm_dir.is_dir()
        assert not cache._paths_for(missing_key).entry_dir.exists()

    @pytest.mark.anyio
    async def test_failed_materialization_converges_deposited_image(
        self, temp_cache_dir
    ):
        """A failed materialization still runs release-time budget enforcement."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        assert cache._budget_dirty is False

        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)
        enforce_cache_budget = AsyncMock(return_value=True)

        async def mock_materialize(self, ctx):
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
            ctx.paths.squashfs_image_path.write_bytes(b"orphaned image")
            raise RuntimeError("mount failed")

        with (
            patch.object(
                cache,
                "_artifact_candidates",
                new_callable=AsyncMock,
                return_value=[artifact],
            ),
            patch.object(SquashfsArtifact, "materialize", mock_materialize),
            patch.object(cache, "_enforce_cache_budget", enforce_cache_budget),
        ):
            with pytest.raises(RuntimeError, match="mount failed"):
                await _materialize(cache, cache_key, artifact_uri)

        assert cache._paths_for(cache_key).squashfs_image_path.is_file()
        assert cache._budget_dirty is False
        enforce_cache_budget.assert_awaited_once_with()

    @pytest.mark.anyio
    async def test_materialization_rearms_budget_dirty_consumed_mid_flight(
        self, temp_cache_dir
    ):
        """A convergence pass consuming the signal mid-download cannot unarm it."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        assert cache._budget_dirty is False

        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)
        enforce_cache_budget = AsyncMock(return_value=True)

        async def mock_materialize(self, ctx):
            # A concurrent lease release consumes the dirty signal and finishes
            # its scan before this attempt lands its canonical bytes.
            cache._budget_dirty = False
            ctx.paths.entry_dir.mkdir(parents=True, exist_ok=True)
            ctx.paths.squashfs_image_path.write_bytes(b"late image")
            return [ctx.paths.squashfs_mount_dir]

        with (
            patch.object(
                cache,
                "_artifact_candidates",
                new_callable=AsyncMock,
                return_value=[artifact],
            ),
            patch.object(SquashfsArtifact, "materialize", mock_materialize),
            patch.object(cache, "_enforce_cache_budget", enforce_cache_budget),
        ):
            await _materialize(cache, cache_key, artifact_uri)

        assert cache._budget_dirty is False
        assert enforce_cache_budget.await_args_list == [
            call(protected_key=cache_key),
            call(),
        ]

    @pytest.mark.anyio
    async def test_release_keeps_retrying_while_the_cache_stays_over_budget(
        self, temp_cache_dir
    ):
        """A cache that cannot shrink yet must stay marked for re-enforcement."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/pinned.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        _write_image_entry(temp_cache_dir, cache_key, size=4096, mtime=100.0)
        _write_tarball_entry(temp_cache_dir, cache_key)
        cache._budget_dirty = True
        # A second holder keeps the entry pinned past the inner lease.
        cache._acquire_lease(cache_key)

        with patch(MAX_ENTRIES_CONFIG, 0), patch(MAX_BYTES_CONFIG, 1):
            async with cache.lease([artifact_uri]):
                pass

        assert cache._budget_dirty is True

    @pytest.mark.anyio
    async def test_convergence_rescans_after_concurrent_materialization(
        self, temp_cache_dir
    ):
        """A materialization during a budget scan must schedule another scan."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/concurrent.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        scan_started = asyncio.Event()
        materialized = asyncio.Event()
        convergence_scans = 0

        async def mock_enforce_cache_budget(
            *, protected_key: str | None = None
        ) -> bool:
            nonlocal convergence_scans
            if protected_key is not None:
                return True

            convergence_scans += 1
            if convergence_scans == 1:
                scan_started.set()
                await materialized.wait()
            return True

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 1")

        cache._budget_dirty = True
        with (
            patch.object(
                cache,
                "_enforce_cache_budget",
                side_effect=mock_enforce_cache_budget,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            convergence = asyncio.create_task(cache._converge_cache_budget())
            await scan_started.wait()
            registry_paths = await _materialize(cache, cache_key, artifact_uri)
            materialized.set()
            await convergence

        assert registry_paths == [cache._paths_for(cache_key).tarball_target_dir]
        assert convergence_scans == 2
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_cancelled_convergence_rearms_budget_dirty(
        self, temp_cache_dir
    ) -> None:
        """Cancellation must preserve the need for a later budget pass."""
        cache = RegistryArtifactCache(temp_cache_dir)
        enforcement_started = asyncio.Event()
        finish_enforcement = asyncio.Event()

        async def mock_enforce_cache_budget(
            *, protected_key: str | None = None
        ) -> bool:
            del protected_key
            enforcement_started.set()
            await finish_enforcement.wait()
            return True

        cache._budget_dirty = True
        with patch.object(
            cache,
            "_enforce_cache_budget",
            side_effect=mock_enforce_cache_budget,
        ):
            convergence = asyncio.create_task(cache._converge_cache_budget())
            await enforcement_started.wait()
            convergence.cancel()

            with pytest.raises(asyncio.CancelledError):
                await convergence

        assert cache._budget_dirty is True

    @pytest.mark.parametrize(
        "oldest_has_tarball",
        [False, True],
        ids=["squashfs-only", "tarball-bearing"],
    )
    @pytest.mark.anyio
    async def test_enforce_budget_evicts_least_recently_used_until_under_max_bytes(
        self, temp_cache_dir, oldest_has_tarball: bool
    ):
        """Size eviction stops once the cache is within budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest = _write_image_entry(temp_cache_dir, "oldest", size=4096, mtime=100.0)
        if oldest_has_tarball:
            _write_tarball_entry(temp_cache_dir, "oldest")
            oldest_entry = cache._paths_for("oldest").entry_dir
            os.utime(oldest_entry, (100.0, 100.0))
        older = _write_image_entry(temp_cache_dir, "older", size=4096, mtime=200.0)
        newest = _write_image_entry(temp_cache_dir, "newest", size=4096, mtime=300.0)
        snapshot = cache._scan_cache_snapshot()
        max_bytes = (
            snapshot.structural_bytes
            + snapshot.entries["older"].size_bytes
            + snapshot.entries["newest"].size_bytes
        )

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
        ):
            within_budget = await cache._enforce_cache_budget(protected_key="pending")

        assert within_budget is True
        assert not oldest.exists()
        assert not cache._paths_for("oldest").tarball_target_dir.exists()
        assert older.exists()
        assert newest.exists()

    @pytest.mark.anyio
    async def test_final_lease_release_unmounts_and_retains_image(self, temp_cache_dir):
        """An idle entry releases its loop device without deleting its image."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}

        process = AsyncMock()
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        async def mock_umount(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            mounted.discard(paths.squashfs_mount_dir)
            return process

        with (
            patch(MOUNT_CHECK, lambda path: path in mounted),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch.object(
                asyncio,
                "create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch(
                "tracecat.sandbox.utils.terminate_process_group",
                new_callable=AsyncMock,
            ),
        ):
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [paths.squashfs_mount_dir]
                assert paths.squashfs_mount_dir in mounted

            assert paths.squashfs_mount_dir not in mounted

        assert paths.squashfs_image_path.read_bytes() == b"squashfs"
        assert paths.squashfs_mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_concurrent_budget_passes_only_evict_once(self, temp_cache_dir):
        """A waiting budget pass must re-scan after the active pass evicts."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest = _write_image_entry(temp_cache_dir, "oldest", size=16, mtime=100.0)
        retained = _write_image_entry(temp_cache_dir, "retained", size=16, mtime=200.0)
        original_scan = cache._scan_cache_entries
        first_scan_started = threading.Event()
        second_scan_started = threading.Event()
        release_first_scan = threading.Event()
        release_second_scan = threading.Event()
        scan_count_lock = threading.Lock()
        scan_count = 0

        def controlled_scan(*, allocation_unit: int | None = None):
            nonlocal scan_count
            entries = original_scan(allocation_unit=allocation_unit)
            with scan_count_lock:
                scan_index = scan_count
                scan_count += 1
            if scan_index == 0:
                first_scan_started.set()
                release_first_scan.wait(timeout=5)
            elif scan_index == 1:
                second_scan_started.set()
                release_second_scan.wait(timeout=5)
            return entries

        eviction_started = asyncio.Event()
        finish_eviction = asyncio.Event()
        extra_eviction_finished = asyncio.Event()
        evicted_keys: list[str] = []

        async def controlled_evict(
            cache_key: str,
        ) -> RegistryArtifactEviction:
            if cache_key == "oldest":
                if eviction_started.is_set():
                    return RegistryArtifactEviction(
                        retired=False,
                        reclaimed=False,
                    )
                eviction_started.set()
                await finish_eviction.wait()
                _delete_cache_path(cache._paths_for("oldest").entry_dir)
            else:
                _delete_cache_path(cache._paths_for("retained").entry_dir)
                extra_eviction_finished.set()
            evicted_keys.append(cache_key)
            return RegistryArtifactEviction(retired=True, reclaimed=True)

        with (
            patch.object(cache, "_scan_cache_entries", side_effect=controlled_scan),
            patch.object(cache, "_evict_entry", side_effect=controlled_evict),
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            first_pass = asyncio.create_task(cache._enforce_cache_budget())
            assert await asyncio.to_thread(first_scan_started.wait, 1)
            second_pass = asyncio.create_task(cache._enforce_cache_budget())
            second_scan_overlapped = await asyncio.to_thread(
                second_scan_started.wait, 0.5
            )

            release_first_scan.set()
            await asyncio.wait_for(eviction_started.wait(), timeout=1)
            release_second_scan.set()
            try:
                await asyncio.wait_for(extra_eviction_finished.wait(), timeout=0.2)
            except TimeoutError:
                pass
            finish_eviction.set()
            await asyncio.gather(first_pass, second_pass)

        assert second_scan_overlapped is False
        assert scan_count == 2
        assert evicted_keys == ["oldest"]
        assert not oldest.exists()
        assert retained.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_ignores_missing_protected_key(self, temp_cache_dir):
        """Only successfully published entries count against the entry budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        existing = _write_image_entry(temp_cache_dir, "existing", size=16, mtime=100.0)

        with patch(MAX_ENTRIES_CONFIG, 1), patch(MAX_BYTES_CONFIG, 0):
            within_budget = await cache._enforce_cache_budget(protected_key="missing")

        assert within_budget is True
        assert existing.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_never_evicts_the_protected_key(self, temp_cache_dir):
        """The newly materialized key is exempt even when it is the LRU entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        protected = _write_image_entry(
            temp_cache_dir, "protected", size=4096, mtime=100.0
        )
        other = _write_image_entry(temp_cache_dir, "other", size=4096, mtime=300.0)

        with patch(MAX_ENTRIES_CONFIG, 1), patch(MAX_BYTES_CONFIG, 0):
            await cache._enforce_cache_budget(protected_key="protected")

        assert protected.exists()
        assert not other.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_proceeds_over_budget_when_everything_is_leased(
        self, temp_cache_dir
    ):
        """An over-budget cache must degrade, not fail the action."""
        cache = RegistryArtifactCache(temp_cache_dir)
        leased = _write_image_entry(temp_cache_dir, "leased", size=4096, mtime=100.0)
        cache._acquire_lease("leased")

        with patch(MAX_ENTRIES_CONFIG, 0), patch(MAX_BYTES_CONFIG, 1):
            within_budget = await cache._enforce_cache_budget(protected_key="missing")

        assert within_budget is False
        assert leased.exists()

    @pytest.mark.anyio
    async def test_eviction_fails_closed_when_mount_inspection_fails(
        self, temp_cache_dir: Path
    ) -> None:
        """Unknown mount state cannot be mistaken for an unmounted entry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("unknown-mount-state")
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        real_lstat = Path.lstat

        def fail_mount_lstat(path: Path):
            if path == paths.squashfs_mount_dir:
                raise PermissionError("mount inspection denied")
            return real_lstat(path)

        with patch.object(Path, "lstat", fail_mount_lstat):
            with pytest.raises(PermissionError, match="mount inspection denied"):
                await cache._evict_entry("unknown-mount-state")

        assert paths.entry_dir.is_dir()
        assert paths.squashfs_image_path.is_file()
        assert paths.squashfs_mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_eviction_unmounts_before_deleting_the_image(self, temp_cache_dir):
        """Unlinking a mounted image would strand an open-file zombie."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("mounted")
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}
        image_present_at_umount: list[bool] = []

        process = AsyncMock()
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        async def mock_umount(*args, **kwargs):
            assert kwargs["start_new_session"] is True
            image_present_at_umount.append(paths.squashfs_image_path.exists())
            mounted.discard(paths.squashfs_mount_dir)
            return process

        with (
            patch(MOUNT_CHECK, lambda path: path in mounted),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ) as create_subprocess_exec,
            patch(
                "tracecat.sandbox.utils.terminate_process_group",
                new_callable=AsyncMock,
            ),
        ):
            evicted = await cache._evict_entry("mounted")

        assert evicted == RegistryArtifactEviction(retired=True, reclaimed=True)
        assert image_present_at_umount == [True]
        assert not paths.squashfs_image_path.exists()
        assert not paths.squashfs_mount_dir.exists()
        create_subprocess_exec.assert_called_once()
        assert create_subprocess_exec.call_args.args == (
            "/sbin/umount",
            str(paths.squashfs_mount_dir),
        )

    @pytest.mark.anyio
    async def test_repeatedly_cancelled_unmount_reaps_before_releasing_key_lock(
        self, temp_cache_dir
    ):
        """Repeated cancellation leaves a consistent entry for next admission."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/cancelled-unmount.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        (paths.squashfs_mount_dir / "module.py").write_text("VALUE = 1")
        mounted = {paths.squashfs_mount_dir}
        blocked_process = _BlockingSubprocess(block_wait=True)
        released_process = AsyncMock()
        released_process.communicate.return_value = (b"", b"")
        released_process.returncode = 0
        unmount_attempts = 0

        async def mock_umount(*args, **kwargs):
            nonlocal unmount_attempts
            assert kwargs["start_new_session"] is True
            unmount_attempts += 1
            if unmount_attempts == 1:
                return blocked_process
            mounted.discard(paths.squashfs_mount_dir)
            return released_process

        with (
            patch(MOUNT_CHECK, lambda path: path in mounted),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch("tracecat.sandbox.utils.os.killpg") as kill_group,
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            await blocked_process.communicate_started.wait()
            eviction.cancel()
            await blocked_process.wait_started.wait()

            eviction.cancel()
            done, _ = await asyncio.wait({eviction}, timeout=0.05)
            assert not done
            blocked_process.release_wait.set()
            with pytest.raises(asyncio.CancelledError):
                await eviction

            assert blocked_process.cleanup_calls == ["kill", "wait"]
            kill_group.assert_called_once_with(
                blocked_process.pid,
                signal.SIGKILL,
            )
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [paths.squashfs_mount_dir]
                assert registry_paths[0].is_dir()
                assert (registry_paths[0] / "module.py").read_text() == "VALUE = 1"

        assert paths.squashfs_image_path.is_file()
        assert paths.squashfs_mount_dir.is_dir()
        assert paths.squashfs_mount_dir not in mounted

    @pytest.mark.anyio
    async def test_cancelled_background_deletion_leaves_a_clean_miss(
        self, temp_cache_dir
    ):
        """Cancellation cannot expose live paths that deletion still owns."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        artifact_uri = "s3://bucket/cancelled-eviction.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        original_target = _write_tarball_entry(temp_cache_dir, cache_key)
        delete_started = threading.Event()
        finish_delete = threading.Event()
        delete_finished = threading.Event()
        doomed: list[Path] = []

        def blocked_delete(path: Path) -> bool:
            doomed.append(path)
            delete_started.set()
            finish_delete.wait(timeout=5)
            deleted = _delete_cache_path(path)
            delete_finished.set()
            return deleted

        async def mock_download(self, ctx, path):
            path.write_bytes(_tarball_payload(size=1))

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=blocked_delete,
            ),
            patch(
                "tracecat.executor.registry_artifacts._tarball_extracted_size",
                return_value=9,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            assert await asyncio.to_thread(delete_started.wait, 1)
            eviction.cancel()
            try:
                await asyncio.sleep(0)
                eviction.cancel()
                await asyncio.sleep(0)
                assert not eviction.done()
                assert not original_target.exists()
                assert (doomed[0] / "tarball").is_dir()
                assert cache._discover_cache_keys() == set()
            finally:
                finish_delete.set()

            with pytest.raises(asyncio.CancelledError):
                await eviction
            assert await asyncio.to_thread(delete_finished.wait, 1)
            assert not doomed[0].exists()

            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [original_target]
                assert (original_target / "module.py").read_text() == "VALUE = 2"

        assert original_target.is_dir()

    @pytest.mark.parametrize("operation", ["budget", "admission"])
    @pytest.mark.anyio
    async def test_cancelled_cleanup_rejoins_workers_before_releasing_locks(
        self, temp_cache_dir: Path, operation: str
    ) -> None:
        """Cache locks outlive repeatedly cancelled cleanup workers."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cleanup_started = threading.Event()
        cleanup_release = threading.Event()
        cleanup_finished = threading.Event()

        def blocking_clear(work_dir: Path) -> bool:
            assert work_dir == cache.trash_dir
            cleanup_started.set()
            cleanup_release.wait(timeout=5)
            cleanup_finished.set()
            return True

        async def run_operation() -> None:
            if operation == "budget":
                await cache._enforce_cache_budget()
            else:
                async with cache._admission_lock:
                    await cache._ensure_cache_capacity(
                        additional_bytes=0,
                        protected_key="pending",
                        max_bytes=1,
                    )

        with (
            patch.object(cache, "_clear_work_dir", side_effect=blocking_clear),
            patch.object(cache, "_retry_failed_startup_cleanup", return_value=True),
        ):
            running = asyncio.create_task(run_operation())
            try:
                assert await asyncio.to_thread(cleanup_started.wait, 1)
                running.cancel()
                await asyncio.sleep(0)
                running.cancel()
                await asyncio.sleep(0)
                assert not running.done()
                assert cache._admission_lock.locked()
            finally:
                cleanup_release.set()

            with pytest.raises(asyncio.CancelledError):
                await running

        assert cleanup_finished.is_set()
        assert not cache._admission_lock.locked()

    @pytest.mark.anyio
    async def test_doomed_eviction_names_are_startup_scratch(self, temp_cache_dir):
        """Every renamed entry root is invisible and reclaimed on startup."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "squashfs-doomed"
        paths = cache._paths_for(cache_key)
        paths.entry_dir.mkdir(parents=True)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        paths.squashfs_extract_dir.mkdir()
        paths.tarball_target_dir.mkdir()

        with patch(
            "tracecat.executor.registry_artifact_storage._delete_cache_path",
            return_value=True,
        ) as delete_cache_path:
            assert await cache._evict_entry(cache_key) == RegistryArtifactEviction(
                retired=True, reclaimed=True
            )

        delete_cache_path.assert_called_once()
        trash_path = delete_cache_path.call_args.args[0]
        assert trash_path.parent == cache.trash_dir
        assert trash_path.exists()
        assert cache._discover_cache_keys() == set()

        cache._sweep_startup_state()

        assert not trash_path.exists()

    @pytest.mark.anyio
    async def test_eviction_skips_entry_when_unmount_fails(self, temp_cache_dir):
        """A failed unmount skips the key instead of forcing a lazy detach."""
        cache = RegistryArtifactCache(temp_cache_dir)
        stuck = cache._paths_for("stuck")
        stuck.entry_dir.mkdir(parents=True)
        stuck.squashfs_image_path.write_bytes(b"squashfs")
        stuck.squashfs_mount_dir.mkdir()
        os.utime(stuck.squashfs_image_path, (100.0, 100.0))
        idle = _write_image_entry(temp_cache_dir, "idle", size=16, mtime=300.0)
        mounted = {stuck.squashfs_mount_dir}

        process = AsyncMock()
        process.communicate.return_value = (b"", b"target is busy")
        process.returncode = 32

        with (
            patch(MOUNT_CHECK, lambda path: path in mounted),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ),
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            await cache._enforce_cache_budget(protected_key="pending")

        assert stuck.squashfs_image_path.exists()
        assert stuck.squashfs_mount_dir.exists()
        assert not idle.exists()

    @pytest.mark.anyio
    async def test_eviction_retires_idle_runtime_state(self, temp_cache_dir):
        """Eviction releases keyed lock metadata once every user is gone."""
        cache = RegistryArtifactCache(temp_cache_dir)
        _write_tarball_entry(temp_cache_dir, "bookkeeping")
        cache._acquire_lease("bookkeeping")
        cache._release_lease("bookkeeping")

        assert await cache._evict_entry("bookkeeping") == RegistryArtifactEviction(
            retired=True, reclaimed=True
        )
        assert "bookkeeping" not in cache._runtime

    @pytest.mark.anyio
    async def test_eviction_skips_busy_key(self, temp_cache_dir):
        """A key another task is materializing is never evicted underneath it."""
        cache = RegistryArtifactCache(temp_cache_dir)
        target_dir = _write_tarball_entry(temp_cache_dir, "busy")
        lock = cache._runtime_for("busy").lock

        async with lock:
            assert await cache._evict_entry("busy") == RegistryArtifactEviction(
                retired=False, reclaimed=False
            )

        assert target_dir.is_dir()


class TestRegistryArtifactCacheStartupSweep:
    """Tests for the startup sweep that reclaims state from a dead process."""

    @pytest.mark.anyio
    async def test_sweep_uses_entry_root_mtime_for_restart_safe_lru(
        self, temp_cache_dir
    ):
        """Startup trimming preserves a touched older tarball-only entry."""
        previous_cache = RegistryArtifactCache(temp_cache_dir)
        old = _write_tarball_entry(temp_cache_dir, "old")
        previous_cache._touch_entry("old")

        old_mtime = previous_cache._paths_for("old").entry_dir.stat().st_mtime
        new = _write_tarball_entry(temp_cache_dir, "new")
        new_entry_dir = previous_cache._paths_for("new").entry_dir
        os.utime(new_entry_dir, (old_mtime - 1, old_mtime - 1))

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            cache = RegistryArtifactCache(temp_cache_dir)
            await cache.ensure_swept()

        assert old.is_dir()
        assert not new.exists()

    @pytest.mark.anyio
    async def test_sweep_tolerates_missing_cache_dir(self, temp_cache_dir):
        """A cache directory that does not exist yet is a no-op."""
        cache_dir = temp_cache_dir / "missing"

        cache = RegistryArtifactCache(cache_dir)
        await cache.ensure_swept()

        assert cache.cache_dir == cache_dir
        assert not cache_dir.exists()

    @pytest.mark.anyio
    async def test_sweep_rejects_symlinked_cache_root(
        self, temp_cache_dir: Path
    ) -> None:
        outside = temp_cache_dir / "outside"
        orphaned = outside / "staging" / "orphaned.tmp"
        orphaned.parent.mkdir(parents=True)
        orphaned.write_bytes(b"keep")
        symlinked_root = temp_cache_dir / "cache-link"
        symlinked_root.symlink_to(outside, target_is_directory=True)
        cache = RegistryArtifactCache(symlinked_root)

        with pytest.raises(OSError, match="Unsafe registry artifact cache directory"):
            await cache.ensure_swept()

        assert orphaned.read_bytes() == b"keep"

    @pytest.mark.anyio
    async def test_sweep_removes_orphaned_work(self, temp_cache_dir):
        """Interrupted work is reclaimed without touching unrelated paths."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned = cache.staging_dir / "abc123.999999.4321.squashfs"
        orphaned.parent.mkdir()
        orphaned.write_bytes(b"partial")
        orphaned_dir = cache.trash_dir / "abc123.999999.4321"
        orphaned_dir.mkdir(parents=True)
        unrelated = temp_cache_dir / "unrelated"
        unrelated.mkdir()
        unrelated_file = unrelated / "keep.txt"
        unrelated_file.write_text("keep")
        entry_dir = _write_tarball_entry(temp_cache_dir, "abc123")

        await cache.ensure_swept()

        assert not orphaned.exists()
        assert not orphaned_dir.exists()
        assert unrelated_file.read_text() == "keep"
        assert entry_dir.is_dir()

    @pytest.mark.anyio
    async def test_sweep_reclaims_exact_legacy_top_level_layout(
        self, temp_cache_dir: Path
    ) -> None:
        """Pre-entries cache paths cannot remain outside byte accounting."""
        cache_key = "0123456789abcdef"
        legacy_image = temp_cache_dir / f"squashfs-{cache_key}.squashfs"
        legacy_image.write_bytes(b"image")
        legacy_extraction = temp_cache_dir / f"tarball-{cache_key}"
        legacy_extraction.mkdir()
        (legacy_extraction / "module.py").write_text("VALUE = 1")
        legacy_staging = temp_cache_dir / f"{cache_key}.123.456.tmp"
        legacy_staging.mkdir()
        unrelated = temp_cache_dir / "squashfs-not-a-cache-key.squashfs"
        unrelated.write_bytes(b"keep")

        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()

        assert not legacy_image.exists()
        assert not legacy_extraction.exists()
        assert not legacy_staging.exists()
        assert unrelated.read_bytes() == b"keep"

    @pytest.mark.anyio
    async def test_sweep_keeps_mounted_dirs(self, temp_cache_dir):
        """A live mountpoint belongs to a running process and must survive."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("abc123")
        paths.entry_dir.mkdir(parents=True)
        mount_dir = paths.squashfs_mount_dir
        mount_dir.mkdir()

        with patch(MOUNT_CHECK, lambda path: path == mount_dir):
            await cache.ensure_swept()

        assert mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_ensure_swept_runs_once(self, temp_cache_dir):
        """Repeated startup-sweep requests invoke the sweep once."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(
            cache,
            "_sweep_startup_state",
            wraps=cache._sweep_startup_state,
        ) as sweep:
            await cache.ensure_swept()
            await cache.ensure_swept()

        assert sweep.call_count == 1

    @pytest.mark.anyio
    async def test_concurrent_ensure_swept_runs_once(self, temp_cache_dir):
        """Concurrent startup-sweep requests invoke the sweep once."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(
            cache,
            "_sweep_startup_state",
            wraps=cache._sweep_startup_state,
        ) as sweep:
            await asyncio.gather(*(cache.ensure_swept() for _ in range(4)))

        assert sweep.call_count == 1

    @pytest.mark.anyio
    async def test_cancelled_waiter_joins_same_startup_sweep(self, temp_cache_dir):
        """A cancelled waiter cannot start a second concurrent startup sweep."""
        cache = RegistryArtifactCache(temp_cache_dir)
        sweep_started = threading.Event()
        sweep_release = threading.Event()
        invocation_count = 0

        def blocking_sweep() -> None:
            nonlocal invocation_count
            invocation_count += 1
            sweep_started.set()
            sweep_release.wait()

        with patch.object(
            cache,
            "_sweep_startup_state",
            side_effect=blocking_sweep,
        ):
            first_waiter = asyncio.create_task(cache.ensure_swept())
            assert await asyncio.to_thread(sweep_started.wait, 1)
            first_waiter.cancel()

            with pytest.raises(asyncio.CancelledError):
                await first_waiter

            second_waiter = asyncio.create_task(cache.ensure_swept())
            await asyncio.sleep(0)
            sweep_release.set()
            await second_waiter

        assert invocation_count == 1
        assert cache._swept is True

    @pytest.mark.anyio
    async def test_lease_triggers_startup_sweep(self, temp_cache_dir):
        """Cache-backed lease admission reclaims scratch before yielding paths."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned_dir = cache.staging_dir / "abc123.999999.4321"
        orphaned_dir.mkdir(parents=True)
        artifact_uri = "s3://bucket/cached.tar.gz"
        _write_tarball_entry(
            temp_cache_dir,
            compute_registry_artifact_cache_key(artifact_uri),
        )

        async with cache.lease([artifact_uri]):
            assert not orphaned_dir.exists()

    @pytest.mark.anyio
    async def test_failed_ensure_swept_retries(self, temp_cache_dir):
        """A failed startup sweep is retried by the next caller."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned_dir = cache.staging_dir / "abc123.999999.4321"
        orphaned_dir.mkdir(parents=True)

        with (
            patch.object(
                cache,
                "_clear_work_dir",
                side_effect=OSError("simulated sweep failure"),
            ),
            pytest.raises(OSError),
        ):
            await cache.ensure_swept()

        assert orphaned_dir.is_dir()

        await cache.ensure_swept()

        assert not orphaned_dir.exists()

    @pytest.mark.parametrize("work_dir_name", ["staging", "trash"])
    def test_work_directory_inspection_errors_propagate(
        self,
        temp_cache_dir,
        work_dir_name: str,
    ):
        """Unreadable work directories must trigger a later cleanup retry."""
        cache = RegistryArtifactCache(temp_cache_dir)
        work_dir = getattr(cache, f"{work_dir_name}_dir")
        work_dir.mkdir()

        with (
            patch.object(Path, "iterdir", side_effect=PermissionError("denied")),
            pytest.raises(PermissionError, match="denied"),
        ):
            cache._clear_work_dir(work_dir)

    @pytest.mark.anyio
    async def test_active_entry_scan_errors_propagate(self, temp_cache_dir):
        """Unreadable active entries cannot be treated as an empty cache."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifacts.os.scandir",
                side_effect=PermissionError("denied"),
            ),
            pytest.raises(PermissionError, match="denied"),
        ):
            await cache._enforce_cache_budget()

    @pytest.mark.anyio
    async def test_failed_trash_cleanup_skips_startup_trimming(self, temp_cache_dir):
        """A failed orphan deletion must not cascade into active retirements."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphan = cache.trash_dir / "orphan"
        orphan.mkdir(parents=True)
        oldest = _write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        newest = _write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=200.0,
        )
        real_delete = _delete_cache_path

        def fail_orphan(path: Path) -> bool:
            if path == orphan:
                return False
            return real_delete(path)

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=fail_orphan,
            ),
        ):
            await cache.ensure_swept()

        assert orphan.is_dir()
        assert oldest.is_file()
        assert newest.is_file()
        assert cache._budget_dirty is True

    @pytest.mark.anyio
    async def test_failed_startup_cleanup_retries_exact_path(self, temp_cache_dir):
        """Failed startup scratch cleanup retries without sweeping live staging."""
        cache = RegistryArtifactCache(temp_cache_dir)
        orphaned = cache.staging_dir / "orphaned.123.456.tmp"
        orphaned.parent.mkdir(parents=True)
        orphaned.write_bytes(b"partial")
        real_delete = _delete_cache_path
        failed_once = False

        def fail_once(path: Path) -> bool:
            nonlocal failed_once
            if path == orphaned and not failed_once:
                failed_once = True
                return False
            return real_delete(path)

        with patch(
            "tracecat.executor.registry_artifact_storage._delete_cache_path",
            side_effect=fail_once,
        ):
            await cache.ensure_swept()
            assert orphaned.is_file()
            assert set(cache._failed_startup_cleanup) == {orphaned}
            assert cache._budget_dirty is True

            assert await cache._enforce_cache_budget() is True

        assert not orphaned.exists()
        assert cache._failed_startup_cleanup == {}

    @pytest.mark.anyio
    async def test_failed_startup_retirement_stays_dirty_and_retries(
        self, temp_cache_dir
    ):
        """A startup rename failure preserves entries for later enforcement."""
        oldest = _write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        newest = _write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=200.0,
        )
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch(
                "tracecat.executor.registry_artifact_storage._move_entry_to_trash",
                side_effect=OSError("rename failed"),
            ),
        ):
            await cache.ensure_swept()

        assert oldest.is_file()
        assert newest.is_file()
        assert cache._budget_dirty is True

        with (
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            await cache._converge_cache_budget()

        assert not oldest.exists()
        assert newest.is_file()
        assert cache._budget_dirty is False

    @pytest.mark.anyio
    async def test_failed_startup_physical_delete_retries_exact_path(
        self, temp_cache_dir
    ):
        """Startup stops retiring entries when bytes were not reclaimed."""
        oldest = _write_image_entry(
            temp_cache_dir,
            "oldest",
            size=16,
            mtime=100.0,
        )
        older = _write_image_entry(
            temp_cache_dir,
            "older",
            size=16,
            mtime=200.0,
        )
        newest = _write_image_entry(
            temp_cache_dir,
            "newest",
            size=16,
            mtime=300.0,
        )
        cache = RegistryArtifactCache(temp_cache_dir)
        snapshot = cache._scan_cache_snapshot()
        max_bytes = snapshot.structural_bytes + snapshot.entries["newest"].size_bytes
        real_delete = _delete_cache_path
        failed_once = False

        def fail_once(path: Path) -> bool:
            nonlocal failed_once
            if path.parent == cache.trash_dir and not failed_once:
                failed_once = True
                return False
            return real_delete(path)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, max_bytes),
            patch(
                "tracecat.executor.registry_artifact_storage._delete_cache_path",
                side_effect=fail_once,
            ),
        ):
            await cache.ensure_swept()
            assert not oldest.exists()
            assert older.is_file()
            assert newest.is_file()
            assert len(tuple(cache.trash_dir.iterdir())) == 1
            assert cache._budget_dirty is True

            await cache._converge_cache_budget()

        assert not older.exists()
        assert newest.is_file()
        assert not any(cache.trash_dir.iterdir())
        assert cache._budget_dirty is False


class TestSquashfsMountPolicy:
    """Tests for per-artifact SquashFS fallback."""

    @pytest.mark.anyio
    async def test_loop_device_exhaustion_isolated_sticky_extraction_fallback(
        self, temp_cache_dir: Path
    ) -> None:
        """Protect the cache's fail-open policy when loop devices are saturated.

        A mount-command failure must extract only the affected cold artifact,
        preserve already-leased mounts, reuse that extraction on later leases,
        and still let unrelated artifacts attempt mounting. This models loop
        exhaustion deterministically without consuming host-global devices.
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        held_uri = "s3://bucket/already-mounted.squashfs"
        saturated_uri = "s3://bucket/no-loop-available.squashfs"
        later_uri = "s3://bucket/later-artifact.squashfs"
        held_key = compute_registry_artifact_cache_key(held_uri)
        saturated_key = compute_registry_artifact_cache_key(saturated_uri)
        later_key = compute_registry_artifact_cache_key(later_uri)
        harness = _SquashfsMountHarness(
            cache,
            failed_mount_keys={saturated_key},
        )

        with (
            patch(MOUNT_CHECK, lambda path: path in harness.mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", harness.mount),
            patch.object(SquashfsArtifact, "extract", harness.extract),
            patch.object(cache, "_unmount", harness.unmount),
        ):
            async with cache.lease([held_uri]) as held_paths:
                held_mount = cache._paths_for(held_key).squashfs_mount_dir
                assert held_paths == [held_mount]

                async with cache.lease([saturated_uri]) as saturated_paths:
                    extracted = cache._paths_for(saturated_key).squashfs_extract_dir
                    assert saturated_paths == [extracted]
                    assert held_mount in harness.mounted
                    assert cache._refcount(held_key) == 1
                    assert harness.unmounts == []

                # The extracted directory is the cache entry's sticky path;
                # freeing a loop elsewhere does not trigger a remount attempt.
                async with cache.lease([saturated_uri]) as cached_paths:
                    assert cached_paths == [extracted]

                async with cache.lease([later_uri]) as later_paths:
                    later_mount = cache._paths_for(later_key).squashfs_mount_dir
                    assert later_paths == [later_mount]
                    assert held_mount in harness.mounted

                assert held_mount in harness.mounted

        assert harness.mount_attempts == [held_key, saturated_key, later_key]
        assert harness.extraction_attempts == [saturated_key]
        assert harness.unmounts == [later_mount, held_mount]
        assert cache._paths_for(saturated_key).squashfs_image_path.is_file()
        assert cache._paths_for(saturated_key).squashfs_extract_dir.is_dir()
        assert cache._refcount(held_key) == 0
        assert cache._refcount(saturated_key) == 0
        assert cache._refcount(later_key) == 0

    @pytest.mark.anyio
    async def test_mount_failure_does_not_disable_later_artifacts(
        self, temp_cache_dir
    ) -> None:
        """One failed mount falls back without changing later mount attempts."""
        cache = RegistryArtifactCache(temp_cache_dir)
        first_ctx = cache._context_for("first")
        second_ctx = cache._context_for("second")
        first = SquashfsArtifact(
            uri="s3://bucket/first.squashfs",
            cache_key="first",
        )
        second = SquashfsArtifact(
            uri="s3://bucket/second.squashfs",
            cache_key="second",
        )
        mount_attempts: list[str] = []

        async def mock_mount(self, ctx, image_path):
            del image_path
            mount_attempts.append(ctx.cache_key)
            if ctx.cache_key == "first":
                raise SquashfsMountCommandError("operation not permitted")
            ctx.paths.squashfs_mount_dir.mkdir(parents=True)
            return ctx.paths.squashfs_mount_dir

        async def mock_extract(self, ctx, image_path):
            del image_path
            ctx.paths.squashfs_extract_dir.mkdir(parents=True)
            return ctx.paths.squashfs_extract_dir

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
        ):
            assert await first.materialize(first_ctx) == [
                first_ctx.paths.squashfs_extract_dir
            ]
            assert await second.materialize(second_ctx) == [
                second_ctx.paths.squashfs_mount_dir
            ]

        assert mount_attempts == ["first", "second"]
