import asyncio
import importlib
import os
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

# Set workflow return strategy BEFORE importing tracecat modules
# test_workflows.py was written when we returned the full context by default
# This must happen before any tracecat imports to ensure config reads the correct value
os.environ.setdefault("TRACECAT__WORKFLOW_RETURN_STRATEGY", "context")

import aioboto3
import pytest
import redis
import tracecat_registry.integrations.aws_boto3 as boto3_module
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from temporalio.client import Client
from temporalio.worker import Worker

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.enums import OrgRole
from tracecat.contexts import ctx_role
from tracecat.db.engine import (
    get_async_engine,
    get_async_session_context_manager,
    reset_async_engine,
)
from tracecat.db.models import Base, Organization, Workspace
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.plugins import TracecatPydanticAIPlugin
from tracecat.dsl.worker import get_activities, new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.backends import ExecutorBackend
from tracecat.logger import logger
from tracecat.registry.repositories.schemas import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.secrets import secrets_manager
from tracecat.workspaces.service import WorkspaceService

# Test-specific organization ID (not UUID(0) since we removed that default)
# This UUID is deterministic across test runs for fixture seeding
TEST_ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")

# Worker-specific configuration for pytest-xdist parallel execution
# Get xdist worker ID, defaults to "master" if not using xdist
WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "master")

# Generate worker-specific port offsets
# master = 0, gw0 = 0, gw1 = 1, gw2 = 2, etc.
if WORKER_ID == "master":
    WORKER_OFFSET = 0
else:
    # Extract number from "gwN" format
    WORKER_OFFSET = int(WORKER_ID.replace("gw", ""))

# Port configuration - reads from environment for worktree cluster support
# Default ports are for cluster 1, override with PG_PORT, TEMPORAL_PORT, MINIO_PORT, REDIS_PORT
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
TEMPORAL_PORT = int(os.environ.get("TEMPORAL_PORT", "7233"))
MINIO_PORT = int(os.environ.get("MINIO_PORT", "9000"))
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
MINIO_WORKFLOW_BUCKET = "test-tracecat-workflow"

# Worker-specific task queues for pytest-xdist isolation
# Each xdist worker uses different queues to avoid workflow conflicts
TEMPORAL_TASK_QUEUE = f"tracecat-task-queue-{WORKER_ID}"
EXECUTOR_TASK_QUEUE = f"shared-action-queue-{WORKER_ID}"
AGENT_TASK_QUEUE = f"shared-agent-queue-{WORKER_ID}"

# Detect if running inside Docker container by checking for /.dockerenv file
# This is more reliable than checking env vars like REDIS_URL, which may be
# loaded from .env by load_dotenv() even when running on the host
IN_DOCKER = os.path.exists("/.dockerenv")


def _is_temporal_test_run(request: pytest.FixtureRequest) -> bool:
    forced = os.environ.get("TRACECAT__TEST_SUITE")
    if forced is not None:
        return forced.lower() == "temporal"

    session_items = getattr(request.session, "items", None)
    if session_items:
        for item in session_items:
            nodeid = getattr(item, "nodeid", "")
            if "tests/temporal" in nodeid:
                return True

    args = getattr(request.config, "args", None)
    if args:
        return any("tests/temporal" in str(arg) for arg in args)

    return False


def _rewrite_db_host(db_uri: str, *, host: str) -> str:
    parsed = urlparse(db_uri)
    if not parsed.hostname or parsed.hostname != "postgres_db":
        return db_uri

    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"

    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{userinfo}{host}{port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _minio_credentials() -> tuple[str, str]:
    load_dotenv()
    access_key = (
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("MINIO_ROOT_USER")
        or "minioadmin"
    )
    secret_key = (
        os.environ.get("AWS_SECRET_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "minioadmin"
    )
    return access_key, secret_key


def _normalize_db_uri(db_uri: str) -> str:
    return db_uri.replace("+asyncpg", "+psycopg")


def _using_test_db() -> bool:
    return _normalize_db_uri(config.TRACECAT__DB_URI) == TEST_DB_CONFIG.test_url_sync


# ---------------------------------------------------------------------------
# Redis test configuration
# ---------------------------------------------------------------------------

# Worker-specific Redis database number for pytest-xdist isolation
# Each xdist worker uses a different database (0-15) to avoid conflicts
# when multiple workers run tests in parallel
REDIS_DB = WORKER_OFFSET % 16

# Redis URL - use Docker hostname when inside container, localhost otherwise
# Ignore REDIS_URL from .env as it contains Docker-internal hostname
REDIS_HOST = "redis" if IN_DOCKER else "localhost"
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}"


# ---------------------------------------------------------------------------
# Redis fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_server():
    """Verify Redis is available via docker-compose.

    Redis should be started externally via:
    - CI: docker-compose in workflow
    - Local: `just dev` or `docker-compose up`

    Each pytest-xdist worker uses a different Redis database number
    to ensure test isolation during parallel execution.
    """
    # Append worker-specific database number for isolation
    worker_redis_url = f"{REDIS_URL}/{REDIS_DB}"
    client = redis.Redis.from_url(worker_redis_url)
    for _ in range(30):
        try:
            if client.ping():
                logger.info(
                    f"Redis available at {worker_redis_url} (worker={WORKER_ID})"
                )
                # Set REDIS_URL with worker-specific database for isolation
                os.environ["REDIS_URL"] = worker_redis_url
                yield
                return
        except Exception:
            time.sleep(1)

    pytest.fail(
        f"Redis not available at {REDIS_URL}. "
        "Start it with: docker-compose -f docker-compose.dev.yml up -d redis"
    )


@pytest.fixture(autouse=True, scope="function")
def clean_redis_db(redis_server):
    """Flush Redis before every test function to guarantee isolation.

    Uses worker-specific database to avoid affecting other xdist workers.
    """
    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    client.flushdb()
    yield
    client.flushdb()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True, scope="session")
def monkeysession(request: pytest.FixtureRequest):
    mpatch = pytest.MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(autouse=True, scope="function")
