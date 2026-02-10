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
import os
import uuid
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from temporalio.worker import Worker

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import (
    Organization,
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.dsl.schemas import (
    ActionStatement,
    ExecutionContext,
    RunActionInput,
    RunContext,
)
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.executor.action_runner import ActionRunner
from tracecat.executor.backends.ephemeral import EphemeralBackend
from tracecat.executor.schemas import (
    ActionImplementation,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.service import (
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.repositories.platform_service import PlatformRegistryReposService
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.sync.platform_service import PlatformRegistrySyncService
from tracecat.registry.sync.service import RegistrySyncService
from tracecat.registry.sync.workflow import (
    RegistrySyncActivities,
    RegistrySyncWorkflow,
)
from tracecat.storage import blob

# =============================================================================
# Test Configuration
# =============================================================================

# MinIO test configuration - uses docker-compose services (port 9000)
# Credentials are read from env to match docker-compose/.env in CI and local dev.
MINIO_ENDPOINT = "localhost:9000"
TEST_BUCKET = "test-tracecat-registry"


def _minio_credentials() -> tuple[str, str]:
    load_dotenv()
    access_key = (
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minio"
    )
    secret_key = (
        os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "password"
    )
    return access_key, secret_key


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module")
def anyio_backend():
    """Module-scoped anyio backend."""
    return "asyncio"


# Static IDs used by module-scoped fixtures
STATIC_WORKSPACE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
STATIC_ORGANIZATION_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(scope="module")
async def module_organization(db, module_committing_session: AsyncSession):
    """Create a static organization for module-scoped fixtures.

    This organization must exist before any RegistryRepository can be created
    due to FK constraints on organization_id.
    """
    # Check if organization exists
    result = await module_committing_session.execute(
        select(Organization).where(Organization.id == STATIC_ORGANIZATION_ID)
    )
    org = result.scalar_one_or_none()
    if org is None:
        # Create the organization
        org = Organization(
            id=STATIC_ORGANIZATION_ID,
            name="Test Organization (Module)",
            slug=f"test-org-module-{STATIC_ORGANIZATION_ID.hex[:8]}",
            is_active=True,
        )
        module_committing_session.add(org)
        await module_committing_session.commit()
        await module_committing_session.refresh(org)
    return org


@pytest.fixture(scope="module")
def module_test_role(mock_org_id) -> Role:
    """Module-scoped role for shared fixtures.

    Creates a static role that can be used by module-scoped fixtures
    without depending on function-scoped test_workspace.
    """
    return Role(
        type="service",
        user_id=mock_org_id,
        workspace_id=STATIC_WORKSPACE_ID,
        organization_id=STATIC_ORGANIZATION_ID,
        service_id="tracecat-runner",
    )


@pytest.fixture
async def committing_session(db) -> AsyncGenerator[AsyncSession, None]:
    """Create a session that makes real commits (not savepoints).

    This fixture is needed for tests that spawn subprocesses that need to
    read data from the database. The standard `session` fixture uses savepoint
    mode which prevents subprocesses from seeing the data.

    IMPORTANT: This fixture makes real commits to the test database.
    Data cleanup is handled by the test database fixture at session end.
    """
    async_engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        poolclass=NullPool,
    )

    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session

    await async_engine.dispose()


@pytest.fixture
def subprocess_db_env(monkeypatch):
    """Configure environment for subprocesses to connect to test database.

    The subprocess inherits environment variables, so we need to set
    TRACECAT__DB_URI to point to the test database instead of the main
    postgres database.

    Also resets the cached async engine so that _prepare_resolved_context
    can create a new session that connects to the test database.
    """
    import tracecat.db.engine as engine_module

    # Convert asyncpg URL to psycopg URL for subprocess
    test_db_url = TEST_DB_CONFIG.test_url.replace("+asyncpg", "+psycopg")
    monkeypatch.setenv("TRACECAT__DB_URI", test_db_url)
    monkeypatch.setattr(config, "TRACECAT__DB_URI", test_db_url)

    # Reset the cached async engine so new sessions use the test database
    # This is needed because get_async_engine() caches the engine globally
    old_engine = engine_module._async_engine
    engine_module._async_engine = None

    yield

    # Restore the original engine
    engine_module._async_engine = old_engine


@pytest.fixture
def minio_client(minio_server) -> Minio:
    """Create MinIO client using docker-compose service endpoint."""
    access_key, secret_key = _minio_credentials()
    return Minio(
        MINIO_ENDPOINT,
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
    )


@pytest.fixture(autouse=True)
def configure_minio_for_tests(monkeypatch):
    """Configure blob storage to use test MinIO instance."""
    access_key, secret_key = _minio_credentials()
    monkeypatch.setattr(
        config, "TRACECAT__BLOB_STORAGE_ENDPOINT", f"http://{MINIO_ENDPOINT}"
    )
    monkeypatch.setattr(config, "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", TEST_BUCKET)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", access_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret_key)

    # Disable nsjail for all tests (use subprocess mode for macOS/CI)
    monkeypatch.setenv("TRACECAT__DISABLE_NSJAIL", "true")
    monkeypatch.setattr(config, "TRACECAT__DISABLE_NSJAIL", True)
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_SANDBOX_ENABLED", False)

    # Disable registry sync sandbox to use subprocess mode instead of Temporal workflow.
    # This ensures sync activities run in the same process and see the monkeypatched config.
    monkeypatch.setattr(config, "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", False)

    # Reload blob module to pick up new config
    importlib.reload(blob)


@pytest.fixture(scope="module", autouse=True)
async def registry_sync_worker(temporal_client):
    """Start a Temporal worker for registry sync on the test executor queue."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            temporal_client,
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            workflows=[RegistrySyncWorkflow],
            activities=RegistrySyncActivities.get_activities(),
            activity_executor=executor,
            workflow_runner=new_sandbox_runner(),
        ):
            yield


@pytest.fixture
async def test_bucket(minio_client: Minio, mock_org_id: uuid.UUID):
    """Create test bucket for registry tarballs.

    Uses worker-specific org ID for cleanup to avoid conflicts in parallel test execution.
    Each xdist worker has a unique mock_org_id, so cleanup only removes that worker's objects.
    """
    bucket_name = TEST_BUCKET

    # Create bucket if it doesn't exist.
    # Catch S3Error explicitly so failures surface cleanly instead of getting
    # masked by FrozenInstanceError in async generator fixture handling.
    try:
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
    except S3Error as e:
        pytest.fail(f"Failed to ensure test MinIO bucket '{bucket_name}': {e}")

    yield bucket_name

    # Cleanup: only remove objects with this worker's org ID prefix
    # This avoids race conditions when multiple xdist workers share the bucket
    org_prefix = str(mock_org_id)
    try:
        objects = minio_client.list_objects(
            bucket_name, prefix=org_prefix, recursive=True
        )
        for obj in objects:
            if obj.object_name:
                minio_client.remove_object(bucket_name, obj.object_name)
    except Exception:
        pass  # Ignore cleanup errors


@pytest.fixture
async def builtin_repo(
    db, session: AsyncSession, test_role: Role, session_test_organization
):
    """Create builtin registry repository for testing.

    Note: depends on `db` fixture to ensure test database is created.
    Uses the test session directly to ensure transaction visibility.
    Depends on session_test_organization to satisfy FK constraint on organization_id.
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
async def committing_builtin_repo(
    db, committing_session: AsyncSession, test_role: Role, test_organization
):
    """Create builtin registry repository with committing session.

    This fixture is for tests that spawn subprocesses that need to see
    the repository data. It uses committing_session to make real commits
    that the subprocess can read.
    Depends on test_organization to satisfy FK constraint on organization_id.
    """
    svc = RegistryReposService(committing_session, role=test_role)
    # Get existing or create new builtin repository
    repo = await svc.get_repository(DEFAULT_REGISTRY_ORIGIN)
    if repo is None:
        repo = await svc.create_repository(
            RegistryRepositoryCreate(origin=DEFAULT_REGISTRY_ORIGIN)
        )
    await committing_session.commit()
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
        # Build proper RegistryLock with origins and action mappings
        origins = registry_lock or {"tracecat_registry": "test-version"}
        actions = {action: list(origins.keys())[0]}
        return RunActionInput(
            task=ActionStatement(
                action=action,
                args=args or {"value": {"test": True}},
                ref="test_action",
            ),
            exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
            run_context=RunContext(
                wf_id=wf_id,
                wf_exec_id=f"{wf_id.short()}/exec_test",
                wf_run_id=uuid.uuid4(),
                environment="default",
                logical_time=datetime.now(UTC),
            ),
            registry_lock=RegistryLock(origins=origins, actions=actions),
        )

    return _create


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create temporary cache directory for tarball extraction."""
    cache_dir = tmp_path / "registry-cache"
    cache_dir.mkdir(parents=True)
    return cache_dir


@pytest.fixture
def unique_version() -> str:
    """Generate a unique version string for each test.

    This ensures each test gets its own registry version and tarball,
    avoiding issues with bucket cleanup removing tarballs while DB
    records persist.
    """
    import tracecat_registry

    return f"{tracecat_registry.__version__}-{uuid.uuid4().hex[:8]}"


# =============================================================================
# Module-scoped fixtures for shared sync (optimization)
# =============================================================================


@pytest.fixture(scope="module")
def module_unique_version() -> str:
    """Generate a unique version string once per module.

    This allows tests to share a single synced registry version,
    avoiding redundant 15-30s sync operations per test.
    """
    import tracecat_registry

    return f"{tracecat_registry.__version__}-module-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
async def module_committing_session(db) -> AsyncGenerator[AsyncSession, None]:
    """Module-scoped committing session for shared fixtures.

    Creates a single session that persists across the module for
    setting up shared test data (like synced registry).
    """
    async_engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        poolclass=NullPool,
    )

    async with AsyncSession(async_engine, expire_on_commit=False) as session:
        yield session

    await async_engine.dispose()


@pytest.fixture(scope="module")
async def module_builtin_repo(
    db,
    module_committing_session: AsyncSession,
    module_test_role: Role,
    module_organization,  # Ensures organization exists before creating repos
):
    """Module-scoped builtin repository for shared sync.

    Creates BOTH org-scoped and platform-scoped repositories:
    - Org repo: Used by RegistrySyncService for version/index sync
    - Platform repo: Used by executor for tarball lookups (via UNION ALL)

    Depends on module_organization to satisfy FK constraint on organization_id.
    """
    # Create org-scoped repo
    org_svc = RegistryReposService(module_committing_session, role=module_test_role)
    org_repo = await org_svc.get_repository(DEFAULT_REGISTRY_ORIGIN)
    if org_repo is None:
        org_repo = await org_svc.create_repository(
            RegistryRepositoryCreate(origin=DEFAULT_REGISTRY_ORIGIN)
        )

    # Also create platform-scoped repo (for executor tarball lookups)
    platform_svc = PlatformRegistryReposService(module_committing_session)
    await platform_svc.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)

    await module_committing_session.commit()
    yield org_repo  # Return org repo for version/index sync


@pytest.fixture(scope="module")
async def shared_synced_registry(
    db,
    minio_server,
    module_committing_session: AsyncSession,
    module_builtin_repo,
    module_test_role: Role,
    module_unique_version: str,
):
    """Module-scoped synced registry to avoid redundant sync operations.

    This fixture syncs the registry ONCE per module and shares the result
    across all tests that need it. This saves ~15-30 seconds per test that
    would otherwise call sync_repository_v2.

    Syncs to BOTH org and platform tables:
    - Org sync: For org-scoped tests (version + index tables)
    - Platform sync: For executor tarball lookups (uses UNION ALL)

    Returns a dict with:
    - sync_result: The RegistrySyncResult from sync_repository_v2
    - version: The version string
    - tarball_uri: The S3 URI of the tarball
    """
    # Sync to org tables first (original behavior)
    async with RegistrySyncService.with_session(
        session=module_committing_session, role=module_test_role
    ) as org_sync_service:
        sync_result = await org_sync_service.sync_repository_v2(
            module_builtin_repo, target_version=module_unique_version
        )

    # Also sync to platform tables (executor queries platform tables for tracecat_registry)
    platform_svc = PlatformRegistryReposService(module_committing_session)
    platform_repo = await platform_svc.get_or_create_repository(DEFAULT_REGISTRY_ORIGIN)
    platform_sync_service = PlatformRegistrySyncService(module_committing_session)
    await platform_sync_service.sync_repository_v2(
        platform_repo,
        target_version=module_unique_version,
        bypass_temporal=True,
    )

    await module_committing_session.commit()

    yield {
        "sync_result": sync_result,
        "version": sync_result.version.version,
        "tarball_uri": sync_result.tarball_uri,
    }


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
    1. The EphemeralBackend internally calls get_registry_artifacts_for_lock
    2. Those functions use their own DB sessions (can't see uncommitted data)
    3. Mocking separates concerns: sync tests verify DB, execution tests verify execution
    """

    @pytest.mark.anyio
    async def test_action_runner_downloads_and_extracts_tarball(
        self,
        shared_synced_registry: dict,
        temp_cache_dir: Path,
    ):
        """Verify ActionRunner downloads tarball from MinIO and extracts it."""
        # Use shared synced registry (avoids redundant 15-30s sync)
        tarball_uri = shared_synced_registry["tarball_uri"]

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
        subprocess_db_env,
        test_role: Role,
        shared_synced_registry: dict,
        run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify action execution through EphemeralBackend with synced registry.

        Note: This test runs with TRACECAT__DISABLE_NSJAIL=true to use subprocess
        mode instead of real nsjail, making it runnable on macOS/CI.
        We mock the ActionRunner's execute_action to use force_sandbox=False.

        Uses shared_synced_registry fixture (module-scoped) to avoid redundant sync.
        """
        # Use shared synced registry (avoids redundant 15-30s sync)
        sync_result = shared_synced_registry["sync_result"]

        # Create input for core.transform.reshape action
        args = {"value": {"input": "test_value"}}
        input_data = run_action_input_factory(
            action="core.transform.reshape",
            args=args,
            registry_lock={"tracecat_registry": sync_result.version.version},
        )

        # Create resolved context for execution
        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args=args,
            workspace_id=str(test_role.workspace_id),
            workflow_id=str(input_data.run_context.wf_id),
            run_id=str(input_data.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
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
        ):
            result = await backend.execute(
                input_data, test_role, resolved_context=resolved_context, timeout=60.0
            )

        # Verify execution succeeded
        assert isinstance(result, ExecutorResultSuccess), (
            f"Expected success, got: {result}"
        )
        assert result.result is not None
        assert result.result == {"input": "test_value"}

    @pytest.mark.anyio
    async def test_execute_action_with_registry_lock(
        self,
        subprocess_db_env,
        test_role: Role,
        shared_synced_registry: dict,
        run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify action execution respects registry_lock for version pinning.

        Uses shared_synced_registry fixture (module-scoped) to avoid redundant sync.
        """
        # Use shared synced registry (avoids redundant 15-30s sync)
        sync_result = shared_synced_registry["sync_result"]

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
        args = {"value": {"locked": True}}
        input_data = run_action_input_factory(
            action="core.transform.reshape",
            args=args,
            registry_lock=registry_lock,
        )

        # Create resolved context for execution
        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args=args,
            workspace_id=str(test_role.workspace_id),
            workflow_id=str(input_data.run_context.wf_id),
            run_id=str(input_data.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
        )

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False  # Override to use direct subprocess
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
            result = await backend.execute(
                input_data, test_role, resolved_context=resolved_context, timeout=60.0
            )

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
        subprocess_db_env,
        committing_session: AsyncSession,
        test_role: Role,
        minio_server,
        minio_client: Minio,
        test_bucket: str,
        committing_builtin_repo,
        unique_version: str,
        run_action_input_factory,
        temp_cache_dir: Path,
    ):
        """Verify execution raises an error when tarball is missing from MinIO.

        Note: Currently the executor raises HTTPStatusError when the tarball
        download fails. A future improvement could convert this to a graceful
        ExecutorResultFailure.
        """
        import httpx

        # Sync registry to org-scoped tables
        async with RegistrySyncService.with_session(
            session=committing_session, role=test_role
        ) as sync_service:
            sync_result = await sync_service.sync_repository_v2(
                committing_builtin_repo,
                target_version=unique_version,
            )

        # Sync the same version to platform-scoped tables.
        # Executor lock resolution routes tracecat_registry to platform tables.
        platform_svc = PlatformRegistryReposService(committing_session)
        platform_repo = await platform_svc.get_or_create_repository(
            DEFAULT_REGISTRY_ORIGIN
        )
        platform_sync_service = PlatformRegistrySyncService(committing_session)
        await platform_sync_service.sync_repository_v2(
            platform_repo,
            target_version=unique_version,
            bypass_temporal=True,
        )
        await committing_session.commit()

        # Create input
        args = {"value": {"test": True}}
        input_data = run_action_input_factory(
            args=args,
            registry_lock={"tracecat_registry": sync_result.version.version},
        )

        # Create resolved context for execution
        resolved_context = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args=args,
            workspace_id=str(test_role.workspace_id),
            workflow_id=str(input_data.run_context.wf_id),
            run_id=str(input_data.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
        )

        # Resolve and delete the exact tarball URI(s) the executor will use.
        # For tracecat_registry, this may resolve to platform-scoped artifacts.
        backend = EphemeralBackend()
        tarball_uris = await backend._get_tarball_uris(input_data, test_role)
        assert tarball_uris, (
            "Expected resolved tarball URIs for locked registry version"
        )
        for tarball_uri in tarball_uris:
            uri_parts = tarball_uri.replace("s3://", "").split("/", 1)
            bucket = uri_parts[0]
            key = uri_parts[1]
            minio_client.remove_object(bucket, key)

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
        ):
            # Currently raises HTTPStatusError for missing tarball
            # A future improvement could convert this to ExecutorResultFailure
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await backend.execute(
                    input_data,
                    test_role,
                    resolved_context=resolved_context,
                    timeout=30.0,
                )

            assert exc_info.value.response.status_code == 404

    @pytest.mark.anyio
    async def test_registry_lock_fails_on_nonexistent_version(
        self,
        test_role: Role,
    ):
        """Verify registry lock resolution fails for non-existent version."""
        # Ensure organization_id is set
        assert test_role.organization_id is not None

        # Try to resolve non-existent version
        registry_lock = {"tracecat_registry": "nonexistent-version-12345"}

        artifacts = await get_registry_artifacts_for_lock(
            registry_lock, organization_id=test_role.organization_id
        )

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
                organization_id=uuid.uuid4(),
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
            action = "core.transform.reshape"
            # Build proper RegistryLock with origins and action mappings
            origins = registry_lock or {"tracecat_registry": "test-version"}
            actions = {action: list(origins.keys())[0]}
            return RunActionInput(
                task=ActionStatement(
                    action=action,
                    args={"value": value},
                    ref="test_action",
                ),
                exec_context=ExecutionContext(ACTIONS={}, TRIGGER=None),
                run_context=RunContext(
                    wf_id=wf_id,
                    wf_exec_id=f"{wf_id.short()}/exec_test",
                    wf_run_id=uuid.uuid4(),
                    environment="default",
                    logical_time=datetime.now(UTC),
                ),
                registry_lock=RegistryLock(origins=origins, actions=actions),
            )

        return _create

    @pytest.mark.anyio
    async def test_concurrent_execution_different_workspaces(
        self,
        subprocess_db_env,
        shared_synced_registry: dict,
        create_workspace_role,
        create_run_input,
        temp_cache_dir: Path,
    ):
        """Verify concurrent execution from different workspaces is isolated.

        This test:
        1. Creates two different workspace roles
        2. Uses shared synced registry (module-scoped) to avoid redundant sync
        3. Executes actions concurrently from both workspaces
        4. Verifies each workspace gets correct results without cross-contamination
        """
        import asyncio

        # Use shared synced registry (avoids redundant 15-30s sync)
        sync_result = shared_synced_registry["sync_result"]

        # Create two different workspace roles
        workspace_a_id = uuid.uuid4()
        workspace_b_id = uuid.uuid4()
        role_a = create_workspace_role(workspace_a_id)
        role_b = create_workspace_role(workspace_b_id)

        # Create inputs with workspace-specific values
        value_a = {"workspace": "A", "tenant_id": str(workspace_a_id)}
        value_b = {"workspace": "B", "tenant_id": str(workspace_b_id)}
        registry_lock = {"tracecat_registry": sync_result.version.version}
        input_a = create_run_input(
            workspace_id=workspace_a_id,
            value=value_a,
            registry_lock=registry_lock,
        )
        input_b = create_run_input(
            workspace_id=workspace_b_id,
            value=value_b,
            registry_lock=registry_lock,
        )

        # Create resolved contexts for both workspaces
        resolved_context_a = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args={"value": value_a},
            workspace_id=str(workspace_a_id),
            workflow_id=str(input_a.run_context.wf_id),
            run_id=str(input_a.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
        )
        resolved_context_b = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args={"value": value_b},
            workspace_id=str(workspace_b_id),
            workflow_id=str(input_b.run_context.wf_id),
            run_id=str(input_b.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
        )

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False  # Override to use direct subprocess
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
        ):
            # Run both executions concurrently
            result_a, result_b = await asyncio.gather(
                backend.execute(
                    input_a, role_a, resolved_context=resolved_context_a, timeout=60.0
                ),
                backend.execute(
                    input_b, role_b, resolved_context=resolved_context_b, timeout=60.0
                ),
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
        subprocess_db_env,
        test_role: Role,
        shared_synced_registry: dict,
        create_run_input,
        temp_cache_dir: Path,
    ):
        """Verify multiple concurrent requests from same workspace work correctly.

        This tests the scenario where a single tenant has multiple workflows
        running in parallel, ensuring they all complete without interference.

        Uses shared_synced_registry fixture (module-scoped) to avoid redundant sync.
        """
        import asyncio

        # Use shared synced registry (avoids redundant 15-30s sync)
        sync_result = shared_synced_registry["sync_result"]

        # Create multiple inputs for the same workspace
        workspace_id = test_role.workspace_id
        registry_lock = {"tracecat_registry": sync_result.version.version}
        inputs_and_contexts = []
        for i in range(5):
            value = {"request_id": i, "workspace": str(workspace_id)}
            inp = create_run_input(
                workspace_id=workspace_id,
                value=value,
                registry_lock=registry_lock,
            )
            resolved_ctx = ResolvedContext(
                secrets={},
                variables={},
                action_impl=ActionImplementation(
                    type="udf",
                    module="tracecat_registry.core.transform",
                    name="reshape",
                ),
                evaluated_args={"value": value},
                workspace_id=str(workspace_id),
                workflow_id=str(inp.run_context.wf_id),
                run_id=str(inp.run_context.wf_run_id),
                executor_token="mock-token-for-testing",
            )
            inputs_and_contexts.append((inp, resolved_ctx))

        # Execute via EphemeralBackend
        backend = EphemeralBackend()

        # Create test runner
        test_runner = ActionRunner(cache_dir=temp_cache_dir)

        # Wrap execute_action to disable force_sandbox for testing
        original_execute = test_runner.execute_action

        async def execute_without_nsjail(**kwargs):
            kwargs["force_sandbox"] = False  # Override to use direct subprocess
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
        ):
            results = await asyncio.gather(
                *[
                    backend.execute(
                        inp, test_role, resolved_context=resolved_ctx, timeout=60.0
                    )
                    for inp, resolved_ctx in inputs_and_contexts
                ]
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
        subprocess_db_env,
        shared_synced_registry: dict,
        create_workspace_role,
        create_run_input,
        temp_cache_dir: Path,
    ):
        """Verify different workspaces can use different registry versions via locks.

        This tests that registry_lock properly pins versions per workflow,
        allowing different tenants to use different registry versions.

        Uses shared_synced_registry fixture (module-scoped) to avoid redundant sync.
        """
        import asyncio

        # Use shared synced registry (avoids redundant 15-30s sync)
        sync_result = shared_synced_registry["sync_result"]

        # Create two different workspace roles
        workspace_a_id = uuid.uuid4()
        workspace_b_id = uuid.uuid4()
        role_a = create_workspace_role(workspace_a_id)
        role_b = create_workspace_role(workspace_b_id)

        # Both use the same version via registry lock (simulating pinned deployments)
        registry_lock = {"tracecat_registry": sync_result.version.version}

        # Create inputs with registry locks
        value_a = {"workspace": "A", "locked_version": sync_result.version.version}
        value_b = {"workspace": "B", "locked_version": sync_result.version.version}
        input_a = create_run_input(
            workspace_id=workspace_a_id,
            value=value_a,
            registry_lock=registry_lock,
        )
        input_b = create_run_input(
            workspace_id=workspace_b_id,
            value=value_b,
            registry_lock=registry_lock,
        )

        # Create resolved contexts for both workspaces
        resolved_context_a = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args={"value": value_a},
            workspace_id=str(workspace_a_id),
            workflow_id=str(input_a.run_context.wf_id),
            run_id=str(input_a.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
        )
        resolved_context_b = ResolvedContext(
            secrets={},
            variables={},
            action_impl=ActionImplementation(
                type="udf",
                module="tracecat_registry.core.transform",
                name="reshape",
            ),
            evaluated_args={"value": value_b},
            workspace_id=str(workspace_b_id),
            workflow_id=str(input_b.run_context.wf_id),
            run_id=str(input_b.run_context.wf_run_id),
            executor_token="mock-token-for-testing",
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
            kwargs["force_sandbox"] = False  # Override to use direct subprocess
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
                backend.execute(
                    input_a, role_a, resolved_context=resolved_context_a, timeout=60.0
                ),
                backend.execute(
                    input_b, role_b, resolved_context=resolved_context_b, timeout=60.0
                ),
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


# =============================================================================
# Test Class: Version Isolation Artifact Resolution
# =============================================================================


@pytest.mark.integration
class TestVersionIsolationArtifactResolution:
    """Tests for registry artifact resolution with version isolation.

    These tests verify that when multiple registry versions exist in the database,
    the artifact resolution correctly returns the locked version's tarball,
    not the current version's tarball.
    """

    @pytest.mark.anyio
    async def test_registry_artifacts_resolve_locked_version_not_current(
        self,
        subprocess_db_env,
        committing_session: AsyncSession,
        test_role: Role,
    ) -> None:
        """Test that artifact resolution uses locked version, not current version.

        This tests the critical execution path: when a workflow is locked to v1
        but v2 is current, the executor should resolve v1's tarball_uri.

        Uses DEFAULT_REGISTRY_ORIGIN (tracecat_registry) since get_registry_artifacts_for_lock
        only queries platform tables for this origin.
        """
        # Ensure organization_id is set
        assert test_role.organization_id is not None
        organization_id = test_role.organization_id

        # Use unique versions to avoid conflicts with other tests
        V1 = f"isolation-test-v1.0.0-{uuid.uuid4().hex[:8]}"
        V2 = f"isolation-test-v2.0.0-{uuid.uuid4().hex[:8]}"

        # Setup: Get or create platform registry repo
        result = await committing_session.execute(
            select(PlatformRegistryRepository).where(
                PlatformRegistryRepository.origin == DEFAULT_REGISTRY_ORIGIN
            )
        )
        repo = result.scalar_one_or_none()
        if repo is None:
            repo = PlatformRegistryRepository(origin=DEFAULT_REGISTRY_ORIGIN)
            committing_session.add(repo)
            await committing_session.flush()

        # Create v1
        v1 = PlatformRegistryVersion(
            repository_id=repo.id,
            version=V1,
            manifest={"version": V1, "actions": {}},
            tarball_uri=f"s3://{DEFAULT_REGISTRY_ORIGIN}/{V1}.tar.gz",
        )
        committing_session.add(v1)
        await committing_session.flush()

        repo.current_version_id = v1.id
        await committing_session.commit()

        # Create v2 and set as current
        v2 = PlatformRegistryVersion(
            repository_id=repo.id,
            version=V2,
            manifest={"version": V2, "actions": {}},
            tarball_uri=f"s3://{DEFAULT_REGISTRY_ORIGIN}/{V2}.tar.gz",
        )
        committing_session.add(v2)
        await committing_session.flush()

        repo.current_version_id = v2.id  # v2 is now current
        await committing_session.commit()

        # Action: Resolve artifacts for v1's lock (not current)
        artifacts = await get_registry_artifacts_for_lock(
            origins={DEFAULT_REGISTRY_ORIGIN: V1},  # Locked to v1
            organization_id=organization_id,
        )

        # Assertions: Should get v1's tarball, not v2's
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.origin == DEFAULT_REGISTRY_ORIGIN
        assert artifact.version == V1
        assert V1 in artifact.tarball_uri  # v1's tarball
        assert V2 not in artifact.tarball_uri  # NOT v2's tarball
