"""Set up shared environment variables and S3 proxy server (MinIO) for integration tests."""

import os
import subprocess
import sys
import time

import pytest
from loguru import logger

logger.add(
    sink=sys.stderr,
    level="INFO",
    format="{time} | <level>{level: <8}</level> <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> | {extra}",
)


def _temporal_cluster_is_running(compose_file: str) -> bool:
    # Check if the Temporal cluster is already running
    required_services = [
        "temporal",
        "temporal-admin-tools",
        "elasticsearch",
        "postgresql",
        "temporal-ui",
    ]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            compose_file,
            "ps",
            "--services",
            "--filter",
            "status=running",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    running_services = set(result.stdout.strip().split("\n"))
    return all(service in running_services for service in required_services)


def _tracecat_worker_is_running() -> bool:
    # Check if the Tracecat worker is already running
    result = subprocess.run(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        check=True,
        capture_output=True,
        text=True,
    )
    running_services = result.stdout.strip().split("\n")
    return "worker" in running_services


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