async def test_db_engine():
    """Ensure a fresh async engine for each test.

    This fixture creates a new engine for each test function and disposes it
    after the test completes. This ensures connections are properly cleaned up
    and don't hold references to closed event loops when using pytest-xdist.
    """
    engine = get_async_engine()
    try:
        yield engine
    finally:
        try:
            await engine.dispose()
        except Exception as e:
            logger.warning(f"Error disposing engine: {e}")
        finally:
            # Reset the global so next test gets a fresh engine
            reset_async_engine()


@pytest.fixture(autouse=True, scope="session")
def db() -> Iterator[None]:
    """Session-scoped fixture to create and teardown test database using sync SQLAlchemy."""

    default_engine = create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT"
    )

    termination_query = text(
        f"""
        SELECT pg_terminate_backend(pg_stat_activity.pid)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '{TEST_DB_CONFIG.test_db_name}'
        AND pid <> pg_backend_pid();
        """
    )

    test_engine: Any = None
    try:
        with default_engine.connect() as conn:
            # Terminate existing connections
            conn.execute(termination_query)
            # Create test database
            conn.execute(text(f'CREATE DATABASE "{TEST_DB_CONFIG.test_db_name}"'))
            logger.info("Created test database")

        # Create sync engine for test db
        test_engine = create_engine(TEST_DB_CONFIG.test_url_sync)
        with test_engine.begin() as conn:
            logger.info("Creating all tables")
            Base.metadata.create_all(conn)
        yield
    finally:
        if test_engine is not None:
            test_engine.dispose()
        # # Cleanup - reconnect to system db to drop test db
        with default_engine.begin() as conn:
            conn.execute(termination_query)
            conn.execute(
                text(f'DROP DATABASE IF EXISTS "{TEST_DB_CONFIG.test_db_name}"')
            )
        logger.info("Dropped test database")
        default_engine.dispose()


