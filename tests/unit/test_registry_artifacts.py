"""Tests for executor registry artifact materialization."""

from __future__ import annotations

import asyncio
import os
import tarfile
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import tracecat_registry

from tracecat.executor.registry_artifacts import (
    SQUASHFS_MOUNT_OPTIONS,
    TEMP_ARTIFACT_PATTERN,
    RegistryArtifactCache,
    RegistryArtifactFormat,
    RegistryArtifactPaths,
    SquashfsArtifact,
    SquashfsMountCommandError,
    TarballArtifact,
    _delete_entry_paths,
    bundled_builtin_registry_uri,
    compute_registry_artifact_cache_key,
)
from tracecat.executor.schemas import ExecutorBackendType
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
BACKEND_CONFIG = (
    "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_BACKEND"
)
RESOLVE_BACKEND = "tracecat.executor.registry_artifacts.resolve_backend_type"


def _write_tarball_entry(cache_dir: Path, cache_key: str) -> Path:
    """Create a materialized tarball cache entry on disk."""
    target_dir = cache_dir / f"tarball-{cache_key}"
    target_dir.mkdir(parents=True)
    (target_dir / "module.py").write_text("VALUE = 1")
    return target_dir


def _write_image_entry(
    cache_dir: Path, cache_key: str, *, size: int, mtime: float
) -> Path:
    """Create a downloaded SquashFS image cache entry with a fixed mtime."""
    image_path = cache_dir / f"squashfs-{cache_key}.squashfs"
    image_path.write_bytes(b"x" * size)
    os.utime(image_path, (mtime, mtime))
    return image_path


