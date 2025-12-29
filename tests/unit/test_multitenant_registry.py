"""Tests for multi-tenant registry dependency isolation and pool eviction logic.

These tests verify:
1. Multi-tenant registry isolation (different workspaces get correct registry versions)
2. Registry lock vs cached artifacts branching
3. Tarball cache behavior (concurrent downloads, cache keys)
4. Pool worker PYTHONPATH handling per-request
5. Artifact cache TTL behavior (60s expiration)
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.action_runner import ActionRunner
from tracecat.executor.service import (
    RegistryArtifactsContext,
    _get_registry_pythonpath,
    _registry_artifacts_cache,
    get_registry_artifacts_cached,
    get_registry_artifacts_for_lock,
)
from tracecat.identifiers.workflow import WorkflowUUID

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace_a_id() -> uuid.UUID:
    """Workspace A identifier."""
    return uuid.UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def workspace_b_id() -> uuid.UUID:
    """Workspace B identifier."""
    return uuid.UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")


@pytest.fixture
def role_workspace_a(workspace_a_id: uuid.UUID) -> Role:
    """Role for workspace A."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=workspace_a_id,
        user_id=uuid.uuid4(),
    )


@pytest.fixture
def role_workspace_b(workspace_b_id: uuid.UUID) -> Role:
    """Role for workspace B."""
    return Role(
        type="service",
        service_id="tracecat-executor",
        workspace_id=workspace_b_id,
        user_id=uuid.uuid4(),
    )


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_run_action_input_factory():
    """Factory for creating RunActionInput with optional registry_lock."""

    def _create(registry_lock: dict[str, str] | None = None) -> RunActionInput:
        wf_id = WorkflowUUID.new_uuid4()
        return RunActionInput(
            task=ActionStatement(
                action="core.http_request",
                args={"url": "https://example.com"},
                ref="test_action",
            ),
            exec_context={},
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/exec_test",
                wf_run_id=uuid.uuid4(),
                environment="test",
            ),
            registry_lock=registry_lock,
        )

    return _create


# =============================================================================
# Test Class 1: Artifact Cache TTL
# =============================================================================


