"""Set up shared environment variables and S3 proxy server (MinIO) for integration tests.
"""

import os
import subprocess
import time

import pytest
from cryptography.fernet import Fernet
from minio import Minio

from tracecat.logger import standard_logger

logger = standard_logger("tests")


# MinIO settings
MINIO_CONTAINER_NAME = "minio_test_server"
MINIO_ACCESS_KEY = "admin"
MINIO_SECRET_KEY = "password"
MINIO_PORT = 9000
MINIO_REGION = "us-west-2"


# S3 log sample settings
AWS_CLOUDTRAIL__BUCKET_NAME = "aws-cloudtrail-logs"


@pytest.fixture(autouse=True)
def setup_shared_env():
    os.environ["TRACECAT__DB_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["TRACECAT__API_URL"] = "http://api:8000"
    os.environ["TRACECAT__RUNNER_URL"] = "http://runner:8000"
    os.environ["TRACECAT__SERVICE_KEY"] = "test-service-key"

    # MinIO Client (local)
    os.environ["MINIO_ACCESS_KEY"] = MINIO_ACCESS_KEY
    os.environ["MINIO_SECRET_KEY"] = MINIO_SECRET_KEY
    os.environ["MINIO_ENDPOINT"] = f"localhost:{MINIO_PORT}"

    try:
        yield
    finally:
        del os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        del os.environ["TRACECAT__API_URL"]
        del os.environ["TRACECAT__RUNNER_URL"]
        del os.environ["TRACECAT__SERVICE_KEY"]
        del os.environ["MINIO_ACCESS_KEY"]
        del os.environ["MINIO_SECRET_KEY"]
        del os.environ["MINIO_ENDPOINT"]


@pytest.fixture(scope="session", autouse=True)
def minio_container():
    # Check if the MinIO container is already running
    existing_containers = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={MINIO_CONTAINER_NAME}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
    )

    container_exists = MINIO_CONTAINER_NAME in existing_containers.stdout.strip()
    logger.info("🐳 MinIO container exists: %r", container_exists)

    if not container_exists:
        # Setup: Start MinIO server
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                MINIO_CONTAINER_NAME,
                "-p",
                f"{MINIO_PORT}:{MINIO_PORT}",
                "-e",
                f"MINIO_ACCESS_KEY={MINIO_ACCESS_KEY}",
                "-e",
                f"MINIO_SECRET_KEY={MINIO_SECRET_KEY}",
                "minio/minio",
                "server",
                "/data",
            ],
            check=True,
        )
        # Wait for the server to start
        time.sleep(5)
        logger.info("✅ Created minio container %r", MINIO_CONTAINER_NAME)
    else:
        logger.info("✅ Using existing minio container %r", MINIO_CONTAINER_NAME)

    # Connect to MinIO
    client = Minio(
        f"localhost:{MINIO_PORT}",
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )

    # Create or connect to AWS CloudTrail bucket
    bucket = AWS_CLOUDTRAIL__BUCKET_NAME
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("✅ Created minio bucket %r", bucket)

    yield
    should_cleanup = os.getenv("MINIO_CLEANUP", "1").lower() in (
        "true",
        "1",
    )
    if not container_exists and should_cleanup:
        logger.info("🧹 Cleaning up minio container %r", MINIO_CONTAINER_NAME)
        subprocess.run(["docker", "stop", MINIO_CONTAINER_NAME], check=True)
    else:
        logger.info(
            "🧹 Skipping cleanup of minio container %r. Set `MINIO_CLEANUP` to cleanup.",
            MINIO_CONTAINER_NAME,
        )
