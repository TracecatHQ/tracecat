import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.database import TEST_DB_CONFIG
from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_engine, get_async_session_context_manager
from tracecat.db.schemas import User, Workspace
from tracecat.logger import logger
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.types.auth import AccessLevel, Role
from tracecat.workspaces.models import WorkspaceMetadataResponse


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
        await engine.dispose()


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
            await async_session.close()
            # Rollback the outer transaction, invalidating everything done in the test
            await connection.rollback()
            await async_engine.dispose()


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
    monkeysession.setattr(
        config,
        "TRACECAT__REMOTE_REPOSITORY_URL",
        "git+ssh://git@github.com/TracecatHQ/udfs.git",
    )
    # Need this for local unit tests
    monkeysession.setattr(config, "TRACECAT__EXECUTOR_URL", "http://localhost:8001")

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
    # Cleanup is automatic with monkeypatch
    logger.info("Environment variables cleaned up")


@pytest.fixture(scope="session")
def mock_user_id() -> uuid.UUID:
    # Predictable uuid4 for testing
    return uuid.UUID("44444444-aaaa-4444-aaaa-444444444444")


@pytest.fixture(scope="session")
def mock_org_id() -> uuid.UUID:
    # Predictable uuid4 for testing
    return uuid.UUID("00000000-0000-4444-aaaa-000000000000")


# NOTE: Don't auto-use this fixture unless necessary
@pytest.fixture(scope="session")
def test_role(test_workspace, mock_org_id):
    """Create a test role for the test session and set `ctx_role`."""
    service_role = Role(
        type="service",
        user_id=mock_org_id,
        workspace_id=test_workspace.id,
        service_id="tracecat-runner",
    )
    ctx_role.set(service_role)
    return service_role


@pytest.fixture(scope="session")
def test_admin_role(test_workspace, mock_org_id):
    """Create a test role for the test session and set `ctx_role`."""
    admin_role = Role(
        type="user",
        user_id=mock_org_id,
        workspace_id=test_workspace.id,
        access_level=AccessLevel.ADMIN,
        service_id="tracecat-runner",
    )
    return admin_role


@pytest.fixture(scope="session")
def test_config_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    tmp_path = tmp_path_factory.mktemp("config")
    config_path = tmp_path / "test_config.json"
    yield config_path


@pytest.fixture(scope="session")
def authed_client_controls(test_config_path: Path):
    def _read_config() -> dict[str, Any]:
        try:
            with test_config_path.open() as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get_client():
        url = os.environ["TRACECAT__PUBLIC_API_URL"]
        if cookies_data := _read_config().get("cookies"):
            return httpx.Client(base_url=url, cookies=httpx.Cookies(cookies_data))
        raise ValueError("No cookies found in config")

    def cfg_write(key: str, value: Any | None = None):
        config_data = _read_config()
        if value:
            config_data[key] = value
        else:
            config_data.pop(key, None)
        with test_config_path.open(mode="w") as f:
            json.dump(config_data, f, indent=2)

    def cfg_read(key: str) -> Any | None:
        return _read_config().get(key)

    return get_client, cfg_write, cfg_read


@pytest.fixture(scope="session")
def test_admin_user(env_sandbox, authed_client_controls):
    from tracecat.auth.models import UserCreate, UserRole
    # Login

    url = os.environ["TRACECAT__PUBLIC_API_URL"]
    get_client, cfg_write, cfg_read = authed_client_controls
    email = "testing@tracecat.com"
    password = "1234567890qwer"

    try:
        with httpx.Client(base_url=url) as client:
            response = client.post(
                "/auth/register",
                json=UserCreate(
                    email=email,
                    password=password,
                    role=UserRole.ADMIN,
                    first_name="Test",
                    last_name="User",
                ).model_dump(mode="json"),
            )
            response.raise_for_status()
        logger.info("Registered test admin user", email=email)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            logger.info("Test admin user already registered", email=email)

    # Login
    try:
        with httpx.Client(base_url=url) as client:
            response = client.post(
                "/auth/login",
                data={"username": email, "password": password},
            )
            response.raise_for_status()
            cfg_write("cookies", dict(response.cookies))

        # Current user
        logger.info("Getting current user", read_cfg=cfg_read("cookies"))

        with get_client() as client:
            response = client.get("/users/me")
            response.raise_for_status()
            user_data = response.json()
            user = User(**user_data)
        logger.info("Logged into admin user", user=user)
        yield user
    finally:
        # Logout
        logger.info("Logging out of test session")
        with get_client() as client:
            response = client.post("/auth/logout")
            response.raise_for_status()


@pytest.fixture(scope="session")
def test_workspace(test_admin_user, authed_client_controls):
    """Create a test workspace for the test session."""

    get_client, cfg_write, cfg_read = authed_client_controls
    # Login
    workspace_name = "__test_workspace"
    workspace: WorkspaceMetadataResponse | None = None

    try:
        # First, try to delete any existing test workspace
        with get_client() as client:
            # Get all workspaces
            response = client.get("/workspaces")
            response.raise_for_status()
            workspaces = response.json()

            # Find and delete test workspace if it exists
            for ws in workspaces:
                if ws["name"] == workspace_name:
                    logger.info("Found existing test workspace, deleting it first")
                    delete_response = client.delete(f"/workspaces/{ws['id']}")
                    delete_response.raise_for_status()
                    break

        # Create new test workspace
        with get_client() as client:
            response = client.post(
                "/workspaces",
                json={"name": workspace_name},
            )
            response.raise_for_status()
            workspace = WorkspaceMetadataResponse(**response.json())

        logger.info("Created test workspace", workspace=workspace)
        cfg_write("workspace", workspace.model_dump(mode="json"))

        yield workspace
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            logger.info("Test workspace already exists")
            workspace = cfg_read("workspace")
            if not workspace:
                raise ValueError(
                    "Unexpected error when retrieving workspace. Workspace either doesn't exist or there was a conflict."
                    "Please check the logs for more information, or try viewing the database directly."
                ) from e
            yield WorkspaceMetadataResponse(**workspace.model_dump())
    finally:
        # NOTE: This will remove all test assets created from the DB
        logger.info("Teardown test workspace")
        if workspace:
            with get_client() as client:
                response = client.delete(f"/workspaces/{workspace.id}")
                response.raise_for_status()


@pytest.fixture(scope="function")
def temporal_client():
    from tracecat.dsl.client import get_temporal_client

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

    async with get_async_session_context_manager() as session:
        rr_service = RegistryReposService(session, role=test_role)
        db_repo = await rr_service.create_repository(
            RegistryRepositoryCreate(
                origin="git+ssh://git@github.com/TracecatHQ/dummy-repo.git"
            )
        )
        try:
            yield session, db_repo.id
        finally:
            try:
                await rr_service.delete_repository(db_repo)
                logger.info("Cleaned up db repo")
            except Exception as e:
                logger.error("Error cleaning up repo", e=e)


@pytest.fixture
async def svc_workspace(
    session: AsyncSession,
) -> AsyncGenerator[Workspace, None]:
    """Service test fixture. Create a function scoped test workspace."""
    workspace = Workspace(
        name="test-workspace",
        owner_id=config.TRACECAT__DEFAULT_ORG_ID,
    )  # type: ignore
    session.add(workspace)
    await session.commit()
    try:
        yield workspace
    finally:
        logger.info("Cleaning up test workspace")
        await session.delete(workspace)
        await session.commit()


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
