import asyncio
import os
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest
from minio import Minio
from minio.error import S3Error
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from temporalio.client import Client
from temporalio.worker import Worker

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_engine, get_async_session_context_manager
from tracecat.db.schemas import Workspace
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.worker import get_activities, new_sandbox_runner
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.logger import logger
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.secrets.models import SecretCreate, SecretKeyValue, SecretUpdate
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import AccessLevel, Role, system_role
from tracecat.workspaces.service import WorkspaceService

# Define Slack test skip markers
skip_if_no_slack_token = pytest.mark.skipif(
    not os.getenv("SLACK_BOT_TOKEN"),
    reason="SLACK_BOT_TOKEN must be set as environment variable",
)

skip_if_no_slack_credentials = pytest.mark.skipif(
    not os.getenv("SLACK_BOT_TOKEN") or not os.getenv("SLACK_CHANNEL_ID"),
    reason="SLACK_BOT_TOKEN and SLACK_CHANNEL_ID must be set as environment variables",
)

# Define a reusable usefixture marker for slack tests
requires_slack_mocks = pytest.mark.usefixtures("mock_slack_secrets")

# MinIO test configuration
MINIO_ENDPOINT = "localhost:9002"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_CONTAINER_NAME = "test-minio-grep"


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
    """Create a new engine for each integration test."""
    engine = get_async_engine()
    try:
        yield engine
    finally:
        # Ensure the engine is disposed even if the test fails
        try:
            await engine.dispose()
        except Exception as e:
            logger.error(f"Error disposing engine in test_db_engine: {e}")


@pytest.fixture(scope="session")
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
            SQLModel.metadata.create_all(conn)
        yield
    finally:
        test_engine.dispose()
        # # Cleanup - reconnect to system db to drop test db
        with default_engine.begin() as conn:
            conn.execute(termination_query)
            conn.execute(
                text(f'DROP DATABASE IF EXISTS "{TEST_DB_CONFIG.test_db_name}"')
            )
        logger.info("Dropped test database")
        default_engine.dispose()


