"""Tests for executor registry artifact materialization."""

from __future__ import annotations

import asyncio
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tracecat.executor.registry_artifacts import (
    SQUASHFS_MOUNT_OPTIONS,
    RegistryArtifactCache,
    RegistryArtifactFormat,
    compute_registry_artifact_cache_key,
    parse_s3_uri,
)


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
        output_path = temp_cache_dir / "artifact.squashfs"

        with patch(
            "tracecat.executor.registry_artifacts.blob.download_file_to_path",
            new_callable=AsyncMock,
            return_value=123,
        ) as download_file_to_path:
            await cache._download_artifact(
                "s3://bucket/path/site-packages.squashfs",
                output_path,
            )

        download_file_to_path.assert_awaited_once_with(
            key="path/site-packages.squashfs",
            bucket="bucket",
            output_path=output_path,
        )

    @pytest.mark.anyio
    async def test_download_artifact_normalizes_missing_objects_to_http_404(
        self, temp_cache_dir
    ):
        """Preserve the missing-artifact error contract from presigned downloads."""
        cache = RegistryArtifactCache(temp_cache_dir)
        output_path = temp_cache_dir / "artifact.tar.gz"

        with patch(
            "tracecat.executor.registry_artifacts.blob.download_file_to_path",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await cache._download_artifact(
                    "s3://bucket/path/site-packages.tar.gz",
                    output_path,
                )

        assert exc_info.value.response.status_code == 404
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    @pytest.mark.anyio
    async def test_resolve_preferred_artifact_uses_squashfs_sidecar(
        self, temp_cache_dir
    ):
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
            artifact = await cache._resolve_preferred_artifact(
                cache_key, "s3://bucket/path/site-packages.tar.gz"
            )

        assert artifact.uri == "s3://bucket/path/site-packages.squashfs"
        assert artifact.format == RegistryArtifactFormat.SQUASHFS
        file_exists.assert_awaited_once_with(
            key="path/site-packages.squashfs",
            bucket="bucket",
        )

    @pytest.mark.anyio
    async def test_resolve_preferred_artifact_uses_seeded_squashfs_cache(
        self, temp_cache_dir, monkeypatch
    ):
        """Seeded SquashFS cache images should avoid blob lookups."""
        root = temp_cache_dir / "prebuilt"
        key = "platform/tarball-venvs/tracecat_registry/1.2.3"
        local_squashfs = root / key / "site-packages.squashfs"
        local_squashfs.parent.mkdir(parents=True)
        local_squashfs.write_bytes(b"squashfs")

        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
            str(root),
        )
        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY",
            "bucket",
        )
        cache = RegistryArtifactCache(temp_cache_dir)
        tarball_uri = f"s3://bucket/{key}/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(tarball_uri)
        seeded_image = cache._squashfs_image_path(cache_key)

        assert seeded_image.is_symlink()
        assert seeded_image.resolve() == local_squashfs.resolve()

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
            ) as file_exists,
            patch.object(cache, "_can_try_squashfs", return_value=True),
        ):
            artifact = await cache._resolve_preferred_artifact(cache_key, tarball_uri)

        assert artifact.uri == f"s3://bucket/{key}/site-packages.squashfs"
        assert artifact.format == RegistryArtifactFormat.SQUASHFS
        file_exists.assert_not_awaited()

    @pytest.mark.anyio
    async def test_resolve_preferred_artifact_squashfs_fallback_uses_gzip(
        self, temp_cache_dir
    ):
        """Test direct SquashFS URIs fall back to sibling gzip tarballs."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch.object(cache, "_can_try_squashfs") as can_try_squashfs:
            cache_key = compute_registry_artifact_cache_key(
                "s3://bucket/path/site-packages.squashfs"
            )
            artifact = await cache._resolve_preferred_artifact(
                cache_key,
                "s3://bucket/path/site-packages.squashfs",
                allow_squashfs=False,
            )

        assert artifact.uri == "s3://bucket/path/site-packages.tar.gz"
        assert artifact.format == RegistryArtifactFormat.TAR_GZ
        can_try_squashfs.assert_not_called()

    @pytest.mark.anyio
    async def test_resolve_preferred_artifact_falls_back_to_gzip(self, temp_cache_dir):
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
            artifact = await cache._resolve_preferred_artifact(
                cache_key, "s3://bucket/path/site-packages.tar.gz"
            )

        assert artifact.uri == "s3://bucket/path/site-packages.tar.gz"
        assert artifact.format == RegistryArtifactFormat.TAR_GZ

    def test_can_try_squashfs_does_not_require_preloaded_filesystem(
        self, temp_cache_dir
    ):
        """Attempt SquashFS even when the kernel module is not registered yet."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifacts.shutil.which",
                return_value="/usr/bin/mount",
            ),
            patch(
                "tracecat.executor.registry_artifacts.config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                True,
            ),
            patch("tracecat.executor.registry_artifacts.Path") as path_cls,
        ):
            filesystems_path = path_cls.return_value
            filesystems_path.exists.return_value = True
            filesystems_path.read_text.return_value = "nodev\tproc\nnodev\ttmpfs\n"

            assert cache._can_try_squashfs() is True

        filesystems_path.read_text.assert_not_called()

    @pytest.mark.anyio
    async def test_resolve_preferred_artifact_skips_non_registry_tarballs(
        self, temp_cache_dir
    ):
        """Test that arbitrary gzip tarballs do not trigger sidecar lookups."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with patch(
            "tracecat.executor.registry_artifacts.blob.file_exists",
            new_callable=AsyncMock,
        ) as file_exists:
            cache_key = compute_registry_artifact_cache_key(
                "s3://bucket/path/custom.tar.gz"
            )
            artifact = await cache._resolve_preferred_artifact(
                cache_key, "s3://bucket/path/custom.tar.gz"
            )

        assert artifact.uri == "s3://bucket/path/custom.tar.gz"
        assert artifact.format == RegistryArtifactFormat.TAR_GZ
        file_exists.assert_not_awaited()

    @pytest.mark.anyio
    async def test_materialize_mounts_squashfs_sidecar(self, temp_cache_dir):
        """Test that a SquashFS sidecar is mounted instead of extracting tarballs."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_download(_uri, path):
            assert path.name.endswith(".squashfs")
            path.write_bytes(b"squashfs")

        async def mock_mount(_image_path, target_dir):
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch.object(cache, "_can_try_squashfs", return_value=True),
            patch.object(cache, "_download_artifact", mock_download),
            patch.object(cache, "_mount_squashfs", mock_mount),
            patch.object(cache, "_extract_tarball", new_callable=AsyncMock) as extract,
        ):
            target_dir = await cache.materialize(
                "squashfs-key",
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert (target_dir / "module.py").read_text() == "VALUE = 1"
        extract.assert_not_awaited()

    @pytest.mark.anyio
    async def test_materialize_mounts_seeded_squashfs_without_download(
        self, temp_cache_dir, monkeypatch
    ):
        """Seeded SquashFS cache images should mount without S3 download."""
        root = temp_cache_dir / "prebuilt"
        key = "platform/tarball-venvs/tracecat_registry/1.2.3"
        local_squashfs = root / key / "site-packages.squashfs"
        local_squashfs.parent.mkdir(parents=True)
        local_squashfs.write_bytes(b"squashfs")

        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR",
            str(root),
        )
        monkeypatch.setattr(
            "tracecat.executor.registry_artifacts.config.TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY",
            "bucket",
        )
        cache = RegistryArtifactCache(temp_cache_dir)
        tarball_uri = f"s3://bucket/{key}/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(tarball_uri)
        seeded_image = cache._squashfs_image_path(cache_key)

        async def mock_mount(image_path, target_dir):
            assert image_path == seeded_image
            assert image_path.resolve() == local_squashfs.resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / "module.py").write_text("VALUE = 1")

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
            ) as file_exists,
            patch.object(cache, "_can_try_squashfs", return_value=True),
            patch.object(
                cache, "_download_artifact", new_callable=AsyncMock
            ) as download,
            patch.object(cache, "_mount_squashfs", mock_mount),
            patch.object(cache, "_extract_tarball", new_callable=AsyncMock) as extract,
        ):
            target_dir = await cache.materialize(
                cache_key,
                tarball_uri,
            )

        assert (target_dir / "module.py").read_text() == "VALUE = 1"
        file_exists.assert_not_awaited()
        download.assert_not_awaited()
        extract.assert_not_awaited()

    @pytest.mark.anyio
    async def test_mount_squashfs_uses_hardened_read_only_options(
        self,
        temp_cache_dir,
    ):
        """Test that SquashFS images are mounted read-only without device/setuid bits."""
        cache = RegistryArtifactCache(temp_cache_dir)
        image_path = temp_cache_dir / "site-packages.squashfs"
        target_dir = temp_cache_dir / "squashfs-cache-key"
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
            await cache._mount_squashfs(image_path, target_dir)

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
    async def test_materialize_falls_back_when_squashfs_mount_fails(
        self, temp_cache_dir
    ):
        """Test that SquashFS mount failures fall back to gzip extraction."""
        cache = RegistryArtifactCache(temp_cache_dir)
        source = temp_cache_dir / "source"
        source.mkdir()
        (source / "module.py").write_text("VALUE = 1")

        async def mock_download(_uri, path):
            if path.name.endswith(".squashfs"):
                path.write_bytes(b"squashfs")
                return
            with tarfile.open(path, "w:gz") as tar:
                tar.add(source / "module.py", arcname="module.py")

        async def mock_mount(_image_path, _target_dir):
            raise RuntimeError("operation not permitted")

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                side_effect=[True, False],
            ),
            patch.object(cache, "_can_try_squashfs", return_value=True),
            patch.object(cache, "_download_artifact", mock_download),
            patch.object(cache, "_mount_squashfs", mock_mount),
        ):
            target_dir = await cache.materialize(
                "fallback-key",
                "s3://bucket/path/site-packages.tar.gz",
            )

        assert (target_dir / "module.py").read_text() == "VALUE = 1"

    @pytest.mark.anyio
    async def test_materialize_treats_unknown_suffix_as_gzip(self, temp_cache_dir):
        """Test that existing gzip artifacts can use arbitrary S3 key suffixes."""
        cache = RegistryArtifactCache(temp_cache_dir)
        source = temp_cache_dir / "source"
        source.mkdir()
        (source / "module.py").write_text("VALUE = 1")

        async def mock_download(_uri, path):
            assert path.name.endswith(".tar.gz")
            with tarfile.open(path, "w:gz") as tar:
                tar.add(source / "module.py", arcname="module.py")

        with patch.object(cache, "_download_artifact", mock_download):
            target_dir = await cache.materialize(
                "custom-key-test",
                "s3://bucket/path/custom-key",
            )

        assert (target_dir / "module.py").read_text() == "VALUE = 1"

    @pytest.mark.anyio
    async def test_materialize_caches_result(self, temp_cache_dir):
        """Test that tarball extraction is cached."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "test-cache-key"
        target_dir = temp_cache_dir / f"tarball-{cache_key}"
        target_dir.mkdir(parents=True)

        result = await cache.materialize(cache_key, "s3://bucket/test.tar.gz")

        assert result == target_dir

    @pytest.mark.anyio
    async def test_materialize_concurrent_requests(self, temp_cache_dir):
        """Test that concurrent requests for same artifact do not race."""
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "concurrent-test"
        download_count = 0

        async def mock_download(_uri, path):
            nonlocal download_count
            download_count += 1
            await asyncio.sleep(0.1)
            path.write_bytes(b"fake tarball content")

        async def mock_extract(_tarball_path, target_dir):
            (target_dir / "extracted.txt").write_text("extracted")

        with (
            patch.object(cache, "_download_artifact", mock_download),
            patch.object(cache, "_extract_tarball", mock_extract),
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
