"""Registry artifact URI, key, and candidate resolution tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import tracecat_registry

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    RegistryArtifactFormat,
    SquashfsArtifact,
    TarballArtifact,
    _artifact_uri_for_logging,
    bundled_builtin_registry_uri,
    compute_registry_artifact_cache_key,
)
from tracecat.registry.artifact_keys import parse_s3_uri


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


class TestRegistryArtifactResolution:
    """Resolve artifact identities and preferred formats."""

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

    def test_artifact_uri_for_logging_removes_identifiers_and_credentials(self):
        uri = (
            "s3://access:secret@bucket/org-id/repository-origin/1.2.3/"
            "site-packages.squashfs"
            "?X-Amz-Signature=signed-secret#fragment"
        )

        logged_uri = _artifact_uri_for_logging(uri)

        assert logged_uri == "s3://<redacted>"
        assert all(
            value not in logged_uri
            for value in ("secret", "bucket", "org-id", "repository-origin", "1.2.3")
        )

    def test_artifact_uri_for_logging_redacts_malformed_uri(self):
        assert _artifact_uri_for_logging("s3://[malformed") == (
            "<redacted-artifact-uri>"
        )

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
            defer_cleanup: Callable[[Path], None],
            redact_log_identifiers: bool,
        ) -> None:
            assert defer_cleanup == ctx.defer_cleanup
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
            "tracecat.executor.registry_artifact_materialization.sysconfig.get_path",
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
            "tracecat.executor.registry_artifact_materialization.sysconfig.get_path",
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
        artifact_uri = (
            "s3://access:secret@bucket/path/site-packages.tar.gz"
            "?X-Amz-Signature=signed-secret#fragment"
        )
        artifact = TarballArtifact(
            uri=artifact_uri,
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
        assert str(exc_info.value) == "Registry artifact not found: s3://<redacted>"
        assert "secret" not in str(exc_info.value)

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
    async def test_artifact_candidates_ignore_malformed_local_sidecar(
        self, temp_cache_dir: Path
    ) -> None:
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://bucket/path/site-packages.tar.gz"
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        ctx = cache._context_for(cache_key)
        ctx.paths.squashfs_image_path.mkdir(parents=True)

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                return_value=False,
            ) as file_exists,
            patch.object(cache, "_can_try_squashfs", return_value=True),
        ):
            candidates = await cache._artifact_candidates(ctx, artifact_uri)

        assert len(candidates) == 1
        assert isinstance(candidates[0], TarballArtifact)
        file_exists.assert_awaited_once_with(
            key="path/site-packages.squashfs",
            bucket="bucket",
        )

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
    async def test_sidecar_lookup_failure_redacts_exception_text(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """SDK exception strings cannot leak registry identifiers into logs."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "s3://sensitive-bucket/org/repo/site-packages.tar.gz"
        sidecar_uri = "s3://sensitive-bucket/org/repo/site-packages.squashfs"
        ctx = cache._context_for(compute_registry_artifact_cache_key(artifact_uri))
        lookup_error = ConnectionError(
            f"Could not connect to endpoint URL: {sidecar_uri}"
        )

        with (
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
                side_effect=lookup_error,
            ),
            patch.object(cache, "_can_try_squashfs", return_value=True),
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            candidates = await cache._artifact_candidates(ctx, artifact_uri)

        assert len(candidates) == 1
        assert isinstance(candidates[0], TarballArtifact)
        warning.assert_called_once_with(
            "Failed to check for registry artifact sidecar, falling back",
            artifact_uri="s3://<redacted>",
            sidecar_uri="s3://<redacted>",
            artifact_format=RegistryArtifactFormat.SQUASHFS.value,
            error_type="ConnectionError",
        )
        assert "sensitive-bucket" not in repr(warning.call_args)
        assert "org/repo" not in repr(warning.call_args)

    @pytest.mark.anyio
    async def test_sidecar_parse_failure_uses_redacted_fallback(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """Malformed sidecar URIs cannot escape the redacted fallback path."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = (
            "https://tenant-bucket.invalid/org/repository/site-packages.tar.gz"
        )
        ctx = cache._context_for(compute_registry_artifact_cache_key(artifact_uri))

        with (
            patch.object(cache, "_can_try_squashfs", return_value=True),
            patch(
                "tracecat.executor.registry_artifacts.blob.file_exists",
                new_callable=AsyncMock,
            ) as file_exists,
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            candidates = await cache._artifact_candidates(ctx, artifact_uri)

        assert len(candidates) == 1
        assert isinstance(candidates[0], TarballArtifact)
        file_exists.assert_not_awaited()
        warning.assert_called_once_with(
            "Failed to check for registry artifact sidecar, falling back",
            artifact_uri="https://<redacted>",
            sidecar_uri="https://<redacted>",
            artifact_format=RegistryArtifactFormat.SQUASHFS.value,
            error_type="ValueError",
        )
        assert "tenant-bucket" not in repr(warning.call_args)
        assert "org/repository" not in repr(warning.call_args)

    @pytest.mark.anyio
    async def test_materialization_fallback_redacts_malformed_uri_error(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """Malformed candidate URIs cannot leak identifiers through errors."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = (
            "https://tenant-bucket.invalid/org/repository/site-packages.squashfs"
        )
        cache_key = compute_registry_artifact_cache_key(artifact_uri)
        ctx = cache._context_for(cache_key)
        candidates = await cache._artifact_candidates(ctx, artifact_uri)
        fallback_path = temp_cache_dir / "fallback"

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization."
                "config.TRACECAT__EXECUTOR_REGISTRY_SQUASHFS_ENABLED",
                False,
            ),
            patch.object(
                TarballArtifact,
                "materialize",
                new_callable=AsyncMock,
                return_value=[fallback_path],
            ) as materialize_fallback,
            patch("tracecat.executor.registry_artifacts.logger.warning") as warning,
        ):
            registry_paths = await cache._materialize_candidates(ctx, candidates)

        assert registry_paths == [fallback_path]
        materialize_fallback.assert_awaited_once()
        warning.assert_called_once_with(
            "Failed to materialize registry artifact candidate, trying fallback",
            cache_key=cache_key,
            artifact_uri="https://<redacted>",
            artifact_format=RegistryArtifactFormat.SQUASHFS.value,
            error_type="RegistryArtifactUriError",
        )
        assert "tenant-bucket" not in repr(warning.call_args)
        assert "org/repository" not in repr(warning.call_args)

    @pytest.mark.anyio
    async def test_final_candidate_malformed_uri_error_is_sanitized(
        self,
        temp_cache_dir: Path,
    ) -> None:
        """A final candidate cannot expose its malformed URI to callers."""
        cache = RegistryArtifactCache(temp_cache_dir)
        artifact_uri = "https://tenant-bucket.invalid/org/repository/artifact.tar.gz"

        with pytest.raises(ValueError) as exc_info:
            async with cache.lease([artifact_uri]):
                pass

        assert str(exc_info.value) == "Invalid registry artifact URI"
        assert exc_info.value.__cause__ is None
        assert "tenant-bucket" not in repr(exc_info.value)
        assert "org/repository" not in repr(exc_info.value)

    def test_can_try_squashfs_does_not_require_mount_binary(self, temp_cache_dir):
        """Prefer SquashFS whenever enabled; extraction may work without mounts."""
        cache = RegistryArtifactCache(temp_cache_dir)

        with (
            patch(
                "tracecat.executor.registry_artifact_materialization.shutil.which",
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
