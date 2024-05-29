import os
import subprocess
import time
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from loguru import logger


def pytest_addoption(parser):
    parser.addoption(
        "--temporal-compose-file",
        action="store",
        default="../temporal/docker-compose/docker-compose.yml",
        help="Path to Temporal's docker-compose.yml file",
    )


@pytest.fixture(autouse=True, scope="session")
def monkeysession(request):
    mpatch = pytest.MonkeyPatch()
    yield mpatch
    mpatch.undo()


# NOTE: Don't auto-use this fixture unless necessary
@pytest.fixture(scope="session")
def auth_sandbox():
    from tracecat.auth import Role
    from tracecat.contexts import ctx_role

    service_role = Role(
        type="service", user_id="test-tracecat-user", service_id="tracecat-testing"
    )
    ctx_role.set(service_role)
    yield


@pytest.fixture(autouse=True, scope="session")
def env_sandbox(monkeysession, request: pytest.FixtureRequest):
    logger.info("Setting up environment variables")
    temporal_compose_file = request.config.getoption("--temporal-compose-file")
    monkeysession.setenv(
        "TRACECAT__DB_URI",
        "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
    )
    monkeysession.setenv("TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeysession.setenv("TRACECAT__API_URL", "http://api:8000")
    monkeysession.setenv("TRACECAT__RUNNER_URL", "http://runner:8000")
    monkeysession.setenv("TRACECAT__PUBLIC_RUNNER_URL", "http://localhost:8001")
    monkeysession.setenv("TRACECAT__SERVICE_KEY", "test-service-key")
    monkeysession.setenv("TEMPORAL__DOCKER_COMPOSE_PATH", temporal_compose_file)
    # When launching the worker directly in a test, use localhost
    # If the worker is running inside a container, use host.docker.internal
    monkeysession.setenv("TEMPORAL__CLUSTER_URL", "http://localhost:7233")
    monkeysession.setenv("TEMPORAL__CLUSTER_QUEUE", "test-dsl-task-queue")
    yield
    # Cleanup is automatic with monkeypatch
    logger.info("Environment variables cleaned up")


@pytest.fixture(scope="session")
def create_mock_secret():
    from tracecat.db.models import Secret
    from tracecat.types.secrets import SecretKeyValue

    def _get_secret(secret_name: str, secrets: dict[str, str]) -> list[Secret]:
        keys = [SecretKeyValue(key=k, value=v) for k, v in secrets.items()]
        new_secret = Secret(
            owner_id=uuid4().hex,  # Assuming owner_id should be unique per secret
            id=uuid4().hex,  # Generate a unique ID for each secret
            name=secret_name,
            type="custom",  # Assuming a fixed type; adjust as necessary
        )
        new_secret.keys = keys
        return new_secret

    return _get_secret


@pytest.fixture(scope="session")
def temporal_cluster(env_sandbox):
    compose_file = os.environ["TEMPORAL__DOCKER_COMPOSE_PATH"]
    logger.info(
        "Setting up Temporal cluster",
        compose_file=compose_file,
    )
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
def tracecat_stack(env_sandbox):
    logger.info("Setup Tracecat stack")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "api", "postgres_db"], check=True
        )
        time.sleep(5)  # Wait for the cluster to start
        logger.info("Tracecat stack started")

        yield
    finally:
        logger.info("Shutting down Tracecat stack")
        subprocess.run(["docker", "compose", "down", "--remove-orphans"], check=True)
        logger.info("Successfully shut down Tracecat stack")
