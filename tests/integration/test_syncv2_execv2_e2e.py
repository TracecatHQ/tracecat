"""End-to-end tests for Syncv2 -> Execv2 flow with MinIO.

These tests verify the full integration:
1. Sync builtin/git registry -> tarball uploaded to MinIO
2. Execute action -> tarball downloaded, extracted, action runs in subprocess

Requirements:
- Docker Compose dev stack running (`just dev`)
- MinIO server (started by fixture)
- PostgreSQL (started by fixture)

Run with:
    uv run pytest tests/integration/test_syncv2_execv2_e2e.py -x -v
"""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from minio import Minio

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.action_runner import ActionRunner
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.schemas import ExecutorResultSuccess
from tracecat.executor.service import (
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.sync.service import RegistrySyncService
from tracecat.storage import blob

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# =============================================================================
# Test Configuration
# =============================================================================

# MinIO test configuration (matches conftest.py)
MINIO_ENDPOINT = "localhost:9002"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
TEST_BUCKET = "test-tracecat-registry"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def anyio_backend():
    """Module-scoped anyio backend."""
    return "asyncio"


@pytest.fixture(autouse=True)
def configure_minio_for_tests(monkeypatch):
    """Configure blob storage to use test MinIO instance."""
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_PROTOCOL", "minio")
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_ENDPOINT", f"http://{MINIO_ENDPOINT}"
    )
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", TEST_BUCKET)
    monkeypatch.setenv("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)

    # Disable nsjail for all tests (use subprocess mode for macOS/CI)
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")
    monkeypatch.setattr(config, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_SANDBOX_ENABLED", False)

    # Reload blob module to pick up new config
    importlib.reload(blob)


@pytest.fixture
async def test_bucket(minio_client: Minio):
    """Create test bucket for registry tarballs."""
    bucket_name = TEST_BUCKET

    # Create bucket if it doesn't exist
    if not minio_client.bucket_exists(bucket_name):
        minio_client.make_bucket(bucket_name)

    yield bucket_name

    # Cleanup: remove all objects
    try:
        objects = minio_client.list_objects(bucket_name, recursive=True)
        for obj in objects:
            if obj.object_name:
                minio_client.remove_object(bucket_name, obj.object_name)
    except Exception:
        pass  # Ignore cleanup errors


@pytest.fixture
async def builtin_repo(db, session: AsyncSession, test_role: Role):
    """Create builtin registry repository for testing.

    Note: depends on `db` fixture to ensure test database is created.
    Uses the test session directly to ensure transaction visibility.
    """
    svc = RegistryReposService(session, role=test_role)
    # Get existing or create new builtin repository
    repo = await svc.get_repository(DEFAULT_REGISTRY_ORIGIN)
    if repo is None:
        repo = await svc.create_repository(
            RegistryRepositoryCreate(origin=DEFAULT_REGISTRY_ORIGIN)
        )
    yield repo


@pytest.fixture
def run_action_input_factory():
    """Factory for creating RunActionInput objects."""

    def _create(
        action: str = "core.transform.reshape",
        args: dict | None = None,
        registry_lock: dict[str, str] | None = None,
    ) -> RunActionInput:
        wf_id = WorkflowUUID.new_uuid4()
        return RunActionInput(
            task=ActionStatement(
                action=action,
                args=args or {"value": {"test": True}},
                ref="test_action",
            ),
            exec_context={},
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/exec_test",
                wf_run_id=uuid.uuid4(),
                environment="default",
            ),
            registry_lock=registry_lock,
        )

    return _create


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create temporary cache directory for tarball extraction."""
    cache_dir = tmp_path / "registry-cache"
    cache_dir.mkdir(parents=True)
    return cache_dir


# =============================================================================
# Test Class: Sync -> MinIO Integration
# =============================================================================


@pytest.mark.integration
class TestSyncv2MinioIntegration:
    """Tests for Syncv2 -> MinIO tarball upload."""

    @pytest.mark.anyio
    async def test_sync_builtin_registry_uploads_tarball(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        minio_client: Minio,
        test_bucket: str,
        builtin_repo,
    ):
        """Verify that syncing builtin registry uploads tarball to MinIO.

        This test:
        1. Syncs the builtin tracecat_registry
        2. Verifies a RegistryVersion is created with tarball_uri
        3. Verifies the tarball object exists in MinIO
        """
        # Sync the builtin registry
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            result = await sync_service.sync_repository_v2(builtin_repo)

        # Verify sync result
        assert result.version is not None
        assert result.tarball_uri is not None
        assert result.tarball_uri.startswith("s3://")
        assert result.num_actions > 0

        # Extract bucket and key from tarball_uri
        # Format: s3://bucket/key
        uri_parts = result.tarball_uri.replace("s3://", "").split("/", 1)
        bucket = uri_parts[0]
        key = uri_parts[1]

        # Verify tarball exists in MinIO
        try:
            stat = minio_client.stat_object(bucket, key)
            assert stat.size is not None and stat.size > 0
            assert stat.content_type in (
                "application/gzip",
                "application/x-gzip",
                "application/octet-stream",
            )
        except Exception as e:
            pytest.fail(f"Tarball not found in MinIO: {e}")

    @pytest.mark.anyio
    async def test_sync_creates_registry_version_with_manifest(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
    ):
        """Verify that sync creates RegistryVersion with proper manifest."""
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            result = await sync_service.sync_repository_v2(builtin_repo)

        # Verify version record
        version = result.version
        assert version.id is not None
        assert version.version is not None
        assert version.tarball_uri is not None
        assert version.manifest is not None

        # Verify manifest has actions
        manifest = version.manifest
        assert "actions" in manifest
        assert len(manifest["actions"]) > 0

        # Manifest actions are keyed by full action name (e.g., "core.transform.reshape")
        action_names = list(manifest["actions"].keys())
        assert "core.transform.reshape" in action_names

    @pytest.mark.anyio
    async def test_sync_idempotent_returns_existing_version(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
    ):
        """Verify that re-syncing same version returns existing record."""
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            # First sync
            result1 = await sync_service.sync_repository_v2(builtin_repo)

            # Second sync with same target version
            result2 = await sync_service.sync_repository_v2(
                builtin_repo,
                target_version=result1.version.version,
            )

        # Should return same version
        assert result1.version.id == result2.version.id
        assert result1.tarball_uri == result2.tarball_uri


# =============================================================================
# Test Class: Execute Action with Synced Registry
# =============================================================================


@pytest.mark.integration
class TestExecuteWithSyncedRegistry:
    """Tests for action execution using synced registry tarballs.

    Note: These tests mock the artifact resolution functions because:
    1. The EphemeralBackend internally calls get_registry_artifacts_cached/for_lock
    2. Those functions use their own DB sessions (can't see uncommitted data)
    3. Mocking separates concerns: sync tests verify DB, execution tests verify execution
    """

    @pytest.mark.anyio
    async def test_action_runner_downloads_and_extracts_tarball(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        minio_client: Minio,
        test_bucket: str,
        builtin_repo,
        temp_cache_dir: Path,
    ):
        """Verify ActionRunner downloads tarball from MinIO and extracts it."""
        # Sync to create tarball in MinIO
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        tarball_uri = sync_result.tarball_uri

        # Create ActionRunner with test cache dir
        runner = ActionRunner(cache_dir=temp_cache_dir)
        cache_key = runner.compute_tarball_cache_key(tarball_uri)

        # Download and extract tarball
        extracted_path = await runner.ensure_tarball_extracted(cache_key, tarball_uri)

        # Verify extraction
        assert extracted_path.exists()
        assert extracted_path.is_dir()

        # Should have Python packages extracted
        # The tarball contains site-packages content
        files = list(extracted_path.rglob("*"))
        assert len(files) > 0

    @pytest.mark.anyio
    async def test_execute_action_with_ephemeral_backend(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
        run_action_input_factory,
        temp_cache_dir: Path,
        monkeypatch,
    ):
        """Verify action execution through EphemeralBackend with synced registry.

        Note: This test runs with TRACECAT__DISABLE_NSJAIL=true to use subprocess
        mode instead of real nsjail, making it runnable on macOS/CI.
        We mock the ActionRunner's execute_action to use force_sandbox=False.
        """
        # Sync registry to create tarball in MinIO
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        # Create mock artifacts from sync result
        mock_artifacts = [
            RegistryArtifactsContext(
                origin=DEFAULT_REGISTRY_ORIGIN,
                version=sync_result.version.version,
                tarball_uri=sync_result.tarball_uri,
            )
        ]

        # Create input for core.transform.reshape action
        input_data = run_action_input_factory(
            action="core.transform.reshape",
            args={"value": {"input": "test_value"}},
        )

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing (nsjail not available on macOS)
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False  # Override to use direct subprocess
            return await original_execute(**kwargs)

        # Mock both the action runner and artifact resolution
        with (
            patch(
                "tracecat.executor.backends.ephemeral.get_action_runner",
                return_value=test_runner,
            ),
            patch.object(
                test_runner,
                "execute_action",
                side_effect=execute_without_nsjail,
            ),
            patch(
                "tracecat.executor.backends.ephemeral.get_registry_artifacts_cached",
                new_callable=AsyncMock,
                return_value=mock_artifacts,
            ),
        ):
            result = await backend.execute(input_data, test_role, timeout=60.0)

        # Verify execution succeeded
        assert isinstance(result, ExecutorResultSuccess), (
            f"Expected success, got: {result}"
        )
        assert result.result is not None
        assert result.result == {"input": "test_value"}

    @pytest.mark.anyio
    async def test_execute_action_with_registry_lock(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
        run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify action execution respects registry_lock for version pinning."""
        # Sync registry to create version
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        # Create mock artifacts for the locked version
        mock_artifacts = [
            RegistryArtifactsContext(
                origin=DEFAULT_REGISTRY_ORIGIN,
                version=sync_result.version.version,
                tarball_uri=sync_result.tarball_uri,
            )
        ]

        # Create input with registry lock
        registry_lock = {"tracecat_registry": sync_result.version.version}
        input_data = run_action_input_factory(
            action="core.transform.reshape",
            args={"value": {"locked": True}},
            registry_lock=registry_lock,
        )

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False
            return await original_execute(**kwargs)

        with (
            patch(
                "tracecat.executor.backends.ephemeral.get_action_runner",
                return_value=test_runner,
            ),
            patch.object(
                test_runner,
                "execute_action",
                side_effect=execute_without_nsjail,
            ),
            patch(
                "tracecat.executor.backends.ephemeral.get_registry_artifacts_for_lock",
                new_callable=AsyncMock,
                return_value=mock_artifacts,
            ),
        ):
            result = await backend.execute(input_data, test_role, timeout=60.0)

        # Verify execution succeeded with locked version
        assert isinstance(result, ExecutorResultSuccess), (
            f"Expected success, got: {result}"
        )
        assert result.result == {"locked": True}


# =============================================================================
# Test Class: Failure Scenarios
# =============================================================================


@pytest.mark.integration
class TestFailureScenarios:
    """Tests for critical failure scenarios."""

    @pytest.mark.anyio
    async def test_execute_raises_when_tarball_missing(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        minio_client: Minio,
        test_bucket: str,
        builtin_repo,
        run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify execution raises an error when tarball is missing from MinIO.

        Note: Currently the executor raises HTTPStatusError when the tarball
        download fails. A future improvement could convert this to a graceful
        ExecutorResultFailure.
        """
        import httpx

        # Sync registry
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        # Delete the tarball from MinIO
        uri_parts = sync_result.tarball_uri.replace("s3://", "").split("/", 1)
        bucket = uri_parts[0]
        key = uri_parts[1]
        minio_client.remove_object(bucket, key)

        # Create mock artifacts pointing to the deleted tarball
        mock_artifacts = [
            RegistryArtifactsContext(
                origin=DEFAULT_REGISTRY_ORIGIN,
                version=sync_result.version.version,
                tarball_uri=sync_result.tarball_uri,  # Points to deleted object
            )
        ]

        # Create input
        input_data = run_action_input_factory()

        # Execute should fail
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False
            return await original_execute(**kwargs)

        with (
            patch(
                "tracecat.executor.backends.ephemeral.get_action_runner",
                return_value=test_runner,
            ),
            patch.object(
                test_runner,
                "execute_action",
                side_effect=execute_without_nsjail,
            ),
            patch(
                "tracecat.executor.backends.ephemeral.get_registry_artifacts_cached",
                new_callable=AsyncMock,
                return_value=mock_artifacts,
            ),
        ):
            # Currently raises HTTPStatusError for missing tarball
            # A future improvement could convert this to ExecutorResultFailure
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await backend.execute(input_data, test_role, timeout=30.0)

            assert exc_info.value.response.status_code == 404

    @pytest.mark.anyio
    async def test_registry_lock_fails_on_nonexistent_version(
        self,
        test_role: Role,
    ):
        """Verify registry lock resolution fails for non-existent version."""
        # Try to resolve non-existent version
        registry_lock = {"tracecat_registry": "nonexistent-version-12345"}

        artifacts = await get_registry_artifacts_for_lock(registry_lock)

        # Should return empty list (version not found)
        assert len(artifacts) == 0


# =============================================================================
# Test Class: Multitenant Workloads
# =============================================================================


@pytest.mark.integration
class TestMultitenantWorkloads:
    """Tests for multitenant workload handling.

    These tests verify that the executor correctly isolates workloads
    between different workspaces/tenants when using the EphemeralBackend.
    """

    @pytest.fixture
    def create_workspace_role(self):
        """Factory for creating roles with different workspace IDs."""

        def _create(workspace_id: uuid.UUID | None = None) -> Role:
            return Role(
                type="service",
                service_id="tracecat-runner",
                workspace_id=workspace_id or uuid.uuid4(),
                organization_id=config.TRACECAT__DEFAULT_ORG_ID,
                user_id=uuid.UUID("00000000-0000-4444-aaaa-000000000000"),
            )

        return _create

    @pytest.fixture
    def create_run_input(self):
        """Factory for creating RunActionInput with workspace-specific values."""

        def _create(
            workspace_id: uuid.UUID,
            value: dict,
            registry_lock: dict[str, str] | None = None,
        ) -> RunActionInput:
            wf_id = WorkflowUUID.new_uuid4()
            return RunActionInput(
                task=ActionStatement(
                    action="core.transform.reshape",
                    args={"value": value},
                    ref="test_action",
                ),
                exec_context={},
                run_context=RunContext(
                    wf_id=wf_id,
                    wf_exec_id=f"{wf_id.short()}/exec_test",
                    wf_run_id=uuid.uuid4(),
                    environment="default",
                ),
                registry_lock=registry_lock,
            )

        return _create

    @pytest.mark.anyio
    async def test_concurrent_execution_different_workspaces(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
        create_workspace_role,
        create_run_input,
        temp_cache_dir: Path,
    ):
        """Verify concurrent execution from different workspaces is isolated.

        This test:
        1. Creates two different workspace roles
        2. Syncs registry to create shared tarball in MinIO
        3. Executes actions concurrently from both workspaces
        4. Verifies each workspace gets correct results without cross-contamination
        """
        import asyncio

        # Sync registry to create tarball in MinIO
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        # Create two different workspace roles
        workspace_a_id = uuid.uuid4()
        workspace_b_id = uuid.uuid4()
        role_a = create_workspace_role(workspace_a_id)
        role_b = create_workspace_role(workspace_b_id)

        # Create inputs with workspace-specific values
        input_a = create_run_input(
            workspace_id=workspace_a_id,
            value={"workspace": "A", "tenant_id": str(workspace_a_id)},
        )
        input_b = create_run_input(
            workspace_id=workspace_b_id,
            value={"workspace": "B", "tenant_id": str(workspace_b_id)},
        )

        # Create mock artifacts for both workspaces
        mock_artifacts = [
            RegistryArtifactsContext(
                origin=DEFAULT_REGISTRY_ORIGIN,
                version=sync_result.version.version,
                tarball_uri=sync_result.tarball_uri,
            )
        ]

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False
            return await original_execute(**kwargs)

        # Execute both workspaces concurrently
        with (
            patch(
                "tracecat.executor.backends.ephemeral.get_action_runner",
                return_value=test_runner,
            ),
            patch.object(
                test_runner,
                "execute_action",
                side_effect=execute_without_nsjail,
            ),
            patch(
                "tracecat.executor.backends.ephemeral.get_registry_artifacts_cached",
                new_callable=AsyncMock,
                return_value=mock_artifacts,
            ),
        ):
            # Run both executions concurrently
            result_a, result_b = await asyncio.gather(
                backend.execute(input_a, role_a, timeout=60.0),
                backend.execute(input_b, role_b, timeout=60.0),
            )

        # Verify both succeeded
        assert isinstance(result_a, ExecutorResultSuccess), (
            f"Workspace A failed: {result_a}"
        )
        assert isinstance(result_b, ExecutorResultSuccess), (
            f"Workspace B failed: {result_b}"
        )

        # Verify each workspace got its own result (no cross-contamination)
        assert result_a.result["workspace"] == "A"
        assert result_a.result["tenant_id"] == str(workspace_a_id)
        assert result_b.result["workspace"] == "B"
        assert result_b.result["tenant_id"] == str(workspace_b_id)

    @pytest.mark.anyio
    async def test_multiple_concurrent_requests_same_workspace(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
        create_run_input,
        temp_cache_dir: Path,
    ):
        """Verify multiple concurrent requests from same workspace work correctly.

        This tests the scenario where a single tenant has multiple workflows
        running in parallel, ensuring they all complete without interference.
        """
        import asyncio

        # Sync registry to create tarball in MinIO
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        # Create multiple inputs for the same workspace
        workspace_id = test_role.workspace_id
        inputs = [
            create_run_input(
                workspace_id=workspace_id,
                value={"request_id": i, "workspace": str(workspace_id)},
            )
            for i in range(5)
        ]

        # Create mock artifacts
        mock_artifacts = [
            RegistryArtifactsContext(
                origin=DEFAULT_REGISTRY_ORIGIN,
                version=sync_result.version.version,
                tarball_uri=sync_result.tarball_uri,
            )
        ]

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False
            return await original_execute(**kwargs)

        # Execute all requests concurrently
        with (
            patch(
                "tracecat.executor.backends.ephemeral.get_action_runner",
                return_value=test_runner,
            ),
            patch.object(
                test_runner,
                "execute_action",
                side_effect=execute_without_nsjail,
            ),
            patch(
                "tracecat.executor.backends.ephemeral.get_registry_artifacts_cached",
                new_callable=AsyncMock,
                return_value=mock_artifacts,
            ),
        ):
            results = await asyncio.gather(
                *[backend.execute(inp, test_role, timeout=60.0) for inp in inputs]
            )

        # Verify all succeeded
        for i, result in enumerate(results):
            assert isinstance(result, ExecutorResultSuccess), (
                f"Request {i} failed: {result}"
            )
            assert result.result["request_id"] == i
            assert result.result["workspace"] == str(workspace_id)

    @pytest.mark.anyio
    async def test_workspace_specific_registry_locks(
        self,
        session: AsyncSession,
        test_role: Role,
        minio_server,
        test_bucket: str,
        builtin_repo,
        create_workspace_role,
        create_run_input,
        temp_cache_dir: Path,
    ):
        """Verify different workspaces can use different registry versions via locks.

        This tests that registry_lock properly pins versions per workflow,
        allowing different tenants to use different registry versions.
        """
        import asyncio

        # Sync registry to create tarball in MinIO
        async with RegistrySyncService.with_session(
            session=session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(builtin_repo)

        # Create two different workspace roles
        workspace_a_id = uuid.uuid4()
        workspace_b_id = uuid.uuid4()
        role_a = create_workspace_role(workspace_a_id)
        role_b = create_workspace_role(workspace_b_id)

        # Both use the same version via registry lock (simulating pinned deployments)
        registry_lock = {"tracecat_registry": sync_result.version.version}

        # Create inputs with registry locks
        input_a = create_run_input(
            workspace_id=workspace_a_id,
            value={"workspace": "A", "locked_version": sync_result.version.version},
            registry_lock=registry_lock,
        )
        input_b = create_run_input(
            workspace_id=workspace_b_id,
            value={"workspace": "B", "locked_version": sync_result.version.version},
            registry_lock=registry_lock,
        )

        # Create mock artifacts for locked version
        mock_artifacts = [
            RegistryArtifactsContext(
                origin=DEFAULT_REGISTRY_ORIGIN,
                version=sync_result.version.version,
                tarball_uri=sync_result.tarball_uri,
            )
        ]

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False
            return await original_execute(**kwargs)

        # Execute both workspaces concurrently with locked versions
        with (
            patch(
                "tracecat.executor.backends.ephemeral.get_action_runner",
                return_value=test_runner,
            ),
            patch.object(
                test_runner,
                "execute_action",
                side_effect=execute_without_nsjail,
            ),
            patch(
                "tracecat.executor.backends.ephemeral.get_registry_artifacts_for_lock",
                new_callable=AsyncMock,
                return_value=mock_artifacts,
            ),
        ):
            result_a, result_b = await asyncio.gather(
                backend.execute(input_a, role_a, timeout=60.0),
                backend.execute(input_b, role_b, timeout=60.0),
            )

        # Verify both succeeded with locked version
        assert isinstance(result_a, ExecutorResultSuccess), (
            f"Workspace A failed: {result_a}"
        )
        assert isinstance(result_b, ExecutorResultSuccess), (
            f"Workspace B failed: {result_b}"
        )

        # Verify each got correct result with locked version info
        assert result_a.result["workspace"] == "A"
        assert result_a.result["locked_version"] == sync_result.version.version
        assert result_b.result["workspace"] == "B"
        assert result_b.result["locked_version"] == sync_result.version.version
