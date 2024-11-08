import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.schemas import User
from tracecat.logger import logger
from tracecat.registry.repository import Repository
from tracecat.types.auth import Role
from tracecat.workspaces.models import WorkspaceMetadataResponse


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--temporal-compose-file",
        action="store",
        default="../temporal/docker-compose/docker-compose.yml",
        help="Path to Temporal's docker-compose.yml file",
    )
    parser.addoption(
        "--temporal-no-restart",
        action="store_true",
        default=False,
        help="Do not restart the Temporal cluster if it is already running",
    )

    parser.addoption(
        "--tracecat-no-restart",
        action="store_true",
        default=False,
        help="Do not restart the Tracecat stack if it is already running",
    )


@pytest.fixture(autouse=True)
def check_disable_fixture(request):
    marker = request.node.get_closest_marker("disable_fixture")
    if marker and marker.args[0] == "test_user":
        pytest.skip("Test user fixture disabled for this test or module")


@pytest.fixture(autouse=True, scope="session")
def event_loop():
    logger.info("Creating event loop")
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True, scope="session")
def env_sandbox(
    monkeysession: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    import dotenv

    dotenv.load_dotenv()
    logger.info("Setting up environment variables")
    temporal_compose_file = request.config.getoption("--temporal-compose-file")

    monkeysession.setattr(
        config,
        "TRACECAT__DB_URI",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    monkeysession.setattr(config, "TEMPORAL__CLUSTER_URL", "http://localhost:7233")
    monkeysession.setattr(
        config,
        "TRACECAT__REMOTE_REPOSITORY_URL",
        "git+ssh://git@github.com/TracecatHQ/udfs.git",
    )

    monkeysession.setenv(
        "TRACECAT__DB_URI",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    # monkeysession.setenv("TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeysession.setenv("TRACECAT__API_URL", "http://api:8000")
    monkeysession.setenv("TRACECAT__PUBLIC_API_URL", "http://localhost/api")
    monkeysession.setenv("TRACECAT__PUBLIC_RUNNER_URL", "http://localhost:8001")
    monkeysession.setenv("TRACECAT__SERVICE_KEY", os.environ["TRACECAT__SERVICE_KEY"])
    monkeysession.setenv("TRACECAT__SIGNING_SECRET", "test-signing-secret")
    monkeysession.setenv("TEMPORAL__DOCKER_COMPOSE_PATH", temporal_compose_file)
    # When launching the worker directly in a test, use localhost
    # If the worker is running inside a container, use host.docker.internal
    monkeysession.setenv("TEMPORAL__CLUSTER_URL", "http://localhost:7233")
    monkeysession.setenv("TEMPORAL__CLUSTER_QUEUE", "test-tracecat-task-queue")
    monkeysession.setenv("TEMPORAL__CLUSTER_NAMESPACE", "default")
    yield
    # Cleanup is automatic with monkeypatch
    logger.info("Environment variables cleaned up")


@pytest.fixture(autouse=True, scope="session")
def monkeysession(request):
    mpatch = pytest.MonkeyPatch()
    yield mpatch
    mpatch.undo()


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
def temporal_cluster(pytestconfig: pytest.Config, env_sandbox):
    compose_file = os.environ["TEMPORAL__DOCKER_COMPOSE_PATH"]
    logger.info(
        "Setting up Temporal cluster",
        compose_file=compose_file,
    )

    no_restart = pytestconfig.getoption("--temporal-no-restart")
    if no_restart:
        logger.info("Skipping Temporal cluster setup")
        yield
    else:
        try:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "up", "-d"], check=True
            )
            time.sleep(10)  # Wait for the cluster to start
            logger.info("Temporal started")

            yield  # Run the tests

        finally:
            logger.info("Shutting down Temporal cluster")
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "down", "--remove-orphans"],
                check=True,
            )
            logger.info("Successfully shut down Temporal cluster")


@pytest.fixture(scope="session")
def tracecat_stack(pytestconfig: pytest.Config, env_sandbox):
    logger.info("Setup Tracecat stack")
    no_restart = pytestconfig.getoption("--tracecat-no-restart")
    if no_restart:
        logger.info("Skipping Tracecat stack setup")
        yield
    else:
        try:
            subprocess.run(
                ["docker", "compose", "up", "-d", "api", "postgres_db"], check=True
            )
            time.sleep(5)  # Wait for the cluster to start
            logger.info("Tracecat stack started")

            yield
        finally:
            logger.info("Shutting down Tracecat stack")
            subprocess.run(
                ["docker", "compose", "down", "--remove-orphans"], check=True
            )
            logger.info("Successfully shut down Tracecat stack")


@pytest.fixture(scope="session")
def tracecat_worker(env_sandbox):
    # Start the Tracecat Temporal worker
    # The worker is in our main tracecat docker compose file
    try:
        # Check that worker is not already running
        logger.info("Starting Tracecat Temporal worker")
        env_copy = os.environ.copy()
        # As the worker is running inside a container, use host.docker.internal
        env_copy["TEMPORAL__CLUSTER_URL"] = "http://host.docker.internal:7233"
        subprocess.run(
            ["docker", "compose", "up", "-d", "worker"],
            check=True,
            env=env_copy,
        )
        time.sleep(5)

        yield
    finally:
        logger.info("Stopping Tracecat Temporal worker")
        subprocess.run(
            ["docker", "compose", "down", "--remove-orphans", "worker"], check=True
        )
        logger.info("Stopped Tracecat Temporal worker")


@pytest.fixture(scope="session", autouse=True)
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


@pytest.fixture(autouse=True, scope="session")
def test_admin_user(env_sandbox, authed_client_controls):
    # Login

    url = os.environ["TRACECAT__PUBLIC_API_URL"]
    get_client, cfg_write, cfg_read = authed_client_controls

    # Login
    try:
        with httpx.Client(base_url=url) as client:
            response = client.post(
                "/auth/login",
                data={"username": "admin@domain.com", "password": "password"},
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


@pytest.fixture(autouse=True, scope="session")
def test_workspace(test_admin_user, authed_client_controls):
    """Create a test workspace for the test session."""

    get_client, cfg_write, cfg_read = authed_client_controls
    # Login
    workspace: WorkspaceMetadataResponse | None = None
    try:
        with get_client() as client:
            response = client.post(
                "/workspaces",
                json={"name": "__test_workspace"},
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


@pytest.fixture(scope="session")
def temporal_client():
    from tracecat.dsl.client import get_temporal_client

    loop = asyncio.get_event_loop()
    client = loop.run_until_complete(get_temporal_client())
    return client


@pytest.fixture
def base_registry():
    try:
        registry = Repository()
        registry.init(include_base=True, include_templates=False)
        yield registry
    finally:
        registry._reset()