@pytest.fixture(autouse=True, scope="session")
def registry_version_with_manifest(db: None, env_sandbox: None) -> Iterator[None]:
    """Session-scoped fixture to create a RegistryVersion with manifest for core actions.

    This enables versioned action resolution in workflow tests. The manifest includes
    core actions like core.transform.reshape, core.http_request, etc.

    Uses sync SQLAlchemy to avoid event loop conflicts with async fixtures.
    """
    from sqlalchemy.orm import Session

    from tracecat.db.models import (
        PlatformRegistryRepository,
        PlatformRegistryVersion,
        RegistryRepository,
        RegistryVersion,
    )

    def _seed_registry_version(sync_db_uri: str) -> None:
        # Use sync engine to avoid event loop conflicts.
        sync_db_uri = sync_db_uri.replace("+asyncpg", "+psycopg")
        sync_engine = create_engine(sync_db_uri)

        # Ensure schema exists for service sessions that target the default DB.
        Base.metadata.create_all(sync_engine)

        with Session(sync_engine) as session:
            session.execute(
                text(
                    """
                    INSERT INTO organization (id, name, slug, is_active, created_at, updated_at)
                    VALUES (
                        :org_id,
                        'Test Organization',
                        :org_slug,
                        true,
                        now(),
                        now()
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "org_id": str(TEST_ORG_ID),
                    "org_slug": f"test-org-{TEST_ORG_ID.hex[:8]}",
                },
            )
            session.commit()
            # Create a registry repository for core actions
            origin = "tracecat_registry"
            repo = session.scalar(
                select(RegistryRepository).where(
                    RegistryRepository.organization_id == TEST_ORG_ID,
                    RegistryRepository.origin == origin,
                )
            )
            if repo is None:
                repo = RegistryRepository(
                    organization_id=TEST_ORG_ID,
                    origin=origin,
                )
                session.add(repo)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    repo = session.scalar(
                        select(RegistryRepository).where(
                            RegistryRepository.organization_id == TEST_ORG_ID,
                            RegistryRepository.origin == origin,
                        )
                    )
                    if repo is None:
                        raise
                else:
                    session.refresh(repo)

            # Create manifest with core actions used in tests
            manifest_actions = {}

            # Core transform actions
            core_transform_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.transform",
                "name": "reshape",
            }
            manifest_actions["core.transform.reshape"] = {
                "namespace": "core.transform",
                "name": "reshape",
                "action_type": "udf",
                "description": "Reshapes the input value to the output",
                "interface": {"expects": {}, "returns": None},
                "implementation": core_transform_impl,
            }

            # core.http_request
            http_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.http",
                "name": "http_request",
            }
            manifest_actions["core.http_request"] = {
                "namespace": "core",
                "name": "http_request",
                "action_type": "udf",
                "description": "Make an HTTP request",
                "interface": {"expects": {}, "returns": None},
                "implementation": http_impl,
            }

            # core.workflow.execute
            wf_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.workflow",
                "name": "execute",
            }
            manifest_actions["core.workflow.execute"] = {
                "namespace": "core.workflow",
                "name": "execute",
                "action_type": "udf",
                "description": "Execute a child workflow",
                "interface": {"expects": {}, "returns": None},
                "implementation": wf_impl,
            }

            # core.transform.filter
            filter_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.transform",
                "name": "filter",
            }
            manifest_actions["core.transform.filter"] = {
                "namespace": "core.transform",
                "name": "filter",
                "action_type": "udf",
                "description": "Filter a collection",
                "interface": {"expects": {}, "returns": None},
                "implementation": filter_impl,
            }

            # core.transform.transform (alias for reshape in some tests)
            transform_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.transform",
                "name": "reshape",
            }
            manifest_actions["core.transform.transform"] = {
                "namespace": "core.transform",
                "name": "transform",
                "action_type": "udf",
                "description": "Transform data",
                "interface": {"expects": {}, "returns": None},
                "implementation": transform_impl,
            }

            # core.send_email (used in some template tests)
            email_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.email",
                "name": "send_email",
            }
            manifest_actions["core.send_email"] = {
                "namespace": "core",
                "name": "send_email",
                "action_type": "udf",
                "description": "Send an email",
                "interface": {"expects": {}, "returns": None},
                "implementation": email_impl,
            }

            # core.open_case
            open_case_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.cases",
                "name": "open_case",
            }
            manifest_actions["core.open_case"] = {
                "namespace": "core",
                "name": "open_case",
                "action_type": "udf",
                "description": "Open a case",
                "interface": {"expects": {}, "returns": None},
                "implementation": open_case_impl,
            }

            # core.table.lookup
            table_lookup_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.table",
                "name": "lookup",
            }
            manifest_actions["core.table.lookup"] = {
                "namespace": "core.table",
                "name": "lookup",
                "action_type": "udf",
                "description": "Lookup table value",
                "interface": {"expects": {}, "returns": None},
                "implementation": table_lookup_impl,
            }

            # core.table.insert
            table_insert_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.table",
                "name": "insert",
            }
            manifest_actions["core.table.insert"] = {
                "namespace": "core.table",
                "name": "insert",
                "action_type": "udf",
                "description": "Insert table row",
                "interface": {"expects": {}, "returns": None},
                "implementation": table_insert_impl,
            }

            # core.table.insert_row
            table_insert_row_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.table",
                "name": "insert_row",
            }
            manifest_actions["core.table.insert_row"] = {
                "namespace": "core.table",
                "name": "insert_row",
                "action_type": "udf",
                "description": "Insert a row into a table",
                "interface": {"expects": {}, "returns": None},
                "implementation": table_insert_row_impl,
            }

            # core.script.run_python
            script_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.script",
                "name": "run_python",
            }
            manifest_actions["core.script.run_python"] = {
                "namespace": "core.script",
                "name": "run_python",
                "action_type": "udf",
                "description": "Run a Python script",
                "interface": {"expects": {}, "returns": None},
                "implementation": script_impl,
            }

            # core.ai.extract
            ai_extract_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.ai",
                "name": "extract",
            }
            manifest_actions["core.ai.extract"] = {
                "namespace": "core.ai",
                "name": "extract",
                "action_type": "udf",
                "description": "AI extraction",
                "interface": {"expects": {}, "returns": None},
                "implementation": ai_extract_impl,
            }

            # core.transform.map
            map_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.transform",
                "name": "map",
            }
            manifest_actions["core.transform.map"] = {
                "namespace": "core.transform",
                "name": "map",
                "action_type": "udf",
                "description": "Map over items",
                "interface": {"expects": {}, "returns": None},
                "implementation": map_impl,
            }

            # integrations.sinks.webhook
            webhook_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.integrations.sinks",
                "name": "webhook",
            }
            manifest_actions["integrations.sinks.webhook"] = {
                "namespace": "integrations.sinks",
                "name": "webhook",
                "action_type": "udf",
                "description": "Send webhook",
                "interface": {"expects": {}, "returns": None},
                "implementation": webhook_impl,
            }

            # core.transform.scatter (interface action)
            scatter_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.transform",
                "name": "scatter",
            }
            manifest_actions["core.transform.scatter"] = {
                "namespace": "core.transform",
                "name": "scatter",
                "action_type": "udf",
                "description": "Scatter collection into parallel streams",
                "interface": {"expects": {}, "returns": None},
                "implementation": scatter_impl,
            }

            # core.transform.gather (interface action)
            gather_impl = {
                "type": "udf",
                "url": origin,
                "module": "tracecat_registry.core.transform",
                "name": "gather",
            }
            manifest_actions["core.transform.gather"] = {
                "namespace": "core.transform",
                "name": "gather",
                "action_type": "udf",
                "description": "Gather results from parallel streams",
                "interface": {"expects": {}, "returns": None},
                "implementation": gather_impl,
            }

            manifest = {"schema_version": "1.0", "actions": manifest_actions}

            # Create RegistryVersion with manifest
            version = "test-version"
            rv = session.scalar(
                select(RegistryVersion).where(
                    RegistryVersion.organization_id == TEST_ORG_ID,
                    RegistryVersion.repository_id == repo.id,
                    RegistryVersion.version == version,
                )
            )
            if rv is None:
                rv = RegistryVersion(
                    organization_id=TEST_ORG_ID,
                    repository_id=repo.id,
                    version=version,
                    manifest=manifest,
                    tarball_uri="s3://test/test.tar.gz",
                )
                session.add(rv)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    rv = session.scalar(
                        select(RegistryVersion).where(
                            RegistryVersion.organization_id == TEST_ORG_ID,
                            RegistryVersion.repository_id == repo.id,
                            RegistryVersion.version == version,
                        )
                    )
                    if rv is None:
                        raise
                else:
                    session.refresh(rv)
            else:
                rv.manifest = manifest
                rv.tarball_uri = "s3://test/test.tar.gz"
                session.commit()

            # Set current_version_id on the repository for lock resolution
            repo.current_version_id = rv.id
            session.commit()

            logger.info(
                "Created registry version with manifest",
                extra={
                    "db_uri": sync_db_uri,
                    "version": version,
                    "num_actions": len(manifest_actions),
                },
            )

            # Also seed platform registry tables for platform-scoped resolution
            # The executor routes tracecat_registry origin to platform tables
            platform_repo = session.scalar(
                select(PlatformRegistryRepository).where(
                    PlatformRegistryRepository.origin == origin,
                )
            )
            if platform_repo is None:
                platform_repo = PlatformRegistryRepository(origin=origin)
                session.add(platform_repo)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    platform_repo = session.scalar(
                        select(PlatformRegistryRepository).where(
                            PlatformRegistryRepository.origin == origin,
                        )
                    )
                    if platform_repo is None:
                        raise
                else:
                    session.refresh(platform_repo)

            platform_rv = session.scalar(
                select(PlatformRegistryVersion).where(
                    PlatformRegistryVersion.repository_id == platform_repo.id,
                    PlatformRegistryVersion.version == version,
                )
            )
            if platform_rv is None:
                platform_rv = PlatformRegistryVersion(
                    repository_id=platform_repo.id,
                    version=version,
                    manifest=manifest,
                    tarball_uri="s3://test/test.tar.gz",
                )
                session.add(platform_rv)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    platform_rv = session.scalar(
                        select(PlatformRegistryVersion).where(
                            PlatformRegistryVersion.repository_id == platform_repo.id,
                            PlatformRegistryVersion.version == version,
                        )
                    )
                    if platform_rv is None:
                        raise
                else:
                    session.refresh(platform_rv)
            else:
                platform_rv.manifest = manifest
                platform_rv.tarball_uri = "s3://test/test.tar.gz"
                session.commit()

            platform_repo.current_version_id = platform_rv.id
            session.commit()

            # Create PlatformRegistryIndex entries for each action in the manifest
            # This is required for get_actions_from_index to work in agent tools
            from tracecat.db.models import PlatformRegistryIndex

            for _action_name, action_data in manifest_actions.items():
                existing_index = session.scalar(
                    select(PlatformRegistryIndex).where(
                        PlatformRegistryIndex.registry_version_id == platform_rv.id,
                        PlatformRegistryIndex.namespace == action_data["namespace"],
                        PlatformRegistryIndex.name == action_data["name"],
                    )
                )
                if existing_index is None:
                    # Ensure include_in_schema is True so actions appear in list queries
                    options = action_data.get("options", {})
                    options.setdefault("include_in_schema", True)
                    index_entry = PlatformRegistryIndex(
                        registry_version_id=platform_rv.id,
                        namespace=action_data["namespace"],
                        name=action_data["name"],
                        action_type=action_data["action_type"],
                        description=action_data.get("description", ""),
                        default_title=action_data.get("default_title"),
                        display_group=action_data.get("display_group"),
                        doc_url=action_data.get("doc_url"),
                        author=action_data.get("author"),
                        deprecated=action_data.get("deprecated"),
                        secrets=action_data.get("secrets"),
                        interface=action_data.get("interface", {}),
                        options=options,
                    )
                    session.add(index_entry)
                    # Commit each entry individually to handle race conditions
                    # with pytest-xdist parallel workers
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        # Entry already exists from another worker, continue

            logger.info(
                "Created platform registry version with manifest and index",
                extra={
                    "db_uri": sync_db_uri,
                    "version": version,
                    "num_actions": len(manifest_actions),
                },
            )

        sync_engine.dispose()

    # Seed the per-test database used by all services in the test suite.
    if _using_test_db():
        _seed_registry_version(TEST_DB_CONFIG.test_url_sync)
    else:
        _seed_registry_version(config.TRACECAT__DB_URI)

    yield
    # No cleanup needed - the database is dropped at the end of the session


@pytest.fixture(scope="function")
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Creates a new database session joined to an external transaction.

    This fixture creates a nested transaction using SAVEPOINT, allowing
    each test to commit/rollback without affecting other tests.
    """
    async_engine = create_async_engine(
        TEST_DB_CONFIG.test_url,
        isolation_level="SERIALIZABLE",
        poolclass=NullPool,  # Prevent connection accumulation in parallel tests
    )

    # Connect and begin the outer transaction
    async with async_engine.connect() as connection:
        await connection.begin()
        # Avoid CI hangs: fail fast on lock waits and runaway statements.
        # NOTE: SET LOCAL scopes these settings to the surrounding transaction.
        await connection.execute(text("SET LOCAL lock_timeout = '30s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '5min'"))

        # Create session bound to this connection
        async_session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        try:
            yield async_session
        finally:
            try:
                await async_session.close()
                # Rollback the outer transaction, invalidating everything done in the test
                await connection.rollback()
            except Exception as e:
                logger.error(f"Error during session cleanup: {e}")
            finally:
                try:
                    await async_engine.dispose()
                except Exception as e:
                    logger.error(f"Error disposing engine: {e}")


@pytest.fixture(autouse=True, scope="session")
def env_sandbox(
    monkeysession: pytest.MonkeyPatch,
    db: None,
    request: pytest.FixtureRequest,
):
    load_dotenv()
    logger.info("Setting up environment variables")
    importlib.reload(config)
    monkeysession.setattr(config, "TRACECAT__APP_ENV", "development")

    # Use module-level IN_DOCKER detection for host selection
    temporal_host = "temporal" if IN_DOCKER else "localhost"
    api_host = "api" if IN_DOCKER else "localhost"
    blob_storage_host = "minio" if IN_DOCKER else "localhost"

    if _is_temporal_test_run(request):
        db_uri = config.TRACECAT__DB_URI
        if not IN_DOCKER:
            db_uri = _rewrite_db_host(db_uri, host="localhost")
    else:
        db_uri = TEST_DB_CONFIG.test_url_sync
    monkeysession.setattr(config, "TRACECAT__DB_URI", db_uri)
    monkeysession.setattr(
        config, "TEMPORAL__CLUSTER_URL", f"http://{temporal_host}:{TEMPORAL_PORT}"
    )
    blob_storage_endpoint = f"http://{blob_storage_host}:{MINIO_PORT}"
    monkeysession.setattr(
        config, "TRACECAT__BLOB_STORAGE_ENDPOINT", blob_storage_endpoint
    )
    # Configure MinIO for result externalization (StoredObject -> S3)
    monkeysession.setattr(
        config, "TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW", MINIO_WORKFLOW_BUCKET
    )
    monkeysession.setattr(config, "TRACECAT__RESULT_EXTERNALIZATION_ENABLED", True)
    # Externalize all results for testing (threshold=0)
    monkeysession.setattr(config, "TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES", 0)
    monkeysession.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", ["tracecat.com"])
    if os.getenv("TRACECAT__CONTEXT_COMPRESSION_ENABLED"):
        logger.info("Enabling compression for workflow context")
        monkeysession.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", True)
        # Force compression for local unit tests
        monkeysession.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB", 0)

    # Add Homebrew path for macOS development environments
    monkeysession.setattr(
        config,
        "TRACECAT__SYSTEM_PATH",
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    )

    monkeysession.setenv("TRACECAT__DB_URI", db_uri)
    monkeysession.setenv("TRACECAT__BLOB_STORAGE_ENDPOINT", blob_storage_endpoint)
    monkeysession.setenv(
        "TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW", MINIO_WORKFLOW_BUCKET
    )
    monkeysession.setenv("TRACECAT__RESULT_EXTERNALIZATION_ENABLED", "true")
    monkeysession.setenv("TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES", "0")
    # monkeysession.setenv("TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # Point API URL to appropriate host
    api_url = f"http://{api_host}:8000"
    executor_url = f"http://{'executor' if IN_DOCKER else 'localhost'}:8001"
    monkeysession.setattr(config, "TRACECAT__API_URL", api_url)
    monkeysession.setenv("TRACECAT__API_URL", api_url)
    monkeysession.setenv("TRACECAT__EXECUTOR_URL", executor_url)
    # Use DirectBackend for in-process executor (no sandbox overhead) unless overridden
    if not IN_DOCKER:
        monkeysession.setattr(config, "TRACECAT__EXECUTOR_BACKEND", "direct")
        monkeysession.setenv("TRACECAT__EXECUTOR_BACKEND", "direct")
    monkeysession.setenv("TRACECAT__PUBLIC_API_URL", f"http://{api_host}/api")
    service_key = os.environ.get("TRACECAT__SERVICE_KEY", "test-service-key")
    monkeysession.setattr(config, "TRACECAT__SERVICE_KEY", service_key)
    monkeysession.setenv("TRACECAT__SERVICE_KEY", service_key)
    monkeysession.setenv("TRACECAT__SIGNING_SECRET", "test-signing-secret")
    monkeysession.setenv("TEMPORAL__CLUSTER_URL", f"http://{temporal_host}:7233")
    monkeysession.setenv("TEMPORAL__CLUSTER_NAMESPACE", "default")
    # Use worker-specific task queues for pytest-xdist isolation
    monkeysession.setenv("TEMPORAL__CLUSTER_QUEUE", TEMPORAL_TASK_QUEUE)
    monkeysession.setattr(config, "TEMPORAL__CLUSTER_QUEUE", TEMPORAL_TASK_QUEUE)
    monkeysession.setenv("TRACECAT__EXECUTOR_QUEUE", EXECUTOR_TASK_QUEUE)
    monkeysession.setattr(config, "TRACECAT__EXECUTOR_QUEUE", EXECUTOR_TASK_QUEUE)
    monkeysession.setenv("TRACECAT__AGENT_QUEUE", AGENT_TASK_QUEUE)
    monkeysession.setattr(config, "TRACECAT__AGENT_QUEUE", AGENT_TASK_QUEUE)
    reset_async_engine()

    yield
    logger.info("Environment variables cleaned up")


@pytest.fixture(scope="session")
def mock_user_id() -> uuid.UUID:
    # Predictable uuid4 for testing
    return uuid.UUID("44444444-aaaa-4444-aaaa-444444444444")


@pytest.fixture(scope="session")
def mock_org_id() -> uuid.UUID:
    # Worker-specific org ID for pytest-xdist isolation
    # Each worker gets a unique org ID to avoid conflicts in shared resources (e.g., MinIO)
    # Format: 00000000-0000-4444-aaaa-00000000000N where N is worker number
    return uuid.UUID(f"00000000-0000-4444-aaaa-{WORKER_OFFSET:012d}")


@pytest.fixture(scope="function")
async def test_role(test_workspace, mock_org_id):
    """Create a test role for the test session and set `ctx_role`."""
    service_role = Role(
        type="service",
        user_id=mock_org_id,
        organization_id=mock_org_id,
        workspace_id=test_workspace.id,
        service_id="tracecat-runner",
    )
    token = ctx_role.set(service_role)
    try:
        yield service_role
    finally:
        ctx_role.reset(token)


@pytest.fixture(scope="function")
async def test_admin_role(test_workspace, mock_org_id):
    """Create a test role for the test session and set `ctx_role`."""
    admin_role = Role(
        type="user",
        user_id=mock_org_id,
        organization_id=mock_org_id,
        workspace_id=test_workspace.id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        service_id="tracecat-runner",
    )
    token = ctx_role.set(admin_role)
    try:
        yield admin_role
    finally:
        ctx_role.reset(token)


@pytest.fixture(scope="function")
async def test_organization(mock_org_id):
    """Create or get a test organization for the test session."""
    async with get_async_session_context_manager() as session:
        # Check if organization exists
        result = await session.execute(
            select(Organization).where(Organization.id == mock_org_id)
        )
        org = result.scalar_one_or_none()
        if org is None:
            # Create test organization
            # Use WORKER_OFFSET in slug to avoid collisions in parallel xdist workers
            org = Organization(
                id=mock_org_id,
                name="Test Organization",
                slug=f"test-org-{WORKER_OFFSET}",
                is_active=True,
            )
            session.add(org)
            try:
                await session.commit()
                await session.refresh(org)
                logger.debug("Created test organization", organization=org)
            except IntegrityError:
                # Race condition: another test created the org first
                await session.rollback()
                result = await session.execute(
                    select(Organization).where(Organization.id == mock_org_id)
                )
                org = result.scalar_one_or_none()
                if org is None:
                    raise
                logger.debug(
                    "Got existing test organization after race", organization=org
                )
        yield org


@pytest.fixture(scope="function")
async def session_test_organization(session, mock_org_id):
    """Create a test organization in the test's session.

    Use this fixture when the test needs an organization that's visible
    within the test's isolated database session (e.g., for FK constraints).
    """
    # Check if organization exists in this session
    result = await session.execute(
        select(Organization).where(Organization.id == mock_org_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        # Create test organization in the test's session
        # Use WORKER_OFFSET in slug to avoid collisions in parallel xdist workers
        org = Organization(
            id=mock_org_id,
            name="Test Organization",
            slug=f"test-org-{WORKER_OFFSET}",
            is_active=True,
        )
        session.add(org)
        await session.flush()  # Make visible in session without committing
        logger.debug("Created test organization in session", organization=org)
    return org


@pytest.fixture(scope="function")
async def test_workspace(test_organization, mock_org_id):
    """Create a test workspace for the test session."""
    ws_id = uuid.uuid4()
    workspace_name = f"__test_workspace_{ws_id.hex[:8]}"

    # Use a role with organization_id and org_role for the WorkspaceService
    org_role = Role(
        type="service",
        service_id="tracecat-service",
        organization_id=mock_org_id,
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.OWNER,
    )

    async with WorkspaceService.with_session(role=org_role) as svc:
        # Create new test workspace
        workspace = await svc.create_workspace(name=workspace_name, override_id=ws_id)

        logger.debug("Created test workspace", workspace=workspace)
        try:
            yield workspace
        finally:
            # Clean up the workspace
            logger.debug("Teardown test workspace")
            try:
                await svc.delete_workspace(ws_id)
            except Exception as e:
                logger.warning(f"Error during workspace cleanup: {e}")


@pytest.fixture(scope="session")
def temporal_client():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        policy = asyncio.get_event_loop_policy()
        loop = policy.new_event_loop()

    client = loop.run_until_complete(
        get_temporal_client(plugins=[TracecatPydanticAIPlugin()])
    )
    return client


@pytest.fixture(scope="function")
async def db_session_with_repo(test_role):
    """Fixture that creates a db session and temporary repository."""

    async with RegistryReposService.with_session(role=test_role) as svc:
        db_repo = await svc.create_repository(
            RegistryRepositoryCreate(
                origin=f"git+ssh://git@github.com/TracecatHQ/dummy-repo-{uuid.uuid4().hex[:8]}.git"
            )
        )
        try:
            yield svc.session, db_repo.id
        finally:
            try:
                await svc.delete_repository(db_repo)
                logger.info("Cleaned up db repo")
            except Exception as e:
                logger.error("Error cleaning up repo", e=e)


@pytest.fixture
async def svc_organization(session: AsyncSession) -> AsyncGenerator[Organization, None]:
    """Service test fixture. Create an organization for service tests."""
    org = None
    if _using_test_db():
        # Create in the global session first to avoid duplicate inserts when the
        # test session and global session point at the same DB.
        async with get_async_session_context_manager() as global_session:
            await global_session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await global_session.execute(text("SET LOCAL statement_timeout = '30s'"))
            result = await global_session.execute(
                select(Organization).where(Organization.id == TEST_ORG_ID)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                org = Organization(
                    id=TEST_ORG_ID,
                    name="Test Organization",
                    slug=f"test-org-{TEST_ORG_ID.hex[:8]}",
                    is_active=True,
                )
                global_session.add(org)
                await global_session.commit()
                await global_session.refresh(org)
            else:
                org = existing

        # Attach the committed org to the test session without re-inserting.
        if org is None:
            raise RuntimeError("Failed to create service organization in test DB")
        org = await session.merge(org, load=False)
        yield org
        return

    # Legacy path for non-test DBs: keep session-local + global setup.
    result = await session.execute(
        select(Organization).where(Organization.id == TEST_ORG_ID)
    )
    org = result.scalar_one_or_none()
    if org is None:
        org = Organization(
            id=TEST_ORG_ID,
            name="Test Organization",
            slug=f"test-org-{TEST_ORG_ID.hex[:8]}",
            is_active=True,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)

    async with get_async_session_context_manager() as global_session:
        await global_session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await global_session.execute(text("SET LOCAL statement_timeout = '30s'"))
        result = await global_session.execute(
            select(Organization).where(Organization.id == TEST_ORG_ID)
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            global_session.add(
                Organization(
                    id=TEST_ORG_ID,
                    name="Test Organization",
                    slug=f"test-org-{TEST_ORG_ID.hex[:8]}",
                    is_active=True,
                )
            )
            await global_session.commit()

    yield org


@pytest.fixture
async def svc_workspace(
    session: AsyncSession, svc_organization: Organization
) -> AsyncGenerator[Workspace, None]:
    """Service test fixture. Create a function scoped test workspace."""
    workspace = Workspace(
        name="test-workspace",
        organization_id=svc_organization.id,
    )
    if _using_test_db():
        # Create in the global session first to avoid duplicate inserts when
        # the test session and global session point at the same DB.
        async with get_async_session_context_manager() as global_session:
            await global_session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await global_session.execute(text("SET LOCAL statement_timeout = '30s'"))
            result = await global_session.execute(
                select(Workspace).where(Workspace.id == workspace.id)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                global_session.add(workspace)
                await global_session.commit()
                await global_session.refresh(workspace)
            else:
                workspace = existing

        workspace = await session.merge(workspace, load=False)
    else:
        session.add(workspace)
        await session.commit()

        # Also persist the workspace in the default engine used by BaseWorkspaceService
        # so services that use `with_session()` (and thus `get_async_session_context_manager`)
        # can see the same workspace and satisfy foreign key constraints.
        async with get_async_session_context_manager() as global_session:
            # Set timeouts to avoid deadlocks with parallel workers
            await global_session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await global_session.execute(text("SET LOCAL statement_timeout = '30s'"))
            # Avoid duplicate insert if the workspace already exists
            result = await global_session.execute(
                select(Workspace).where(Workspace.id == workspace.id)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                global_session.add(
                    Workspace(
                        id=workspace.id,
                        name=workspace.name,
                        organization_id=workspace.organization_id,
                    )
                )
                await global_session.commit()

    try:
        yield workspace
    finally:
        logger.debug("Cleaning up test workspace")
        # Clean up workspace from global session (postgres database) first
        try:
            async with get_async_session_context_manager() as global_cleanup_session:
                # Set timeouts to avoid deadlocks with parallel workers
                await global_cleanup_session.execute(
                    text("SET LOCAL lock_timeout = '5s'")
                )
                await global_cleanup_session.execute(
                    text("SET LOCAL statement_timeout = '30s'")
                )
                result = await global_cleanup_session.execute(
                    select(Workspace).where(Workspace.id == workspace.id)
                )
                global_workspace = result.scalar_one_or_none()
                if global_workspace:
                    await global_cleanup_session.delete(global_workspace)
                    await global_cleanup_session.commit()
                    logger.debug("Cleaned up workspace from global session")
        except Exception as e:
            logger.error(f"Error cleaning up workspace from global session: {e}")

        # Clean up workspace from test session only when using a separate DB
        if not _using_test_db():
            try:
                if session.is_active:
                    # Reset transaction state in case it was aborted
                    try:
                        # Try to roll back any active transaction first
                        await session.rollback()
                        # Then delete and commit in a fresh transaction
                        await session.delete(workspace)
                        await session.commit()
                    except Exception as inner_e:
                        logger.error(
                            f"Failed to clean up in existing session: {inner_e}"
                        )
                        # If that fails, try with a completely new session
                        await session.close()
                        async with get_async_session_context_manager() as new_session:
                            # Set timeouts to avoid deadlocks with parallel workers
                            await new_session.execute(
                                text("SET LOCAL lock_timeout = '5s'")
                            )
                            await new_session.execute(
                                text("SET LOCAL statement_timeout = '30s'")
                            )
                            # Fetch the workspace again in the new session by logical ID
                            result = await new_session.execute(
                                select(Workspace).where(Workspace.id == workspace.id)
                            )
                            db_workspace = result.scalar_one_or_none()
                            if db_workspace:
                                await new_session.delete(db_workspace)
                                await new_session.commit()
            except Exception as e:
                # Log the error but don't raise it to prevent test teardown failures
                logger.error(f"Error during workspace cleanup: {e}")


@pytest.fixture
async def svc_role(svc_workspace: Workspace) -> Role:
    """Service test fixture. Create a function scoped test role."""
    return Role(
        type="user",
        access_level=AccessLevel.BASIC,
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
async def svc_admin_role(svc_workspace: Workspace) -> Role:
    """Service test fixture. Create a function scoped test role."""
    return Role(
        type="user",
        access_level=AccessLevel.ADMIN,
        org_role=OrgRole.ADMIN,
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


# MinIO and S3 testing fixtures
@pytest.fixture(scope="session")
def minio_server():
    """Verify MinIO is available via docker-compose.

    MinIO should be started externally via:
    - CI: docker-compose in workflow
    - Local: `just dev` or `docker-compose up`
    """
    endpoint = f"localhost:{MINIO_PORT}"
    for _ in range(30):
        try:
            access_key, secret_key = _minio_credentials()
            client = Minio(
                endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=False,
            )
            list(client.list_buckets())
            logger.info(f"MinIO available on port {MINIO_PORT}")
            yield
            return
        except Exception:
            time.sleep(1)

    pytest.fail(
        f"MinIO not available on port {MINIO_PORT}. "
        "Start it with: docker-compose -f docker-compose.dev.yml up -d minio"
    )


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket(minio_server, env_sandbox):
    """Create the workflow bucket for result externalization and reset object storage.

    This fixture:
    1. Creates the bucket used by S3ObjectStorage for StoredObject externalization
    2. Reloads the blob module to pick up the test config
    3. Resets the object storage singleton so it uses S3ObjectStorage

    Session-scoped and autouse to ensure all tests use S3-backed object storage.
    Depends on env_sandbox to ensure config is set before we create the bucket.
    """
    from tracecat.storage import blob
    from tracecat.storage import object as object_module

    # Reload blob module to pick up MinIO config
    importlib.reload(blob)

    # Create workflow bucket if it doesn't exist
    access_key, secret_key = _minio_credentials()
    client = Minio(
        f"localhost:{MINIO_PORT}",
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
    )
    try:
        if not client.bucket_exists(MINIO_WORKFLOW_BUCKET):
            client.make_bucket(MINIO_WORKFLOW_BUCKET)
            logger.info(f"Created workflow bucket: {MINIO_WORKFLOW_BUCKET}")
    except S3Error as e:
        if e.code != "BucketAlreadyOwnedByYou":
            raise

    # Reset object storage singleton so it picks up the test config (S3ObjectStorage)
    object_module.reset_object_storage()
    logger.info("Reset object storage for S3-backed externalization")

    yield

    # Cleanup: reset object storage after tests
    object_module.reset_object_storage()


@pytest.fixture
async def minio_client(minio_server) -> AsyncGenerator[Minio, None]:
    """Create MinIO client for testing."""
    access_key, secret_key = _minio_credentials()
    client = Minio(
        f"localhost:{MINIO_PORT}",
        access_key=access_key,
        secret_key=secret_key,
        secure=False,
    )
    yield client


@pytest.fixture
async def minio_bucket(minio_client: Minio) -> AsyncGenerator[str, None]:
    """Create and cleanup a test bucket in MinIO."""
    bucket_name = f"test-bucket-{uuid.uuid4().hex[:8]}"

    # Create test bucket
    try:
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
    except S3Error as e:
        if e.code != "BucketAlreadyOwnedByYou":
            raise

    yield bucket_name

    # Cleanup: remove all objects and bucket
    try:
        objects = minio_client.list_objects(bucket_name, recursive=True)
        for obj in objects:
            if obj.object_name:  # Check for None
                minio_client.remove_object(bucket_name, obj.object_name)
        minio_client.remove_bucket(bucket_name)
    except S3Error:
        pass  # Ignore cleanup errors


@pytest.fixture
def mock_s3_secrets():
    """Mock S3 secrets to use MinIO credentials."""
    access_key, secret_key = _minio_credentials()
    secrets_manager.set("AWS_ACCESS_KEY_ID", access_key)
    secrets_manager.set("AWS_SECRET_ACCESS_KEY", secret_key)
    secrets_manager.set("AWS_REGION", "us-east-1")


@pytest.fixture
async def aioboto3_minio_client(monkeypatch):
    """Fixture that mocks aioboto3 to use MinIO endpoint."""

    # Mock get_session to return session with MinIO credentials
    async def mock_get_session():
        access_key, secret_key = _minio_credentials()
        return aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    # Mock client creation to use MinIO endpoint
    original_client = aioboto3.Session.client

    def mock_client(self, service_name, **kwargs):
        if service_name == "s3":
            kwargs["endpoint_url"] = f"http://localhost:{MINIO_PORT}"
        return original_client(self, service_name, **kwargs)

    # Apply mocks using monkeypatch
    monkeypatch.setattr(boto3_module, "get_session", mock_get_session)
    monkeypatch.setattr(aioboto3.Session, "client", mock_client)

    yield


@pytest.fixture(scope="function")
def threadpool() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=4) as executor:
        yield executor


@pytest.fixture(scope="function")
async def executor_backend() -> AsyncGenerator[ExecutorBackend, None]:
    """Initialize executor backend once per test function."""
    from tracecat.executor.backends import (
        initialize_executor_backend,
        shutdown_executor_backend,
    )

    backend = await initialize_executor_backend()
    yield backend
    await shutdown_executor_backend()


@pytest.fixture(scope="function")
async def test_worker_factory(
    threadpool: ThreadPoolExecutor,
) -> AsyncGenerator[Callable[..., Worker], Any]:
    """Factory fixture to create workers with proper ThreadPoolExecutor cleanup."""

    def create_worker(
        client: Client,
        *,
        activities: list[Callable] | None = None,
        task_queue: str | None = None,
    ) -> Worker:
        """Create a worker with the same configuration as production."""

        activities = activities or get_activities()
        return Worker(
            client=client,
            task_queue=task_queue or os.environ["TEMPORAL__CLUSTER_QUEUE"],
            activities=activities,
            workflows=[DSLWorkflow],
            workflow_runner=new_sandbox_runner(),
            activity_executor=threadpool,
        )

    yield create_worker


@pytest.fixture(scope="function")
async def test_executor_worker_factory(
    threadpool: ThreadPoolExecutor,
    executor_backend: ExecutorBackend,
) -> AsyncGenerator[Callable[..., Worker], Any]:
    """Factory fixture to create executor workers with DirectBackend.

    This worker listens on the shared-action-queue and handles execute_action_activity.
    Uses DirectBackend for in-process execution without sandbox overhead.
    """
    from tracecat.executor.activities import ExecutorActivities

    def create_worker(
        client: Client,
        *,
        task_queue: str | None = None,
    ) -> Worker:
        """Create an executor worker for testing."""
        return Worker(
            client=client,
            task_queue=task_queue or config.TRACECAT__EXECUTOR_QUEUE,
            activities=ExecutorActivities.get_activities(),
            activity_executor=threadpool,
        )

    yield create_worker


# ---------------------------------------------------------------------------
# 3rd party credentials
# Loaded in either via dotenv or env vars into the mocked Tracecat secrets manager
# ---------------------------------------------------------------------------

### OpenAI


@pytest.fixture
def mock_openai_secrets(monkeypatch: pytest.MonkeyPatch):
    """Set up env_sandbox with OpenAI API key from environment."""

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        pytest.skip("OPENAI_API_KEY not found in environment")

    with (
        patch("tracecat_registry._internal.secrets.get") as mock_get,
        secrets_manager.env_sandbox({"OPENAI_API_KEY": openai_key}),
    ):

        def side_effect(key: str):
            if key == "OPENAI_API_KEY":
                return openai_key
            return None

        mock_get.side_effect = side_effect
        yield mock_get


### Anthropic


@pytest.fixture
def mock_anthropic_secrets():
    """Set up env_sandbox with Anthropic API key from environment."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not anthropic_key:
        pytest.skip("ANTHROPIC_API_KEY not found in environment")

    with (
        patch("tracecat_registry._internal.secrets.get") as mock_get,
        secrets_manager.env_sandbox({"ANTHROPIC_API_KEY": anthropic_key}),
    ):

        def side_effect(key: str):
            if key == "ANTHROPIC_API_KEY":
                return anthropic_key
            return None

        mock_get.side_effect = side_effect
        yield mock_get


### Bedrock


@pytest.fixture
def mock_bedrock_secrets():
    """Set up env_sandbox with AWS credentials from environment for Bedrock."""
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    if not aws_access_key or not aws_secret_key:
        pytest.skip(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY not found in environment"
        )

    with (
        patch("tracecat_registry._internal.secrets.get") as mock_get,
        secrets_manager.env_sandbox(
            {
                "AWS_ACCESS_KEY_ID": aws_access_key,
                "AWS_SECRET_ACCESS_KEY": aws_secret_key,
                "AWS_REGION": aws_region,
            }
        ),
    ):

        def side_effect(key: str):
            if key == "AWS_ACCESS_KEY_ID":
                return aws_access_key
            if key == "AWS_SECRET_ACCESS_KEY":
                return aws_secret_key
            if key == "AWS_REGION":
                return aws_region
            return None

        mock_get.side_effect = side_effect
        yield mock_get


### Slack


@pytest.fixture
def mock_slack_secrets():
    """Mock Slack secrets lookups for direct SDK access while keeping env sandbox."""
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        pytest.skip("SLACK_BOT_TOKEN not found in environment")

    with (
        patch("tracecat_registry._internal.secrets.get") as mock_get,
        secrets_manager.env_sandbox({"SLACK_BOT_TOKEN": slack_token}),
    ):

        def side_effect(key: str):
            if key == "SLACK_BOT_TOKEN":
                return slack_token
            return None

        mock_get.side_effect = side_effect
        yield mock_get


# ---------------------------------------------------------------------------
# Agent fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mock_agent_session(session: AsyncSession, svc_role: Role):
    """Create a mock AgentSession directly in the database.

    This is a lightweight fixture for tests that need a valid session_id
    to satisfy foreign key constraints (e.g., approval tests).
    """
    from tracecat.db.models import AgentSession

    session_id = uuid.uuid4()
    agent_session = AgentSession(
        id=session_id,
        title="Mock Test Session",
        workspace_id=svc_role.workspace_id,
        entity_type="workflow",
        entity_id=uuid.uuid4(),
    )
    session.add(agent_session)
    await session.commit()
    await session.refresh(agent_session)
    return agent_session
