"""Tests for executor registry artifact materialization."""

from __future__ import annotations

import asyncio
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import tracecat_registry

from tracecat.executor.registry_artifacts import (
    SQUASHFS_MOUNT_OPTIONS,
    RegistryArtifactCache,
    RegistryArtifactFormat,
    SquashfsArtifact,
    TarballArtifact,
    bundled_builtin_registry_uri,
    compute_registry_artifact_cache_key,
)
from tracecat.registry.artifact_keys import parse_s3_uri


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
    async def test_ensure_environment_uses_bundled_current_builtin(
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
        result = await cache.ensure_environment(bundled_builtin_registry_uri(version))

        assert result == [site_packages.resolve()]

    @pytest.mark.anyio
    async def test_ensure_environment_exposes_editable_builtin_parent(
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
        result = await cache.ensure_environment(bundled_builtin_registry_uri(version))

        assert result == [source_root.resolve(), site_packages.resolve()]

    @pytest.mark.anyio
    async def test_ensure_environment_rejects_stale_bundled_builtin(
        self, temp_cache_dir, monkeypatch: pytest.MonkeyPatch
    ):
        """Bundled pseudo-URIs must match this executor's installed package."""
        monkeypatch.setattr(tracecat_registry, "__version__", "1.2.3")

        cache = RegistryArtifactCache(temp_cache_dir)
        with pytest.raises(RuntimeError, match="does not match installed version"):
            await cache.ensure_environment(bundled_builtin_registry_uri("1.2.4"))

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
    async def test_materialize_extracts_squashfs_when_mount_fails(self, temp_cache_dir):
        """Test that SquashFS mount failures fall back to unsquashfs extraction."""
        cache = RegistryArtifactCache(temp_cache_dir)

        async def mock_mount(self, ctx, image_path):
            raise RuntimeError("operation not permitted")

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
            raise RuntimeError("operation not permitted")

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