@pytest.fixture(scope="function")
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Creates a new database session joined to an external transaction.

    This fixture creates a nested transaction using SAVEPOINT, allowing
    each test to commit/rollback without affecting other tests.
    """
    async_engine = create_async_engine(
        TEST_DB_CONFIG.test_url, isolation_level="SERIALIZABLE"
    )

    # Connect and begin the outer transaction
    async with async_engine.connect() as connection:
        await connection.begin()

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
def env_sandbox(monkeysession: pytest.MonkeyPatch):
    from dotenv import load_dotenv

    load_dotenv()
    logger.info("Setting up environment variables")
    monkeysession.setattr(config, "TRACECAT__APP_ENV", "development")
    monkeysession.setattr(
        config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    monkeysession.setattr(config, "TEMPORAL__CLUSTER_URL", "http://localhost:7233")
    monkeysession.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", ["tracecat.com"])
    # Need this for local unit tests
    monkeysession.setattr(config, "TRACECAT__EXECUTOR_URL", "http://localhost:8001")
    if os.getenv("TRACECAT__CONTEXT_COMPRESSION_ENABLED"):
        monkeysession.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_ENABLED", True)
        # Force compression for local unit tests
        monkeysession.setattr(config, "TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB", 0)

    # Add Homebrew path for macOS development environments
    monkeysession.setattr(
        config,
        "TRACECAT__SYSTEM_PATH",
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    )

    monkeysession.setenv(
        "TRACECAT__DB_URI",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    # monkeysession.setenv("TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeysession.setenv("TRACECAT__API_URL", "http://api:8000")
    # Needed for local unit tests
    monkeysession.setenv("TRACECAT__EXECUTOR_URL", "http://executor:8000")
    monkeysession.setenv("TRACECAT__PUBLIC_API_URL", "http://localhost/api")
    monkeysession.setenv("TRACECAT__SERVICE_KEY", os.environ["TRACECAT__SERVICE_KEY"])
    monkeysession.setenv("TRACECAT__SIGNING_SECRET", "test-signing-secret")
    # When launching the worker directly in a test, use localhost
    # If the worker is running inside a container, use host.docker.internal
    monkeysession.setenv("TEMPORAL__CLUSTER_URL", "http://localhost:7233")
    monkeysession.setenv("TEMPORAL__CLUSTER_QUEUE", "test-tracecat-task-queue")
    monkeysession.setenv("TEMPORAL__CLUSTER_NAMESPACE", "default")

    yield
    logger.info("Environment variables cleaned up")


@pytest.fixture(scope="session")
def mock_user_id() -> uuid.UUID:
    # Predictable uuid4 for testing
    return uuid.UUID("44444444-aaaa-4444-aaaa-444444444444")


@pytest.fixture(scope="session")
def mock_org_id() -> uuid.UUID:
    # Predictable uuid4 for testing
    return uuid.UUID("00000000-0000-4444-aaaa-000000000000")


@pytest.fixture(scope="function")
async def test_role(test_workspace, mock_org_id):
    """Create a test role for the test session and set `ctx_role`."""
    service_role = Role(
        type="service",
        user_id=mock_org_id,
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
        workspace_id=test_workspace.id,
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-runner",
    )
    yield admin_role


@pytest.fixture(scope="function")
async def test_workspace():
    """Create a test workspace for the test session."""
    workspace_name = "__test_workspace"

    async with WorkspaceService.with_session(role=system_role()) as svc:
        # Check if test workspace already exists
        existing_workspaces = await svc.admin_list_workspaces()
        for ws in existing_workspaces:
            if ws.name == workspace_name:
                logger.info("Found existing test workspace, deleting it first")
                await svc.delete_workspace(ws.id)

        # Create new test workspace
        workspace = await svc.create_workspace(name=workspace_name)

        logger.info("Created test workspace", workspace=workspace)

        try:
            yield workspace
        finally:
            # Clean up the workspace
            logger.info("Teardown test workspace")
            try:
                await svc.delete_workspace(workspace.id)
            except Exception as e:
                logger.error(f"Error during workspace cleanup: {e}")


@pytest.fixture(scope="function")
def temporal_client():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        policy = asyncio.get_event_loop_policy()
        loop = policy.new_event_loop()

    client = loop.run_until_complete(get_temporal_client())
    return client


@pytest.fixture(scope="function")
async def db_session_with_repo(test_role):
    """Fixture that creates a db session and temporary repository."""

    async with RegistryReposService.with_session(role=test_role) as svc:
        db_repo = await svc.create_repository(
            RegistryRepositoryCreate(
                origin="git+ssh://git@github.com/TracecatHQ/dummy-repo.git"
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
async def svc_workspace(session: AsyncSession) -> AsyncGenerator[Workspace, None]:
    """Service test fixture. Create a function scoped test workspace."""
    workspace = Workspace(
        name="test-workspace",
        owner_id=config.TRACECAT__DEFAULT_ORG_ID,
    )
    session.add(workspace)
    await session.commit()
    try:
        yield workspace
    finally:
        logger.info("Cleaning up test workspace")
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
                    logger.error(f"Failed to clean up in existing session: {inner_e}")
                    # If that fails, try with a completely new session
                    await session.close()
                    async with get_async_session_context_manager() as new_session:
                        # Fetch the workspace again in the new session
                        db_workspace = await new_session.get(Workspace, workspace.id)
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
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
async def svc_admin_role(svc_workspace: Workspace) -> Role:
    """Service test fixture. Create a function scoped test role."""
    return Role(
        type="user",
        access_level=AccessLevel.ADMIN,
        workspace_id=svc_workspace.id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
async def mock_slack_secrets():
    """Mock the secrets.get function for slack_sdk integration.

    This fixture is used by both the agent builder tests and MCP slackbot tests.
    It mocks the secrets.get function for direct SDK access.
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        pytest.skip("SLACK_BOT_TOKEN not set in environment")

    with patch("tracecat_registry.integrations.slack_sdk.secrets.get") as mock_get:

        def side_effect(key):
            if key == "SLACK_BOT_TOKEN":
                return slack_token
            return None

        mock_get.side_effect = side_effect
        yield mock_get


