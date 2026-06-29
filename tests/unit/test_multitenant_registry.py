"""Tests for tarball cache behavior in registry action runner.

These tests verify:
1. Tarball cache behavior (concurrent downloads, cache keys)
2. Cache key isolation per tarball URI
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tracecat.executor.registry_artifacts import (
    RegistryArtifactCache,
    TarballArtifact,
    compute_registry_artifact_cache_key,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# =============================================================================
# Test Class: Tarball Cache Behavior
# =============================================================================


class TestTarballCacheBehavior:
    """Tests for tarball cache behavior.

    Verifies that:
    - Same tarball URI requested concurrently results in only one download
    - Different URIs create separate cache entries
    - Failed extraction cleans up temp files
    """

    @pytest.mark.anyio
    async def test_concurrent_same_uri_single_download(self, temp_cache_dir: Path):
        """Verify that concurrent requests for the same tarball only download once.

        When multiple workers request the same registry tarball simultaneously,
        only one should perform the actual download. Others must wait for the
        download to complete and then use the cached extraction.

        This prevents redundant downloads and potential race conditions when
        extracting the same tarball to the same directory.

        Validates:
        - Exactly one download despite 4 concurrent materialize() calls
        - All calls return the same extracted path
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "concurrent-test-key"
        tarball_uri = "s3://bucket/concurrent-test.tar.gz"

        download_count = [0]  # Use list to allow mutation in nested function

        async def mock_download(self, ctx, path: Path):
            download_count[0] += 1
            await asyncio.sleep(0.1)  # Simulate network delay
            path.write_bytes(b"fake tarball")

        async def mock_extract(self, tarball_path: Path, target_dir: Path):
            (target_dir / "extracted.txt").write_text("content")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            # Launch multiple concurrent requests
            results = await asyncio.gather(
                cache.materialize(cache_key, tarball_uri),
                cache.materialize(cache_key, tarball_uri),
                cache.materialize(cache_key, tarball_uri),
                cache.materialize(cache_key, tarball_uri),
            )

        # All should return same path
        assert all(r == results[0] for r in results)

        # Only one download should have occurred
        assert download_count[0] == 1

    @pytest.mark.anyio
    async def test_different_uris_separate_cache_entries(self, temp_cache_dir: Path):
        """Verify that different tarball URIs are cached independently.

        Each unique tarball URI should have its own cache entry and extraction
        directory. This enables multiple registry versions to coexist in the
        cache simultaneously.

        Validates:
        - Three different URIs result in three separate downloads
        - Each URI gets a unique extraction path
        """
        cache = RegistryArtifactCache(temp_cache_dir)

        uris = [
            "s3://bucket/v1.tar.gz",
            "s3://bucket/v2.tar.gz",
            "s3://bucket/custom.tar.gz",
        ]

        download_calls: list[str] = []

        async def mock_download(self, ctx, path: Path):
            download_calls.append(self.uri)
            path.write_bytes(b"fake tarball")

        async def mock_extract(self, tarball_path: Path, target_dir: Path):
            (target_dir / "extracted.txt").write_text("content")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            results = []
            for uri in uris:
                cache_key = compute_registry_artifact_cache_key(uri)
                result = await cache.materialize(cache_key, uri)
                results.append(result)

        # All results should be different paths
        assert len({str(r) for r in results}) == 3

        # Should have downloaded 3 times
        assert len(download_calls) == 3

    @pytest.mark.anyio
    async def test_failed_extraction_cleans_up_temp_files(self, temp_cache_dir: Path):
        """Verify that failed tarball extraction cleans up partial state.

        When extraction fails (corrupt tarball, disk error, etc.), the cache
        must not be left in an inconsistent state. Temporary download files
        and partial extraction directories should be removed.

        This prevents subsequent requests from using corrupt/incomplete data
        and allows retry of the download.

        Validates:
        - No temp files remain after extraction failure
        - Target extraction directory does not exist
        - RuntimeError is raised with appropriate message
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "failed-extraction-test"
        tarball_uri = "s3://bucket/bad.tar.gz"

        async def mock_download(self, ctx, path: Path):
            path.write_bytes(b"corrupt tarball")

        async def mock_extract(self, tarball_path: Path, target_dir: Path):
            raise RuntimeError("Extraction failed - corrupt tarball")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            with pytest.raises(RuntimeError, match="Extraction failed"):
                await cache.materialize(cache_key, tarball_uri)

        # Verify no temp files remain
        temp_files = list(temp_cache_dir.glob(f"{cache_key}*"))
        assert len(temp_files) == 0, f"Temp files not cleaned up: {temp_files}"

        # Target directory should not exist
        target_dir = temp_cache_dir / f"tarball-{cache_key}"
        assert not target_dir.exists()

    @pytest.mark.anyio
    async def test_cache_reused_on_second_request(self, temp_cache_dir: Path):
        """Verify that extracted tarballs are reused on subsequent requests.

        Once a tarball has been downloaded and extracted, future requests for
        the same URI should return the cached path immediately without network
        access or re-extraction.

        Validates:
        - Only one download across two sequential requests
        - Both requests return the same path
        """
        cache = RegistryArtifactCache(temp_cache_dir)
        cache_key = "reuse-test"
        tarball_uri = "s3://bucket/reuse.tar.gz"

        download_count = [0]

        async def mock_download(self, ctx, path: Path):
            download_count[0] += 1
            path.write_bytes(b"tarball")

        async def mock_extract(self, tarball_path: Path, target_dir: Path):
            (target_dir / "file.txt").write_text("content")

        with (
            patch.object(TarballArtifact, "download", mock_download),
            patch.object(TarballArtifact, "extract", mock_extract),
        ):
            # First request
            result1 = await cache.materialize(cache_key, tarball_uri)
            assert download_count[0] == 1

            # Second request (should use cache)
            result2 = await cache.materialize(cache_key, tarball_uri)
            assert download_count[0] == 1  # No additional download

            assert result1 == result2

    @pytest.mark.anyio
    async def test_tarball_cache_keys_differ_per_uri(self, temp_cache_dir: Path):
        """Verify that tarball cache key derivation produces unique keys per URI.

        The cache key computation (hash of tarball URI) must produce unique keys
        for different URIs to prevent collisions. This test confirms the hash
        function produces distinct outputs for distinct inputs.

        Validates:
        - Three different URIs produce three unique cache keys
        """
        uri_v1 = "s3://bucket/registry-v1.0.0.tar.gz"
        uri_v2 = "s3://bucket/registry-v2.0.0.tar.gz"
        uri_custom = "s3://bucket/custom-registry-v1.tar.gz"

        key_v1 = compute_registry_artifact_cache_key(uri_v1)
        key_v2 = compute_registry_artifact_cache_key(uri_v2)
        key_custom = compute_registry_artifact_cache_key(uri_custom)

        # All keys should be unique
        assert len({key_v1, key_v2, key_custom}) == 3
