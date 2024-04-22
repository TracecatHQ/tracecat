"""Set up shared environment variables and S3 proxy server (MinIO) for integration tests.
"""

import logging
import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from tracecat.db import Secret
from tracecat.types.secrets import SecretKeyValue

# MinIO settings
MINIO_CONTAINER_NAME = "minio_test_server"
MINIO_ACCESS_KEY = "admin"
MINIO_SECRET_KEY = "password"
MINIO_PORT = 9000
MINIO_REGION = "us-west-2"


# S3 log sample settings
AWS_CLOUDTRAIL__BUCKET_NAME = "aws-cloudtrail-logs"


logging.basicConfig(level="INFO")


@pytest.fixture(autouse=True, scope="session")
def setup_shared_env():
    os.environ["TRACECAT__DB_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["TRACECAT__API_URL"] = "http://api:8000"
    os.environ["TRACECAT__RUNNER_URL"] = "http://runner:8000"
    os.environ["TRACECAT__SERVICE_KEY"] = "test-service-key"

    # AWS
    os.environ["AWS_CLOUDTRAIL__BUCKET_NAME"] = AWS_CLOUDTRAIL__BUCKET_NAME

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


@pytest.fixture(scope="session")
def create_mock_secret():
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
