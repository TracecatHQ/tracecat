import os

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def setup_shared_env():
    os.environ["TRACECAT__DB_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["TRACECAT__API_URL"] = "http://api:8000"
    os.environ["TRACECAT__RUNNER_URL"] = "http://runner:8000"
    os.environ["TRACECAT__SERVICE_KEY"] = "test-service-key"

    try:
        yield
    finally:
        del os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        del os.environ["TRACECAT__API_URL"]
        del os.environ["TRACECAT__RUNNER_URL"]
        del os.environ["TRACECAT__SERVICE_KEY"]