class _BlockingSubprocess:
    """Fake subprocess that blocks in communicate until it is cancelled."""

    def __init__(self) -> None:
        self.communicate_started = asyncio.Event()
        self.cleanup_calls: list[str] = []
        self.returncode: int | None = None

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
        return -9


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
        ) -> None:
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
    async def test_materialize_recomputes_candidates_after_lock(self, temp_cache_dir):
        """Test that lock waiters re-check preferred artifact candidates."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "recompute-key"
        cached_path = temp_cache_dir / "cached-squashfs"
        cached_path.mkdir()
        tarball = TarballArtifact(
            uri="s3://bucket/path/site-packages.tar.gz",
            cache_key=cache_key,
        )
        squashfs = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        pre_lock_candidates = [tarball]
        post_lock_candidates = [squashfs, tarball]
        seen_candidates: list[list[RegistryArtifactFormat]] = []

        def fake_first_cached_path(candidates, ctx):
            del ctx
            seen_candidates.append([artifact.format for artifact in candidates])
            if candidates is post_lock_candidates:
                return [cached_path]
            return None

        with (
            patch.object(
                cache,
                "_artifact_candidates",
                new_callable=AsyncMock,
                side_effect=[pre_lock_candidates, post_lock_candidates],
            ) as artifact_candidates,
            patch.object(
                cache,
                "_first_cached_path",
                side_effect=fake_first_cached_path,
            ),
        ):
            result = await cache.materialize(
                cache_key,
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert result == [cached_path]
        assert artifact_candidates.await_count == 2
        assert seen_candidates == [
            [RegistryArtifactFormat.TAR_GZ],
            [RegistryArtifactFormat.SQUASHFS, RegistryArtifactFormat.TAR_GZ],
        ]

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
            result = await cache.materialize(
                "squashfs-key",
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
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        process = AsyncMock()
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        with patch(
            "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ) as create_subprocess_exec:
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
        )

    @pytest.mark.anyio
    async def test_cancelled_mount_kills_and_reaps_subprocess(self, temp_cache_dir):
        """Cancellation cannot leave an orphan mount process after lock release."""
        cache_key = "cancelled-mount"
        cache = RegistryArtifactCache(temp_cache_dir)
        ctx = cache._context_for(cache_key)
        artifact = SquashfsArtifact(
            uri="s3://bucket/path/site-packages.squashfs",
            cache_key=cache_key,
        )
        image_path = ctx.paths.squashfs_image_path
        target_dir = ctx.paths.squashfs_mount_dir
        image_path.write_bytes(b"squashfs")
        target_dir.mkdir()
        process = _BlockingSubprocess()

        with patch(
            "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ):
            mounting = asyncio.create_task(
                artifact._mount_image(image_path, target_dir)
            )
            await process.communicate_started.wait()
            mounting.cancel()

            with pytest.raises(asyncio.CancelledError):
                await mounting

        assert process.cleanup_calls == ["kill", "wait"]
        assert target_dir.is_dir()
        assert not target_dir.is_mount()

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
            result = await cache.materialize(
                "fallback-key",
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name.startswith("unsquashfs-")
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
            result = await cache.materialize(
                "extract-key",
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name.startswith("unsquashfs-")

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
            result = await cache.materialize(
                "gzip-fallback-key",
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert result[0].name.startswith("tarball-")

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
            result = await cache.materialize(
                "custom-key-test",
                "s3://bucket/path/custom-key",
            )

        assert len(result) == 1
        assert (result[0] / "module.py").read_text() == "VALUE = 1"

    @pytest.mark.anyio
    async def test_materialize_caches_result(self, temp_cache_dir):
        """Test that tarball extraction is cached."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "test-cache-key"
        target_dir = temp_cache_dir / f"tarball-{cache_key}"
        target_dir.mkdir(parents=True)

        result = await cache.materialize(cache_key, "s3://bucket/test.tar.gz")

        assert result == [target_dir]

    @pytest.mark.anyio
    async def test_materialize_concurrent_requests(self, temp_cache_dir):
        """Test that concurrent requests for same artifact do not race."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "concurrent-test"
        download_count = 0

        async def mock_download(self, ctx, path):
            nonlocal download_count
            download_count += 1
            await asyncio.sleep(0.1)
            path.write_bytes(b"fake tarball content")

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "extracted.txt").write_text("extracted")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            results = await asyncio.gather(
                cache.materialize(cache_key, "s3://bucket/test.tar.gz"),
                cache.materialize(cache_key, "s3://bucket/test.tar.gz"),
                cache.materialize(cache_key, "s3://bucket/test.tar.gz"),
            )

        assert all(r == results[0] for r in results)
        assert download_count == 1

    @pytest.mark.anyio
    async def test_lock_for_same_key(self, temp_cache_dir):
        """Test that same cache key returns same lock."""
        cache = RegistryArtifactCache(temp_cache_dir)

        lock1 = await cache._lock_for("key1")
        lock2 = await cache._lock_for("key1")

        assert lock1 is lock2

    @pytest.mark.anyio
    async def test_lock_for_different_keys(self, temp_cache_dir):
        """Test that different cache keys return different locks."""
        cache = RegistryArtifactCache(temp_cache_dir)

        lock1 = await cache._lock_for("key1")
        lock2 = await cache._lock_for("key2")

        assert lock1 is not lock2


class TestRegistryArtifactCacheLease:
    """Tests for lease-based pinning of registry artifact cache entries."""

    @pytest.mark.anyio
    async def test_lease_refcounts_and_touches_image_mtime(self, temp_cache_dir):
        """A lease pins its entry and refreshes the restart-safe LRU timestamp."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/leased.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = _write_tarball_entry(temp_cache_dir, cache_key)
        image_path = _write_image_entry(temp_cache_dir, cache_key, size=16, mtime=100.0)

        async with cache.lease([artifact_uri]) as registry_paths:
            assert registry_paths == [target_dir]
            assert cache._refcount(cache_key) == 1
            assert image_path.stat().st_mtime > 100.0

        assert cache._refcount(cache_key) == 0

    @pytest.mark.anyio
    async def test_lease_releases_refcount_when_materialization_fails(
        self, temp_cache_dir
    ):
        """A failed materialization must not leak a permanent pin."""
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

    @pytest.mark.anyio
    async def test_cancelled_lease_admission_releases_refcount(self, temp_cache_dir):
        """Cancellation during candidate lookup must not leak a permanent pin."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        target_dir = _write_tarball_entry(temp_cache_dir, cache_key)
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
        assert await cache._evict_entry(cache_key) is True
        assert not target_dir.exists()

    @pytest.mark.anyio
    async def test_lease_without_uris_returns_base_pythonpath_dir(self, temp_cache_dir):
        """No artifact URIs still yields the base PYTHONPATH directory."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async with cache.lease(None) as registry_paths:
            assert registry_paths == [temp_cache_dir / "base"]
            assert registry_paths[0].is_dir()

        assert cache._leases == {}

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
    async def test_lease_is_never_admitted_across_an_in_flight_eviction(
        self, temp_cache_dir
    ):
        """A lease must not return a mount an in-flight eviction is deleting."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
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
            patch.object(Path, "is_mount", lambda self: self in mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
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

        assert evicted is True
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

        with patch.object(
            cache,
            "_enforce_cache_budget",
            new_callable=AsyncMock,
        ) as enforce_cache_budget:
            async with cache.lease([bundled_builtin_registry_uri(version)]) as paths:
                assert paths == [site_packages.resolve()]
                assert cache._leases == {}

        enforce_cache_budget.assert_not_awaited()


class TestRegistryArtifactCacheEviction:
    """Tests for bounded eviction of registry artifact cache entries."""

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
            path.write_bytes(b"fake tarball")

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(MAX_ENTRIES_CONFIG, 2),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([leased_uri]):
                await cache.materialize(
                    compute_registry_artifact_cache_key(new_uri), new_uri
                )

        assert leased_dir.is_dir()
        assert not idle_dir.exists()

    @pytest.mark.anyio
    async def test_releasing_a_lease_converges_the_cache_to_budget(
        self, temp_cache_dir
    ):
        """Enforcement before materialization cannot see the new entry's size."""
        cache = RegistryArtifactCache(temp_cache_dir)
        idle = _write_image_entry(temp_cache_dir, "idle", size=4096, mtime=100.0)
        new_uri = "s3://bucket/new.tar.gz"
        new_key = compute_registry_artifact_cache_key(new_uri)

        async def mock_download(self, ctx, path):
            path.write_bytes(b"fake tarball")

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_bytes(b"x" * 4096)

        with (
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 6000),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            async with cache.lease([new_uri]) as registry_paths:
                # Both entries fit only because the new one is still leased.
                assert registry_paths == [temp_cache_dir / f"tarball-{new_key}"]
                assert idle.exists()

        assert not idle.exists()
        assert (temp_cache_dir / f"tarball-{new_key}").is_dir()
        assert cache._budget_dirty is False

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
    async def test_failed_materialization_rearms_budget_dirty(self, temp_cache_dir):
        """A failed materialization may leave a canonical image to evict."""
        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()
        assert cache._budget_dirty is False

        artifact_uri = "s3://bucket/path/site-packages.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        artifact = SquashfsArtifact(uri=artifact_uri, cache_key=cache_key)

        async def mock_materialize(self, ctx):
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
        ):
            with pytest.raises(RuntimeError, match="mount failed"):
                await cache.materialize(cache_key, artifact_uri)

        assert cache._paths_for(cache_key).squashfs_image_path.is_file()
        assert cache._budget_dirty is True

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
            path.write_bytes(b"fake tarball")

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
            registry_paths = await cache.materialize(cache_key, artifact_uri)
            materialized.set()
            await convergence

        assert registry_paths == [temp_cache_dir / f"tarball-{cache_key}"]
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
        """Direct-backend size eviction stops once the cache is within budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest = _write_image_entry(temp_cache_dir, "oldest", size=4096, mtime=100.0)
        if oldest_has_tarball:
            _write_tarball_entry(temp_cache_dir, "oldest")
        older = _write_image_entry(temp_cache_dir, "older", size=4096, mtime=200.0)
        newest = _write_image_entry(temp_cache_dir, "newest", size=4096, mtime=300.0)

        with (
            patch(BACKEND_CONFIG, ExecutorBackendType.DIRECT.value),
            patch(MAX_ENTRIES_CONFIG, 0),
            patch(MAX_BYTES_CONFIG, 9000),
        ):
            within_budget = await cache._enforce_cache_budget(protected_key="pending")

        assert within_budget is True
        assert not oldest.exists()
        assert not cache._paths_for("oldest").tarball_target_dir.exists()
        assert older.exists()
        assert newest.exists()

    @pytest.mark.anyio
    async def test_auto_pool_backend_skips_tarball_lru_and_evicts_next_entry(
        self, temp_cache_dir
    ):
        """Auto-resolved pool workers protect tarballs from runtime eviction."""
        cache = RegistryArtifactCache(temp_cache_dir)
        pool_visible_image = _write_image_entry(
            temp_cache_dir, "pool-visible", size=16, mtime=100.0
        )
        pool_visible_tarball = _write_tarball_entry(temp_cache_dir, "pool-visible")
        next_lru = _write_image_entry(temp_cache_dir, "next-lru", size=16, mtime=200.0)
        newest = _write_image_entry(temp_cache_dir, "newest", size=16, mtime=300.0)

        with (
            patch(BACKEND_CONFIG, ExecutorBackendType.AUTO.value),
            patch(RESOLVE_BACKEND, return_value=ExecutorBackendType.POOL),
            patch(MAX_ENTRIES_CONFIG, 2),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            within_budget = await cache._enforce_cache_budget()

        assert within_budget is True
        assert pool_visible_image.exists()
        assert pool_visible_tarball.is_dir()
        assert not next_lru.exists()
        assert newest.exists()

    @pytest.mark.anyio
    async def test_auto_non_pool_backend_evicts_tarball_lru(self, temp_cache_dir):
        """Auto-resolved non-pool backends may evict tarball entries normally."""
        cache = RegistryArtifactCache(temp_cache_dir)
        oldest_image = _write_image_entry(
            temp_cache_dir, "oldest", size=16, mtime=100.0
        )
        oldest_tarball = _write_tarball_entry(temp_cache_dir, "oldest")
        newest = _write_image_entry(temp_cache_dir, "newest", size=16, mtime=200.0)

        with (
            patch(BACKEND_CONFIG, ExecutorBackendType.AUTO.value),
            patch(RESOLVE_BACKEND, return_value=ExecutorBackendType.DIRECT),
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            within_budget = await cache._enforce_cache_budget()

        assert within_budget is True
        assert not oldest_image.exists()
        assert not oldest_tarball.exists()
        assert newest.exists()

    @pytest.mark.anyio
    async def test_pool_backend_all_tarballs_remain_dirty_when_over_budget(
        self, temp_cache_dir
    ):
        """An all-tarball pool cache warns and retries convergence later."""
        cache = RegistryArtifactCache(temp_cache_dir)
        first_image = _write_image_entry(temp_cache_dir, "first", size=16, mtime=100.0)
        first_tarball = _write_tarball_entry(temp_cache_dir, "first")
        second_image = _write_image_entry(
            temp_cache_dir, "second", size=16, mtime=200.0
        )
        second_tarball = _write_tarball_entry(temp_cache_dir, "second")
        cache._budget_dirty = True

        with (
            patch(BACKEND_CONFIG, ExecutorBackendType.POOL.value),
            patch(MAX_ENTRIES_CONFIG, 1),
            patch(MAX_BYTES_CONFIG, 0),
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            await cache._converge_cache_budget()

        assert first_image.exists()
        assert first_tarball.is_dir()
        assert second_image.exists()
        assert second_tarball.is_dir()
        assert cache._budget_dirty is True
        warning.assert_called_once_with(
            "Registry artifact cache is over budget but every entry is in use",
            cache_dir=str(temp_cache_dir),
            entries=2,
            max_entries=1,
            total_bytes=50,
            max_bytes=0,
        )

    @pytest.mark.anyio
    async def test_pool_backend_release_mounted_slot_skips_tarball_entry(
        self, temp_cache_dir
    ):
        """Loop-device recovery must preserve paths visible to warm workers."""
        cache = RegistryArtifactCache(temp_cache_dir)
        pool_visible = cache._paths_for("pool-visible")
        pool_visible.squashfs_image_path.write_bytes(b"squashfs")
        os.utime(pool_visible.squashfs_image_path, (100.0, 100.0))
        pool_visible.squashfs_mount_dir.mkdir()
        _write_tarball_entry(temp_cache_dir, "pool-visible")
        eligible = cache._paths_for("eligible")
        eligible.squashfs_image_path.write_bytes(b"squashfs")
        os.utime(eligible.squashfs_image_path, (200.0, 200.0))
        eligible.squashfs_mount_dir.mkdir()
        mounted = {
            pool_visible.squashfs_mount_dir,
            eligible.squashfs_mount_dir,
        }

        with (
            patch(BACKEND_CONFIG, ExecutorBackendType.POOL.value),
            patch.object(Path, "is_mount", lambda self: self in mounted),
            patch.object(
                cache,
                "_evict_entry",
                new_callable=AsyncMock,
                return_value=True,
            ) as evict_entry,
        ):
            released = await cache._release_mounted_slot("protected")

        assert released is True
        evict_entry.assert_awaited_once_with("eligible")

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

        def controlled_scan():
            nonlocal scan_count
            entries = original_scan()
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

        async def controlled_evict(cache_key: str) -> bool:
            if cache_key == "oldest":
                if eviction_started.is_set():
                    return False
                eviction_started.set()
                await finish_eviction.wait()
                oldest.unlink(missing_ok=True)
            else:
                retained.unlink(missing_ok=True)
                extra_eviction_finished.set()
            evicted_keys.append(cache_key)
            return True

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
    async def test_enforce_budget_counts_the_pending_entry(self, temp_cache_dir):
        """The entry about to be materialized counts against the entry budget."""
        cache = RegistryArtifactCache(temp_cache_dir)
        existing = _write_image_entry(temp_cache_dir, "existing", size=16, mtime=100.0)

        with patch(MAX_ENTRIES_CONFIG, 1), patch(MAX_BYTES_CONFIG, 0):
            await cache._enforce_cache_budget(protected_key="pending")

        assert not existing.exists()

    @pytest.mark.anyio
    async def test_enforce_budget_never_evicts_the_protected_key(self, temp_cache_dir):
        """The key being materialized is exempt even when it is the LRU entry."""
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

        with patch(MAX_ENTRIES_CONFIG, 1), patch(MAX_BYTES_CONFIG, 0):
            await cache._enforce_cache_budget(protected_key="pending")

        assert leased.exists()

    @pytest.mark.anyio
    async def test_eviction_unmounts_before_deleting_the_image(self, temp_cache_dir):
        """Unlinking a mounted image would strand an open-file zombie."""
        cache = RegistryArtifactCache(temp_cache_dir)
        paths = cache._paths_for("mounted")
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        mounted = {paths.squashfs_mount_dir}
        image_present_at_umount: list[bool] = []

        process = AsyncMock()
        process.communicate.return_value = (b"", b"")
        process.returncode = 0

        async def mock_umount(*args, **kwargs):
            image_present_at_umount.append(paths.squashfs_image_path.exists())
            mounted.discard(paths.squashfs_mount_dir)
            return process

        with (
            patch.object(Path, "is_mount", lambda self: self in mounted),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ) as create_subprocess_exec,
        ):
            evicted = await cache._evict_entry("mounted")

        assert evicted is True
        assert image_present_at_umount == [True]
        assert not paths.squashfs_image_path.exists()
        assert not paths.squashfs_mount_dir.exists()
        create_subprocess_exec.assert_called_once()
        assert create_subprocess_exec.call_args.args == (
            "/sbin/umount",
            str(paths.squashfs_mount_dir),
        )

    @pytest.mark.anyio
    async def test_cancelled_unmount_kills_and_reaps_before_releasing_key_lock(
        self, temp_cache_dir
    ):
        """Cancellation leaves a consistent entry for the next admission."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/cancelled-unmount.squashfs"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        paths = cache._paths_for(cache_key)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        (paths.squashfs_mount_dir / "module.py").write_text("VALUE = 1")
        mounted = {paths.squashfs_mount_dir}
        process = _BlockingSubprocess()

        with (
            patch.object(Path, "is_mount", lambda self: self in mounted),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ),
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            await process.communicate_started.wait()
            eviction.cancel()

            with pytest.raises(asyncio.CancelledError):
                await eviction

            assert process.cleanup_calls == ["kill", "wait"]
            async with cache.lease([artifact_uri]) as registry_paths:
                assert registry_paths == [paths.squashfs_mount_dir]
                assert registry_paths[0].is_dir()
                assert (registry_paths[0] / "module.py").read_text() == "VALUE = 1"

        assert paths.squashfs_image_path.is_file()
        assert paths.squashfs_mount_dir.is_dir()

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
        doomed: list[RegistryArtifactPaths] = []

        def blocked_delete(paths: RegistryArtifactPaths) -> None:
            doomed.append(paths)
            delete_started.set()
            finish_delete.wait(timeout=5)
            _delete_entry_paths(paths)
            delete_finished.set()

        async def mock_download(self, ctx, path):
            path.write_bytes(b"fake tarball")

        async def mock_extract(self, tarball_path, target_dir):
            (target_dir / "module.py").write_text("VALUE = 2")

        with (
            patch(
                "tracecat.executor.registry_artifacts._delete_entry_paths",
                side_effect=blocked_delete,
            ),
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            eviction = asyncio.create_task(cache._evict_entry(cache_key))
            assert await asyncio.to_thread(delete_started.wait, 1)
            eviction.cancel()
            with pytest.raises(asyncio.CancelledError):
                await eviction

            try:
                assert not original_target.exists()
                assert doomed[0].tarball_target_dir.is_dir()
                assert cache._discover_cache_keys() == set()

                async with cache.lease([artifact_uri]) as registry_paths:
                    assert registry_paths == [original_target]
                    assert original_target.is_dir()
                    assert doomed[0].tarball_target_dir.is_dir()
                    assert (original_target / "module.py").read_text() == "VALUE = 2"
            finally:
                finish_delete.set()

        assert await asyncio.to_thread(delete_finished.wait, 1)
        assert original_target.is_dir()
        assert not doomed[0].tarball_target_dir.exists()

    @pytest.mark.anyio
    async def test_doomed_eviction_names_are_startup_scratch(self, temp_cache_dir):
        """Every renamed entry root is invisible and reclaimed on startup."""
        cache = RegistryArtifactCache(temp_cache_dir)
        # This key makes doomed names look like live image paths unless cache
        # discovery rejects the shared scratch pattern first.
        cache_key = "squashfs-doomed"
        paths = cache._paths_for(cache_key)
        paths.squashfs_image_path.write_bytes(b"squashfs")
        paths.squashfs_mount_dir.mkdir()
        paths.squashfs_extract_dir.mkdir()
        paths.tarball_target_dir.mkdir()

        with patch(
            "tracecat.executor.registry_artifacts._delete_entry_paths"
        ) as delete_entry_paths:
            assert await cache._evict_entry(cache_key) is True

        doomed_paths = delete_entry_paths.call_args.args[0]
        renamed = (
            doomed_paths.squashfs_image_path,
            doomed_paths.squashfs_mount_dir,
            doomed_paths.squashfs_extract_dir,
            doomed_paths.tarball_target_dir,
        )
        assert all(path.exists() for path in renamed)
        assert all(TEMP_ARTIFACT_PATTERN.fullmatch(path.name) for path in renamed)
        assert cache._discover_cache_keys() == set()

        cache._sweep_startup_state()

        assert not any(path.exists() for path in renamed)

    @pytest.mark.anyio
    async def test_eviction_skips_entry_when_unmount_fails(self, temp_cache_dir):
        """A failed unmount skips the key instead of forcing a lazy detach."""
        cache = RegistryArtifactCache(temp_cache_dir)
        stuck = cache._paths_for("stuck")
        stuck.squashfs_image_path.write_bytes(b"squashfs")
        stuck.squashfs_mount_dir.mkdir()
        os.utime(stuck.squashfs_image_path, (100.0, 100.0))
        idle = _write_image_entry(temp_cache_dir, "idle", size=16, mtime=300.0)
        mounted = {stuck.squashfs_mount_dir}

        process = AsyncMock()
        process.communicate.return_value = (b"", b"target is busy")
        process.returncode = 32

        with (
            patch.object(Path, "is_mount", lambda self: self in mounted),
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
    async def test_eviction_drops_lease_records_but_keeps_the_key_lock(
        self, temp_cache_dir
    ):
        """Locks are stable for the process lifetime; lease records are not."""
        cache = RegistryArtifactCache(temp_cache_dir)
        _write_tarball_entry(temp_cache_dir, "bookkeeping")
        cache._acquire_lease("bookkeeping")
        cache._release_lease("bookkeeping")
        lock = await cache._lock_for("bookkeeping")

        assert await cache._evict_entry("bookkeeping") is True
        assert cache._locks["bookkeeping"] is lock
        assert "bookkeeping" not in cache._leases

    @pytest.mark.anyio
    async def test_eviction_skips_busy_key(self, temp_cache_dir):
        """A key another task is materializing is never evicted underneath it."""
        cache = RegistryArtifactCache(temp_cache_dir)
        target_dir = _write_tarball_entry(temp_cache_dir, "busy")
        lock = await cache._lock_for("busy")

        async with lock:
            assert await cache._evict_entry("busy") is False

        assert target_dir.is_dir()


class TestRegistryArtifactCacheStartupSweep:
    """Tests for the startup sweep that reclaims state from a dead process."""

    @pytest.mark.anyio
    async def test_sweep_tolerates_missing_cache_dir(self, temp_cache_dir):
        """A cache directory that does not exist yet is a no-op."""
        cache_dir = temp_cache_dir / "missing"

        cache = RegistryArtifactCache(cache_dir)
        await cache.ensure_swept()

        assert cache.cache_dir == cache_dir
        assert not cache_dir.exists()

    @pytest.mark.anyio
    async def test_sweep_removes_orphaned_scratch_and_stale_mount_dirs(
        self, temp_cache_dir
    ):
        """Interrupted materializations and dead mount dirs are reclaimed."""
        orphaned = temp_cache_dir / "abc123.999999.4321.squashfs"
        orphaned.write_bytes(b"partial")
        orphaned_dir = temp_cache_dir / "abc123.999999.4321.tmp"
        orphaned_dir.mkdir()
        own = temp_cache_dir / f"abc123.{os.getpid()}.4321.tar.gz"
        own.write_bytes(b"in flight")
        stale_mount_dir = temp_cache_dir / "squashfs-abc123"
        stale_mount_dir.mkdir()
        entry_dir = _write_tarball_entry(temp_cache_dir, "abc123")

        cache = RegistryArtifactCache(temp_cache_dir)
        await cache.ensure_swept()

        assert not orphaned.exists()
        assert not orphaned_dir.exists()
        assert not own.exists()
        assert not stale_mount_dir.exists()
        assert entry_dir.is_dir()

    @pytest.mark.anyio
    async def test_sweep_keeps_mounted_dirs(self, temp_cache_dir):
        """A live mountpoint belongs to a running process and must survive."""
        mount_dir = temp_cache_dir / "squashfs-abc123"
        mount_dir.mkdir()

        with patch.object(Path, "is_mount", lambda self: self == mount_dir):
            cache = RegistryArtifactCache(temp_cache_dir)
            await cache.ensure_swept()

        assert mount_dir.is_dir()

    @pytest.mark.anyio
    async def test_auto_pool_sweep_protects_tarballs_and_trims_other_entries(
        self, temp_cache_dir
    ):
        """Startup trimming preserves paths inherited by auto-resolved workers."""
        oldest = _write_image_entry(temp_cache_dir, "oldest", size=64, mtime=100.0)
        oldest_tarball = _write_tarball_entry(temp_cache_dir, "oldest")
        older = _write_image_entry(temp_cache_dir, "older", size=64, mtime=200.0)
        newest = _write_image_entry(temp_cache_dir, "newest", size=64, mtime=300.0)

        with (
            patch(BACKEND_CONFIG, ExecutorBackendType.AUTO.value),
            patch(RESOLVE_BACKEND, return_value=ExecutorBackendType.POOL),
            patch(MAX_ENTRIES_CONFIG, 2),
            patch(MAX_BYTES_CONFIG, 0),
        ):
            cache = RegistryArtifactCache(temp_cache_dir)
            await cache.ensure_swept()

        assert oldest.exists()
        assert oldest_tarball.is_dir()
        assert not older.exists()
        assert newest.exists()
        assert cache._budget_dirty is False

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
    async def test_lease_triggers_startup_sweep(self, temp_cache_dir):
        """Lease admission reclaims startup scratch before yielding paths."""
        orphaned_dir = temp_cache_dir / "abc123.999999.4321.tmp"
        orphaned_dir.mkdir()
        assert TEMP_ARTIFACT_PATTERN.match(orphaned_dir.name) is not None
        cache = RegistryArtifactCache(temp_cache_dir)

        async with cache.lease(None):
            assert not orphaned_dir.exists()

    @pytest.mark.anyio
    async def test_failed_ensure_swept_retries(self, temp_cache_dir):
        """A failed startup sweep is retried by the next caller."""
        orphaned_dir = temp_cache_dir / "abc123.999999.4321.tmp"
        orphaned_dir.mkdir()
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch.object(
                cache,
                "_sweep_startup_state",
                side_effect=OSError("simulated sweep failure"),
            ),
            pytest.raises(OSError),
        ):
            await cache.ensure_swept()

        assert orphaned_dir.is_dir()

        await cache.ensure_swept()

        assert not orphaned_dir.exists()


class TestSquashfsMountCapability:
    """Tests for process-wide SquashFS mount capability tracking."""

    @pytest.mark.anyio
    async def test_concurrent_first_mount_failure_serializes_capability_probe(
        self, temp_cache_dir
    ) -> None:
        """A failed first probe disables a waiter without racing its mount."""
        cache = RegistryArtifactCache(temp_cache_dir)
        first_ctx = cache._context_for("first-probe")
        second_ctx = cache._context_for("second-probe")
        assert first_ctx.squashfs_mount_state is second_ctx.squashfs_mount_state
        first_ctx.paths.squashfs_image_path.write_bytes(b"squashfs")
        second_ctx.paths.squashfs_image_path.write_bytes(b"squashfs")
        first_artifact = SquashfsArtifact(
            uri="s3://bucket/path/first.squashfs",
            cache_key=first_ctx.cache_key,
        )
        second_artifact = SquashfsArtifact(
            uri="s3://bucket/path/second.squashfs",
            cache_key=second_ctx.cache_key,
        )
        first_mount_started = asyncio.Event()
        release_first_mount = asyncio.Event()
        mount_attempts: list[Path] = []

        async def mock_mount_image(self, image_path, target_dir):
            mount_attempts.append(target_dir)
            if target_dir == first_ctx.paths.squashfs_mount_dir:
                first_mount_started.set()
                await release_first_mount.wait()
                raise SquashfsMountCommandError("operation not permitted")

        with patch.object(SquashfsArtifact, "_mount_image", mock_mount_image):
            first_mount = asyncio.create_task(
                first_artifact._try_mount(
                    first_ctx,
                    first_ctx.paths.squashfs_image_path,
                )
            )
            await first_mount_started.wait()
            second_mount = asyncio.create_task(
                second_artifact._try_mount(
                    second_ctx,
                    second_ctx.paths.squashfs_image_path,
                )
            )
            await asyncio.sleep(0)

            assert first_ctx.squashfs_mount_state.probe_lock.locked()
            assert not second_mount.done()
            assert mount_attempts == [first_ctx.paths.squashfs_mount_dir]

            release_first_mount.set()
            first_result, second_result = await asyncio.gather(
                first_mount,
                second_mount,
            )

        state = first_ctx.squashfs_mount_state
        assert first_result is None
        assert second_result is None
        assert state.disabled is True
        assert state.mounted_once is False
        assert not (state.disabled and state.mounted_once)
        assert mount_attempts == [first_ctx.paths.squashfs_mount_dir]

    @pytest.mark.anyio
    async def test_concurrent_probe_waiter_mounts_after_first_success(
        self, temp_cache_dir
    ) -> None:
        """A waiter mounts after the successful probe releases serialization."""
        cache = RegistryArtifactCache(temp_cache_dir)
        first_ctx = cache._context_for("first-success")
        second_ctx = cache._context_for("second-success")
        assert first_ctx.squashfs_mount_state is second_ctx.squashfs_mount_state
        first_ctx.paths.squashfs_image_path.write_bytes(b"squashfs")
        second_ctx.paths.squashfs_image_path.write_bytes(b"squashfs")
        first_artifact = SquashfsArtifact(
            uri="s3://bucket/path/first.squashfs",
            cache_key=first_ctx.cache_key,
        )
        second_artifact = SquashfsArtifact(
            uri="s3://bucket/path/second.squashfs",
            cache_key=second_ctx.cache_key,
        )
        first_mount_started = asyncio.Event()
        release_first_mount = asyncio.Event()
        mount_attempts: list[Path] = []

        async def mock_mount_image(self, image_path, target_dir):
            mount_attempts.append(target_dir)
            if target_dir == first_ctx.paths.squashfs_mount_dir:
                first_mount_started.set()
                await release_first_mount.wait()

        with patch.object(SquashfsArtifact, "_mount_image", mock_mount_image):
            first_mount = asyncio.create_task(
                first_artifact._try_mount(
                    first_ctx,
                    first_ctx.paths.squashfs_image_path,
                )
            )
            await first_mount_started.wait()
            second_mount = asyncio.create_task(
                second_artifact._try_mount(
                    second_ctx,
                    second_ctx.paths.squashfs_image_path,
                )
            )
            await asyncio.sleep(0)

            assert first_ctx.squashfs_mount_state.probe_lock.locked()
            assert not second_mount.done()
            assert mount_attempts == [first_ctx.paths.squashfs_mount_dir]

            release_first_mount.set()
            first_result, second_result = await asyncio.gather(
                first_mount,
                second_mount,
            )

        state = first_ctx.squashfs_mount_state
        assert first_result == first_ctx.paths.squashfs_mount_dir
        assert second_result == second_ctx.paths.squashfs_mount_dir
        assert state.disabled is False
        assert state.mounted_once is True
        assert mount_attempts == [
            first_ctx.paths.squashfs_mount_dir,
            second_ctx.paths.squashfs_mount_dir,
        ]

    @pytest.mark.anyio
    async def test_first_mount_failure_disables_squashfs_process_wide(
        self, temp_cache_dir
    ):
        """With no prior success the failure is a capability probe."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_mount(self, ctx, image_path):
            raise SquashfsMountCommandError("operation not permitted")

        async def mock_extract(self, ctx, image_path):
            target_dir = ctx.paths.squashfs_extract_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
            patch.object(
                cache,
                "_release_mounted_slot",
                new_callable=AsyncMock,
                return_value=False,
            ) as release_mounted_slot,
        ):
            await cache.materialize(
                "probe-key", "s3://bucket/path/site-packages.squashfs"
            )

        assert cache._squashfs_mount_state.disabled is True
        release_mounted_slot.assert_not_awaited()

    @pytest.mark.anyio
    async def test_mount_failure_after_success_reclaims_loop_device_and_retries(
        self, temp_cache_dir
    ):
        """Loop-device exhaustion evicts an idle mount instead of going sticky."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache._squashfs_mount_state.mounted_once = True
        idle = cache._paths_for("idle")
        idle.squashfs_image_path.write_bytes(b"squashfs")
        idle.squashfs_mount_dir.mkdir()
        mounted = {idle.squashfs_mount_dir}
        attempts: list[str] = []

        async def mock_mount(self, ctx, image_path):
            attempts.append(ctx.cache_key)
            if mounted:
                raise SquashfsMountCommandError("failed to setup loop device")
            target_dir = ctx.paths.squashfs_mount_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            mounted.add(target_dir)
            return target_dir

        umount_process = AsyncMock()
        umount_process.communicate.return_value = (b"", b"")
        umount_process.returncode = 0

        async def mock_umount(*args, **kwargs):
            mounted.discard(idle.squashfs_mount_dir)
            return umount_process

        with (
            patch.object(Path, "is_mount", lambda self: self in mounted),
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/umount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.asyncio.create_subprocess_exec",
                side_effect=mock_umount,
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
        ):
            result = await cache.materialize(
                "new", "s3://bucket/path/site-packages.squashfs"
            )

        assert result == [cache._paths_for("new").squashfs_mount_dir]
        assert (result[0] / "module.py").read_text() == "VALUE = 1"
        assert attempts == ["new", "new"]
        assert cache._squashfs_mount_state.disabled is False
        assert not idle.squashfs_image_path.exists()
        assert not idle.squashfs_mount_dir.exists()

    @pytest.mark.anyio
    async def test_download_failure_does_not_disable_squashfs_process_wide(
        self, temp_cache_dir
    ):
        """A transient download error is not a missing mount capability."""
        cache = RegistryArtifactCache(temp_cache_dir)
        tarball_dir = temp_cache_dir / "gzip-fallback"
        tarball_dir.mkdir()

        async def mock_download(self, ctx, image_path):
            raise RuntimeError("connection reset by peer")

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "download", mock_download),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
                return_value=[tarball_dir],
            ) as tarball_materialize,
            patch.object(
                cache,
                "_release_mounted_slot",
                new_callable=AsyncMock,
                return_value=False,
            ) as release_mounted_slot,
        ):
            result = await cache.materialize(
                "download-failure", "s3://bucket/path/site-packages.squashfs"
            )

        assert result == [tarball_dir]
        assert cache._squashfs_mount_state.disabled is False
        tarball_materialize.assert_awaited_once()
        release_mounted_slot.assert_not_awaited()

    @pytest.mark.anyio
    async def test_download_failure_after_a_mount_success_does_not_reclaim_a_slot(
        self, temp_cache_dir
    ):
        """A transient download error must not evict an unrelated idle mount."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache._squashfs_mount_state.mounted_once = True
        tarball_dir = temp_cache_dir / "gzip-fallback"
        tarball_dir.mkdir()

        async def mock_download(self, ctx, image_path):
            raise RuntimeError("connection reset by peer")

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "download", mock_download),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
                return_value=[tarball_dir],
            ),
            patch.object(
                cache,
                "_release_mounted_slot",
                new_callable=AsyncMock,
                return_value=True,
            ) as release_mounted_slot,
        ):
            result = await cache.materialize(
                "download-failure", "s3://bucket/path/site-packages.squashfs"
            )

        assert result == [tarball_dir]
        assert cache._squashfs_mount_state.disabled is False
        release_mounted_slot.assert_not_awaited()

    @pytest.mark.anyio
    async def test_mount_failure_without_reclaimable_slot_falls_back_to_extraction(
        self, temp_cache_dir
    ):
        """Only this artifact degrades when no idle mount can be reclaimed."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache._squashfs_mount_state.mounted_once = True

        async def mock_mount(self, ctx, image_path):
            raise SquashfsMountCommandError("failed to setup loop device")

        async def mock_extract(self, ctx, image_path):
            target_dir = ctx.paths.squashfs_extract_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")
            return target_dir

        with (
            patch(SQUASHFS_ENABLED_CONFIG, True),
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/sbin/mount",
            ),
            patch.object(SquashfsArtifact, "mount", mock_mount),
            patch.object(SquashfsArtifact, "extract", mock_extract),
        ):
            result = await cache.materialize(
                "no-slot", "s3://bucket/path/site-packages.squashfs"
            )

        assert result[0].name.startswith("unsquashfs-")
        assert cache._squashfs_mount_state.disabled is False
