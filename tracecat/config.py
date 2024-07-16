import os
from pathlib import Path

HTTP_MAX_RETRIES = 10
LLM_MAX_RETRIES = 3

TRACECAT_DIR = (
    Path(os.environ.get("TRACECAT_DIR", "~/.tracecat")).expanduser().resolve()
)
TRACECAT__SCHEDULE_INTERVAL_SECONDS = os.environ.get(
    "TRACECAT__SCHEDULE_INTERVAL_SECONDS", 60
)
TRACECAT__SCHEDULE_MAX_CONNECTIONS = 6
TRACECAT__APP_ENV = os.environ.get("TRACECAT__APP_ENV", "development")
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://localhost:8000")
TRACECAT__PUBLIC_RUNNER_URL = os.environ.get(
    "TRACECAT__PUBLIC_RUNNER_URL", "http://localhost:8001"
)

TRACECAT__DB_URI = os.environ.get(
    "TRACECAT__DB_URI",
    "postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres",
)

TRACECAT__DB_NAME = os.environ.get("TRACECAT__DB_NAME")
TRACECAT__DB_USER = os.environ.get("TRACECAT__DB_USER")
TRACECAT__DB_PASS = os.environ.get("TRACECAT__DB_PASS")
TRACECAT__DB_ENDPOINT = os.environ.get("TRACECAT__DB_ENDPOINT")
TRACECAT__DB_PORT = os.environ.get("TRACECAT__DB_PORT")

TRACECAT__TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
TRACECAT__TRIAGE_DIR = TRACECAT_DIR / "triage"
TRACECAT__TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
TRACECAT__EXECUTIONS_DIR = TRACECAT_DIR / "executions"
TRACECAT__EXECUTIONS_DIR.mkdir(parents=True, exist_ok=True)

# TODO: Set this as an environment variable
TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-runner",
    "tracecat-api",
    "tracecat-cli",
    "tracecat-schedule-runner",
]

# CORS settings
# NOTE: If you are using Tracecat self-hosted, please replace with your
# own domain by setting the comma separated TRACECAT__ALLOW_ORIGINS env var.
TRACECAT__ALLOW_ORIGINS = os.environ.get("TRACECAT__ALLOW_ORIGINS")
if TRACECAT__ALLOW_ORIGINS:
    TRACECAT__ALLOW_ORIGINS = TRACECAT__ALLOW_ORIGINS.split(",")

# Temporal configs
TEMPORAL__CLUSTER_URL = os.environ.get(
    "TEMPORAL__CLUSTER_URL", "http://localhost:7233"
)  # AKA Temporal target host
TEMPORAL__CLUSTER_NAMESPACE = os.environ.get(
    "TEMPORAL__CLUSTER_NAMESPACE", "default"
)  # Temporal namespace
TEMPORAL__CLUSTER_QUEUE = os.environ.get(
    "TEMPORAL__CLUSTER_QUEUE", "tracecat-task-queue"
)  # Temporal task queue
TEMPORAL__TLS_ENABLED = os.environ.get("TEMPORAL__TLS_ENABLED", False)
TEMPORAL__TLS_ENABLED = os.environ.get("TEMPORAL__TLS_ENABLED", False)
TEMPORAL__TLS_CLIENT_CERT = os.environ.get("TEMPORAL__TLS_CLIENT_CERT")
TEMPORAL__TLS_CLIENT_PRIVATE_KEY = os.environ.get("TEMPORAL__TLS_CLIENT_PRIVATE_KEY")

# Tenacity Retry Settings
RETRY_EXPONENTIAL_MULTIPLIER = 1
RETRY_MIN_WAIT_TIME = 1
RETRY_MAX_WAIT_TIME = 60
RETRY_STOP_AFTER_ATTEMPT = 5