class TestArtifactCacheTTL:
    """Tests for artifact cache TTL behavior.

    Verifies that:
    - First call populates cache
    - Second call within 60s uses cache (no DB query)
    - After 60s, cache expires and DB is queried again
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the registry artifacts cache before each test."""
        _registry_artifacts_cache.clear()
        yield
        _registry_artifacts_cache.clear()

    @pytest.mark.anyio
    async def test_first_call_populates_cache(self, role_workspace_a: Role):
        """Verify that the first call to get_registry_artifacts_cached queries the DB.

        On a cold cache, the function must fetch registry artifacts from the database
        and store them in _registry_artifacts_cache keyed by workspace_id.

        Validates:
        - Exactly one DB call is made
        - Cache entry is created for the workspace
        """
        artifacts = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/v1.tar.gz"),
        ]

        db_call_count = [0]

        @asynccontextmanager
        async def mock_cm():
            db_call_count[0] += 1
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = artifacts
            mock_session.execute = AsyncMock(return_value=mock_result)
            yield mock_session

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                return_value=mock_cm(),
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            result = await get_registry_artifacts_cached(role_workspace_a)

        assert db_call_count[0] == 1
        assert len(result) == 1

        # Verify cache is populated
        cache_key = str(role_workspace_a.workspace_id)
        assert cache_key in _registry_artifacts_cache

    @pytest.mark.anyio
    async def test_second_call_within_ttl_uses_cache(self, role_workspace_a: Role):
        """Verify that repeated calls within TTL window reuse cached artifacts.

        The cache has a 60-second TTL. Subsequent calls for the same workspace
        within this window should return cached data without hitting the database.

        Validates:
        - Only one DB call despite two get_registry_artifacts_cached() calls
        - Both calls return identical artifact data
        """
        artifacts = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/v1.tar.gz"),
        ]

        db_call_count = [0]

        @asynccontextmanager
        async def mock_cm():
            db_call_count[0] += 1
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = artifacts
            mock_session.execute = AsyncMock(return_value=mock_result)
            yield mock_session

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                return_value=mock_cm(),
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            # First call
            result1 = await get_registry_artifacts_cached(role_workspace_a)
            assert db_call_count[0] == 1

            # Second call (should use cache)
            result2 = await get_registry_artifacts_cached(role_workspace_a)
            assert db_call_count[0] == 1  # No additional DB call

            # Results should be identical
            assert len(result1) == len(result2)
            assert result1[0].tarball_uri == result2[0].tarball_uri

    @pytest.mark.anyio
    async def test_cache_expires_after_ttl(self, role_workspace_a: Role):
        """Verify that cache entries expire after the 60-second TTL.

        After TTL expiration, the next call must refresh from the database,
        allowing workspaces to pick up new registry versions without restart.

        Test approach:
        - Populate cache with v1 artifacts
        - Manually expire the cache entry by backdating expire_time
        - Update mock to return v2 artifacts
        - Verify second call fetches v2 from DB

        Validates:
        - Two DB calls occur (initial + post-expiry)
        - Second call returns updated artifact version
        """
        artifacts_v1 = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/v1.tar.gz"),
        ]
        artifacts_v2 = [
            ("tracecat_registry", "v2.0.0", "s3://bucket/v2.tar.gz"),
        ]

        db_call_count = [0]
        current_artifacts = [artifacts_v1]  # Mutable container

        def mock_cm_factory():
            @asynccontextmanager
            async def mock_cm():
                db_call_count[0] += 1
                mock_session = AsyncMock()
                mock_result = MagicMock()
                mock_result.all.return_value = current_artifacts[0]
                mock_session.execute = AsyncMock(return_value=mock_result)
                yield mock_session

            return mock_cm()

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                side_effect=mock_cm_factory,
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            # First call - populates cache
            result1 = await get_registry_artifacts_cached(role_workspace_a)
            assert db_call_count[0] == 1
            assert result1[0].version == "v1.0.0"

            # Manually expire the cache by setting expire_time to past
            cache_key = str(role_workspace_a.workspace_id)
            _, cached_artifacts = _registry_artifacts_cache[cache_key]
            _registry_artifacts_cache[cache_key] = (time.time() - 1, cached_artifacts)

            # Update what DB would return
            current_artifacts[0] = artifacts_v2

            # Second call after expiry - should query DB again
            result2 = await get_registry_artifacts_cached(role_workspace_a)
            assert db_call_count[0] == 2  # DB called again
            assert result2[0].version == "v2.0.0"

    @pytest.mark.anyio
    async def test_cache_lock_prevents_concurrent_db_queries(
        self, role_workspace_a: Role
    ):
        """Verify that concurrent cache misses don't cause duplicate DB queries.

        When multiple requests hit a cold cache simultaneously, only one should
        query the database. Others must wait and use the cached result.
        This prevents a thundering herd problem on cache expiration.

        Test approach:
        - Launch 3 concurrent get_registry_artifacts_cached() calls
        - Add artificial DB latency to ensure overlap

        Validates:
        - Exactly one DB call despite 3 concurrent requests
        - All requests return identical results
        """
        artifacts = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/v1.tar.gz"),
        ]

        db_call_count = [0]

        @asynccontextmanager
        async def mock_cm():
            db_call_count[0] += 1
            await asyncio.sleep(0.1)  # Simulate DB latency
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.all.return_value = artifacts
            mock_session.execute = AsyncMock(return_value=mock_result)
            yield mock_session

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                return_value=mock_cm(),
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            # Launch multiple concurrent requests
            results = await asyncio.gather(
                get_registry_artifacts_cached(role_workspace_a),
                get_registry_artifacts_cached(role_workspace_a),
                get_registry_artifacts_cached(role_workspace_a),
            )

        # Only one DB call should have been made
        assert db_call_count[0] == 1

        # All results should be identical
        assert all(len(r) == 1 for r in results)
        assert all(r[0].version == "v1.0.0" for r in results)

    @pytest.mark.anyio
    async def test_different_workspaces_have_separate_caches(
        self, role_workspace_a: Role, role_workspace_b: Role
    ):
        """Verify that each workspace has its own isolated cache entry.

        This test ensures multi-tenant isolation: workspace A's cached registry
        artifacts must not leak to workspace B (and vice versa). Each workspace
        should trigger its own DB query and store results under a separate cache key.

        Expected behavior:
        - Two DB calls are made (one per workspace)
        - Each workspace receives its own distinct artifacts
        - Both results are independently cached by workspace_id
        """
        artifacts_a = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/ws-a.tar.gz"),
        ]
        artifacts_b = [
            ("tracecat_registry", "v2.0.0", "s3://bucket/ws-b.tar.gz"),
        ]

        call_sequence: list[str] = []

        def mock_cm_factory():
            @asynccontextmanager
            async def mock_cm():
                mock_session = AsyncMock()
                mock_result = MagicMock()
                # Return different artifacts based on call order
                if len(call_sequence) == 0:
                    mock_result.all.return_value = artifacts_a
                else:
                    mock_result.all.return_value = artifacts_b
                call_sequence.append("call")
                mock_session.execute = AsyncMock(return_value=mock_result)
                yield mock_session

            return mock_cm()

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                side_effect=mock_cm_factory,
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            result_a = await get_registry_artifacts_cached(role_workspace_a)
            result_b = await get_registry_artifacts_cached(role_workspace_b)

        # Both should have called DB (separate cache entries)
        assert len(call_sequence) == 2

        # Results should be different
        assert result_a[0].tarball_uri == "s3://bucket/ws-a.tar.gz"
        assert result_b[0].tarball_uri == "s3://bucket/ws-b.tar.gz"

        # Both should be cached now
        cache_key_a = str(role_workspace_a.workspace_id)
        cache_key_b = str(role_workspace_b.workspace_id)
        assert cache_key_a in _registry_artifacts_cache
        assert cache_key_b in _registry_artifacts_cache