@pytest.fixture
async def slack_secret(test_role):
    """Create a Slack secret in the Tracecat secrets manager for testing.

    This fixture creates a temporary secret in the Tracecat secrets manager that
    can be used by tests that need to access Slack via the Tracecat service.
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        pytest.skip("SLACK_BOT_TOKEN not set in environment")

    async with SecretsService.with_session(role=test_role) as svc:
        # Check if slack secret already exists
        try:
            existing_secret = await svc.get_secret_by_name("slack")
            if existing_secret:
                # Update the existing secret
                await svc.update_secret(
                    existing_secret,
                    SecretUpdate(
                        keys=[
                            SecretKeyValue(
                                key="SLACK_BOT_TOKEN", value=SecretStr(slack_token)
                            )
                        ]
                    ),
                )
                yield existing_secret
                return
        except Exception:
            # Secret doesn't exist, create it
            pass

        # Create the slack secret
        await svc.create_secret(
            SecretCreate(
                name="slack",
                description="Slack bot token for testing",
                environment="default",
                keys=[
                    SecretKeyValue(key="SLACK_BOT_TOKEN", value=SecretStr(slack_token))
                ],
            )
        )

        # Get the created secret to yield it
        created_secret = await svc.get_secret_by_name("slack")

        try:
            yield created_secret
        finally:
            # Clean up the secret
            try:
                await svc.delete_secret(created_secret)
            except Exception as e:
                logger.warning(f"Failed to clean up slack secret: {e}")


# MinIO and S3 testing fixtures
@pytest.fixture(scope="session")
def minio_server():
    """Start MinIO server in Docker for the test session."""
    # First, clean up any existing container
    try:
        subprocess.run(
            ["docker", "stop", MINIO_CONTAINER_NAME], check=False, capture_output=True
        )
        subprocess.run(
            ["docker", "rm", MINIO_CONTAINER_NAME], check=False, capture_output=True
        )
    except subprocess.CalledProcessError:
        pass

    # Start MinIO container with correct environment variables
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                MINIO_CONTAINER_NAME,
                "-p",
                "9002:9000",
                "-p",
                "9003:9001",
                "-e",
                f"MINIO_ROOT_USER={MINIO_ACCESS_KEY}",
                "-e",
                f"MINIO_ROOT_PASSWORD={MINIO_SECRET_KEY}",
                "minio/minio:latest",
                "server",
                "/data",
                "--console-address",
                ":9001",
            ],
            check=True,
            capture_output=True,
            # Add timeout
            timeout=20,
        )

        # Wait for MinIO to be ready
        max_retries = 30
        for i in range(max_retries):
            try:
                client = Minio(
                    MINIO_ENDPOINT,
                    access_key=MINIO_ACCESS_KEY,
                    secret_key=MINIO_SECRET_KEY,
                    secure=False,
                )
                # Try to list buckets to check if MinIO is ready
                list(client.list_buckets())
                logger.info(f"MinIO server started in container {MINIO_CONTAINER_NAME}")
                break
            except Exception as e:
                if i == max_retries - 1:
                    logger.error(
                        f"MinIO failed to start after {max_retries} retries: {e}"
                    )
                    raise RuntimeError(
                        "MinIO server failed to start within timeout"
                    ) from e
                time.sleep(1)

        yield

    finally:
        # Cleanup: stop and remove container
        try:
            subprocess.run(
                ["docker", "stop", MINIO_CONTAINER_NAME],
                check=False,
                capture_output=True,
            )
            subprocess.run(
                ["docker", "rm", MINIO_CONTAINER_NAME], check=False, capture_output=True
            )
            logger.info(f"MinIO container {MINIO_CONTAINER_NAME} cleaned up")
        except subprocess.CalledProcessError:
            pass


@pytest.fixture
async def minio_client(minio_server) -> AsyncGenerator[Minio, None]:
    """Create MinIO client for testing."""
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
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
async def mock_s3_secrets(monkeypatch):
    """Mock S3 secrets to use MinIO credentials."""

    def mock_get_secret(key: str) -> str:
        secrets_map = {
            "AWS_ACCESS_KEY_ID": MINIO_ACCESS_KEY,
            "AWS_SECRET_ACCESS_KEY": MINIO_SECRET_KEY,
            "AWS_REGION": "us-east-1",
        }
        return secrets_map.get(key, "")

    # Mock the secrets.get function in various modules using the correct import path
    monkeypatch.setattr("tracecat_registry.secrets.get", mock_get_secret)


@pytest.fixture
async def aioboto3_minio_client(monkeypatch):
    """Fixture that mocks aioboto3 to use MinIO endpoint."""
    import aioboto3
    import tracecat_registry.integrations.aws_boto3 as boto3_module

    # Mock get_session to return session with MinIO credentials
    async def mock_get_session():
        return aioboto3.Session(
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            region_name="us-east-1",
        )

    # Mock client creation to use MinIO endpoint
    original_client = aioboto3.Session.client

    def mock_client(self, service_name, **kwargs):
        if service_name == "s3":
            kwargs["endpoint_url"] = f"http://{MINIO_ENDPOINT}"
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
        return Worker(
            client=client,
            task_queue=task_queue or os.environ["TEMPORAL__CLUSTER_QUEUE"],
            activities=activities or get_activities(),
            workflows=[DSLWorkflow],
            workflow_runner=new_sandbox_runner(),
            activity_executor=threadpool,
        )

    yield create_worker