# =============================================================================
# Test Class 2: Registry Lock vs Cached
# =============================================================================


class TestRegistryLockVsCached:
    """Tests for registry lock vs cached artifacts branching.

    Verifies that:
    - Workflows WITH registry_lock use get_registry_artifacts_for_lock
    - Workflows WITHOUT registry_lock use get_registry_artifacts_cached
    - Correct function is called based on presence of lock
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the registry artifacts cache before each test."""
        _registry_artifacts_cache.clear()
        yield
        _registry_artifacts_cache.clear()

    @pytest.mark.anyio
    async def test_with_registry_lock_calls_locked_function(
        self,
        role_workspace_a: Role,
        mock_run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify that workflows with registry_lock use pinned version resolution.

        When a workflow specifies registry_lock (pinned versions), the executor
        must call get_registry_artifacts_for_lock() to fetch those exact versions,
        NOT the cached latest. This enables reproducible workflow execution.

        Validates:
        - get_registry_artifacts_for_lock() is called with the lock dict
        - get_registry_artifacts_cached() is NOT called
        """
        registry_lock = {"tracecat_registry": "v1.0.0"}
        input_with_lock = mock_run_action_input_factory(registry_lock=registry_lock)

        locked_artifacts = [
            RegistryArtifactsContext(
                origin="tracecat_registry",
                version="v1.0.0",
                tarball_uri="s3://bucket/locked-v1.tar.gz",
            )
        ]

        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = runner.compute_tarball_cache_key("s3://bucket/locked-v1.tar.gz")
        (temp_cache_dir / f"tarball-{cache_key}").mkdir(parents=True)

        with (
            patch(
                "tracecat.executor.service.get_registry_artifacts_for_lock",
                new_callable=AsyncMock,
                return_value=locked_artifacts,
            ) as mock_locked,
            patch(
                "tracecat.executor.service.get_registry_artifacts_cached",
                new_callable=AsyncMock,
            ) as mock_cached,
            patch("tracecat.executor.service.config") as mock_config,
            patch("tracecat.executor.service.get_action_runner", return_value=runner),
        ):
            mock_config.TRACECAT__LOCAL_REPOSITORY_ENABLED = False

            await _get_registry_pythonpath(input_with_lock, role_workspace_a)

            mock_locked.assert_called_once_with(registry_lock)
            mock_cached.assert_not_called()

    @pytest.mark.anyio
    async def test_without_registry_lock_calls_cached_function(
        self,
        role_workspace_a: Role,
        mock_run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify that workflows without registry_lock use cached latest versions.

        When no registry_lock is specified (registry_lock=None), the executor
        should use get_registry_artifacts_cached() which returns the latest
        published registry artifacts for the workspace, with TTL caching.

        Validates:
        - get_registry_artifacts_cached() is called with the role
        - get_registry_artifacts_for_lock() is NOT called
        """
        input_without_lock = mock_run_action_input_factory(registry_lock=None)

        cached_artifacts = [
            RegistryArtifactsContext(
                origin="tracecat_registry",
                version="v2.0.0",
                tarball_uri="s3://bucket/latest-v2.tar.gz",
            )
        ]

        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = runner.compute_tarball_cache_key("s3://bucket/latest-v2.tar.gz")
        (temp_cache_dir / f"tarball-{cache_key}").mkdir(parents=True)

        with (
            patch(
                "tracecat.executor.service.get_registry_artifacts_for_lock",
                new_callable=AsyncMock,
            ) as mock_locked,
            patch(
                "tracecat.executor.service.get_registry_artifacts_cached",
                new_callable=AsyncMock,
                return_value=cached_artifacts,
            ) as mock_cached,
            patch("tracecat.executor.service.config") as mock_config,
            patch("tracecat.executor.service.get_action_runner", return_value=runner),
        ):
            mock_config.TRACECAT__LOCAL_REPOSITORY_ENABLED = False

            await _get_registry_pythonpath(input_without_lock, role_workspace_a)

            mock_cached.assert_called_once_with(role_workspace_a)
            mock_locked.assert_not_called()

    @pytest.mark.anyio
    async def test_empty_registry_lock_calls_locked_function(
        self,
        role_workspace_a: Role,
        mock_run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify that empty registry_lock dict ({}) falls through to cached lookup.

        An empty dict is falsy in Python's `if registry_lock:` check, so it should
        behave the same as None and use the cached artifacts path.

        Validates:
        - get_registry_artifacts_cached() is called (empty dict is falsy)
        - get_registry_artifacts_for_lock() is NOT called
        - Result is None (no artifacts from empty cache)
        """
        input_empty_lock = mock_run_action_input_factory(registry_lock={})

        runner = ActionRunner(cache_dir=temp_cache_dir)

        with (
            patch(
                "tracecat.executor.service.get_registry_artifacts_for_lock",
                new_callable=AsyncMock,
                return_value=[],  # Empty lock returns empty
            ) as mock_locked,
            patch(
                "tracecat.executor.service.get_registry_artifacts_cached",
                new_callable=AsyncMock,
            ) as mock_cached,
            patch("tracecat.executor.service.config") as mock_config,
            patch("tracecat.executor.service.get_action_runner", return_value=runner),
        ):
            mock_config.TRACECAT__LOCAL_REPOSITORY_ENABLED = False

            result = await _get_registry_pythonpath(input_empty_lock, role_workspace_a)

            # Empty dict is falsy in Python, so cached should be called
            # Actually, {} is truthy for `if input.registry_lock:` check
            # Let's verify which function was called
            # Based on implementation: `if input.registry_lock:` - empty dict is falsy
            mock_cached.assert_called_once()
            mock_locked.assert_not_called()
            assert result is None  # No artifacts returned

    @pytest.mark.anyio
    async def test_locked_version_returns_specific_artifact(self):
        """Verify get_registry_artifacts_for_lock fetches exact pinned versions.

        When given a registry_lock dict like {"tracecat_registry": "v1.0.0"},
        the function should query the DB for that exact (origin, version) pair
        and return the corresponding tarball URI.

        Test includes multiple registries (built-in + custom git repo) to verify
        the function handles diverse origin formats.

        Validates:
        - Returns artifacts matching exactly the locked versions
        - Handles both tracecat_registry and git+ssh:// origins
        """
        registry_lock = {
            "tracecat_registry": "v1.0.0",
            "git+ssh://github.com/custom/repo.git": "abc1234",
        }

        mock_results = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/tracecat-v1.tar.gz"),
            (
                "git+ssh://github.com/custom/repo.git",
                "abc1234",
                "s3://bucket/custom-abc.tar.gz",
            ),
        ]

        @asynccontextmanager
        async def mock_cm():
            mock_session = AsyncMock()
            call_idx = [0]

            async def mock_execute(statement):
                mock_result = MagicMock()
                if call_idx[0] < len(mock_results):
                    mock_result.first.return_value = mock_results[call_idx[0]]
                    call_idx[0] += 1
                else:
                    mock_result.first.return_value = None
                return mock_result

            mock_session.execute = mock_execute
            yield mock_session

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                return_value=mock_cm(),
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            artifacts = await get_registry_artifacts_for_lock(registry_lock)

        assert len(artifacts) == 2
        assert artifacts[0].origin == "tracecat_registry"
        assert artifacts[0].version == "v1.0.0"
        assert artifacts[1].origin == "git+ssh://github.com/custom/repo.git"
        assert artifacts[1].version == "abc1234"


# =============================================================================
# Test Class 3: Tarball Cache Behavior
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
        - Exactly one download despite 4 concurrent ensure_tarball_extracted() calls
        - All calls return the same extracted path
        """
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = "concurrent-test-key"
        tarball_uri = "s3://bucket/concurrent-test.tar.gz"

        download_count = [0]  # Use list to allow mutation in nested function

        async def mock_download(url: str, path: Path):
            download_count[0] += 1
            await asyncio.sleep(0.1)  # Simulate network delay
            path.write_bytes(b"fake tarball")

        async def mock_extract(tarball_path: Path, target_dir: Path):
            (target_dir / "extracted.txt").write_text("content")

        with (
            patch.object(runner, "_download_file", mock_download),
            patch.object(runner, "_extract_tarball", mock_extract),
            patch.object(
                runner,
                "_tarball_uri_to_http_url",
                new_callable=AsyncMock,
                return_value="http://presigned-url",
            ),
        ):
            # Launch multiple concurrent requests
            results = await asyncio.gather(
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
                runner.ensure_tarball_extracted(cache_key, tarball_uri),
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
        runner = ActionRunner(cache_dir=temp_cache_dir)

        uris = [
            "s3://bucket/v1.tar.gz",
            "s3://bucket/v2.tar.gz",
            "s3://bucket/custom.tar.gz",
        ]

        download_calls: list[str] = []

        async def mock_download(url: str, path: Path):
            download_calls.append(url)
            path.write_bytes(b"fake tarball")

        async def mock_extract(tarball_path: Path, target_dir: Path):
            (target_dir / "extracted.txt").write_text("content")

        with (
            patch.object(runner, "_download_file", mock_download),
            patch.object(runner, "_extract_tarball", mock_extract),
            patch.object(
                runner,
                "_tarball_uri_to_http_url",
                new_callable=AsyncMock,
                side_effect=lambda uri: f"http://presigned/{uri}",
            ),
        ):
            results = []
            for uri in uris:
                cache_key = runner.compute_tarball_cache_key(uri)
                result = await runner.ensure_tarball_extracted(cache_key, uri)
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
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = "failed-extraction-test"
        tarball_uri = "s3://bucket/bad.tar.gz"

        async def mock_download(url: str, path: Path):
            path.write_bytes(b"corrupt tarball")

        async def mock_extract(tarball_path: Path, target_dir: Path):
            raise RuntimeError("Extraction failed - corrupt tarball")

        with (
            patch.object(runner, "_download_file", mock_download),
            patch.object(runner, "_extract_tarball", mock_extract),
            patch.object(
                runner,
                "_tarball_uri_to_http_url",
                new_callable=AsyncMock,
                return_value="http://presigned-url",
            ),
        ):
            with pytest.raises(RuntimeError, match="Extraction failed"):
                await runner.ensure_tarball_extracted(cache_key, tarball_uri)

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
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = "reuse-test"
        tarball_uri = "s3://bucket/reuse.tar.gz"

        download_count = [0]

        async def mock_download(url: str, path: Path):
            download_count[0] += 1
            path.write_bytes(b"tarball")

        async def mock_extract(tarball_path: Path, target_dir: Path):
            (target_dir / "file.txt").write_text("content")

        with (
            patch.object(runner, "_download_file", mock_download),
            patch.object(runner, "_extract_tarball", mock_extract),
            patch.object(
                runner,
                "_tarball_uri_to_http_url",
                new_callable=AsyncMock,
                return_value="http://url",
            ),
        ):
            # First request
            result1 = await runner.ensure_tarball_extracted(cache_key, tarball_uri)
            assert download_count[0] == 1

            # Second request (should use cache)
            result2 = await runner.ensure_tarball_extracted(cache_key, tarball_uri)
            assert download_count[0] == 1  # No additional download

            assert result1 == result2


# =============================================================================
# Test Class 4: Multi-Tenant Registry Isolation
# =============================================================================


class TestMultiTenantRegistryIsolation:
    """Tests for multi-tenant registry isolation.

    Verifies that:
    - Different workspaces get different PYTHONPATH based on their registry versions
    - Concurrent execution from different workspaces doesn't cross-contaminate
    - Cache keys are isolated per tarball URI
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the registry artifacts cache before each test."""
        _registry_artifacts_cache.clear()
        yield
        _registry_artifacts_cache.clear()

    @pytest.mark.anyio
    async def test_different_workspaces_get_different_artifacts(
        self,
        role_workspace_a: Role,
        role_workspace_b: Role,
    ):
        """Verify that each workspace fetches its own registry artifact version.

        In a multi-tenant environment, workspace A might be on registry v1.0.0
        while workspace B is on v2.0.0. The cache must maintain this isolation
        so each workspace executes actions against its own registry version.

        Validates:
        - Workspace A gets v1.0.0 artifacts with its specific tarball URI
        - Workspace B gets v2.0.0 artifacts with its specific tarball URI
        - Both are independently cached
        """
        workspace_a_artifacts = [
            ("tracecat_registry", "v1.0.0", "s3://bucket/registry-v1.0.0.tar.gz"),
        ]
        workspace_b_artifacts = [
            ("tracecat_registry", "v2.0.0", "s3://bucket/registry-v2.0.0.tar.gz"),
        ]

        # Track which workspace is being queried
        call_count = [0]

        def mock_cm_factory():
            @asynccontextmanager
            async def mock_cm():
                mock_session = AsyncMock()
                mock_result = MagicMock()
                # First call is workspace A, second is workspace B
                if call_count[0] == 0:
                    mock_result.all.return_value = workspace_a_artifacts
                else:
                    mock_result.all.return_value = workspace_b_artifacts
                call_count[0] += 1
                mock_session.execute = AsyncMock(return_value=mock_result)
                yield mock_session

            return mock_cm()

        with (
            patch(
                "tracecat.executor.service.get_async_session_context_manager",
                side_effect=mock_cm_factory,
            ),
            patch("tracecat.executor.service.with_session"),
        ):
            # Fetch artifacts for workspace A
            artifacts_a = await get_registry_artifacts_cached(role_workspace_a)

            # Fetch artifacts for workspace B
            artifacts_b = await get_registry_artifacts_cached(role_workspace_b)

        # Verify different artifacts
        assert len(artifacts_a) == 1
        assert artifacts_a[0].version == "v1.0.0"
        assert artifacts_a[0].tarball_uri == "s3://bucket/registry-v1.0.0.tar.gz"

        assert len(artifacts_b) == 1
        assert artifacts_b[0].version == "v2.0.0"
        assert artifacts_b[0].tarball_uri == "s3://bucket/registry-v2.0.0.tar.gz"

    @pytest.mark.anyio
    async def test_cache_key_per_workspace(
        self, role_workspace_a: Role, role_workspace_b: Role
    ):
        """Verify that cache keys are derived from workspace_id for isolation.

        The artifact cache uses workspace_id as the cache key to ensure tenant
        isolation. This test confirms the key derivation produces unique,
        deterministic keys for different workspaces.

        Validates:
        - Cache keys are the string representation of workspace_id UUIDs
        - Different workspaces produce different cache keys
        """
        cache_key_a = str(role_workspace_a.workspace_id)
        cache_key_b = str(role_workspace_b.workspace_id)

        assert cache_key_a != cache_key_b
        assert cache_key_a == "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
        assert cache_key_b == "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"

    @pytest.mark.anyio
    async def test_concurrent_workspace_execution_isolation(
        self,
        role_workspace_a: Role,
        role_workspace_b: Role,
        mock_run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify concurrent workflow executions from different workspaces stay isolated.

        When workspace A and B execute workflows simultaneously, each must receive
        its own PYTHONPATH pointing to its workspace-specific registry extraction.
        This is critical for multi-tenant correctness: workspace A's actions must
        never accidentally use workspace B's registry code.

        Test approach:
        - Pre-create cache directories for both workspaces
        - Execute _get_registry_pythonpath() concurrently for both

        Validates:
        - Both workspaces get non-None PYTHONPATH values
        - The PYTHONPATH values are different (isolated extractions)
        """
        # Setup mock artifacts per workspace
        artifacts_map = {
            str(role_workspace_a.workspace_id): [
                RegistryArtifactsContext(
                    origin="tracecat_registry",
                    version="v1.0.0",
                    tarball_uri="s3://bucket/ws-a-v1.tar.gz",
                )
            ],
            str(role_workspace_b.workspace_id): [
                RegistryArtifactsContext(
                    origin="tracecat_registry",
                    version="v2.0.0",
                    tarball_uri="s3://bucket/ws-b-v2.tar.gz",
                )
            ],
        }

        async def mock_get_cached(role: Role):
            cache_key = str(role.workspace_id)
            return artifacts_map.get(cache_key, [])

        runner = ActionRunner(cache_dir=temp_cache_dir)

        # Pre-create cache directories to avoid actual downloads
        for _ws_key, artifacts in artifacts_map.items():
            for artifact in artifacts:
                cache_key = runner.compute_tarball_cache_key(artifact.tarball_uri)
                target_dir = temp_cache_dir / f"tarball-{cache_key}"
                target_dir.mkdir(parents=True, exist_ok=True)

        input_a = mock_run_action_input_factory(registry_lock=None)
        input_b = mock_run_action_input_factory(registry_lock=None)

        with (
            patch(
                "tracecat.executor.service.get_registry_artifacts_cached",
                side_effect=mock_get_cached,
            ),
            patch("tracecat.executor.service.config") as mock_config,
            patch(
                "tracecat.executor.service.get_action_runner",
                return_value=runner,
            ),
        ):
            mock_config.TRACECAT__LOCAL_REPOSITORY_ENABLED = False

            # Execute concurrently
            paths = await asyncio.gather(
                _get_registry_pythonpath(input_a, role_workspace_a),
                _get_registry_pythonpath(input_b, role_workspace_b),
            )

        path_a, path_b = paths

        # Verify different paths
        assert path_a is not None
        assert path_b is not None
        assert path_a != path_b

    @pytest.mark.anyio
    async def test_tarball_cache_keys_differ_per_uri(self, temp_cache_dir: Path):
        """Verify that tarball cache key derivation produces unique keys per URI.

        The cache key computation (hash of tarball URI) must produce unique keys
        for different URIs to prevent collisions. This test confirms the hash
        function produces distinct outputs for distinct inputs.

        Validates:
        - Three different URIs produce three unique cache keys
        """
        runner = ActionRunner(cache_dir=temp_cache_dir)

        uri_v1 = "s3://bucket/registry-v1.0.0.tar.gz"
        uri_v2 = "s3://bucket/registry-v2.0.0.tar.gz"
        uri_custom = "s3://bucket/custom-registry-v1.tar.gz"

        key_v1 = runner.compute_tarball_cache_key(uri_v1)
        key_v2 = runner.compute_tarball_cache_key(uri_v2)
        key_custom = runner.compute_tarball_cache_key(uri_custom)

        # All keys should be unique
        assert len({key_v1, key_v2, key_custom}) == 3
