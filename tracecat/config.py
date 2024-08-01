import os
import uuid

from tracecat.auth.constants import AuthType

# === Actions Config === #
HTTP_MAX_RETRIES = 10
LLM_MAX_RETRIES = 3

# === Internal Services === #
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

TRACECAT__API_ROOT_PATH = os.environ.get("TRACECAT__API_ROOT_PATH", "/api")

# TODO: Set this as an environment variable
TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-runner",
    "tracecat-api",
    "tracecat-cli",
    "tracecat-schedule-runner",
]
TRACECAT__DEFAULT_USER_ID = uuid.UUID(int=0)

# === DB Config === #
TRACECAT__DB_URI = os.environ.get(
    "TRACECAT__DB_URI",
    "postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres",
)
TRACECAT__DB_NAME = os.environ.get("TRACECAT__DB_NAME")
TRACECAT__DB_USER = os.environ.get("TRACECAT__DB_USER")
TRACECAT__DB_PASS = os.environ.get("TRACECAT__DB_PASS")
TRACECAT__DB_ENDPOINT = os.environ.get("TRACECAT__DB_ENDPOINT")
TRACECAT__DB_PORT = os.environ.get("TRACECAT__DB_PORT")

# === Auth config === #
TRACECAT__AUTH_TYPE = AuthType(
    os.environ.get("TRACECAT__AUTH_TYPE", AuthType.DISABLED.value).lower()
)
TRACECAT__AUTH_DISABLED = os.environ.get("TRACECAT__DISABLE_AUTH", "0").lower() in (
    "true",
    "1",
)
# OAuth Login Flow
# Used for both Google OAuth2 and OIDC flows
OAUTH_CLIENT_ID = (
    os.environ.get("OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or ""
)
OAUTH_CLIENT_SECRET = (
    os.environ.get("OAUTH_CLIENT_SECRET")
    or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    or ""
)
USER_AUTH_SECRET = os.environ.get("USER_AUTH_SECRET", "")

# === CORS config === #
# NOTE: If you are using Tracecat self-hosted, please replace with your
# own domain by setting the comma separated TRACECAT__ALLOW_ORIGINS env var.
TRACECAT__ALLOW_ORIGINS = os.environ.get("TRACECAT__ALLOW_ORIGINS")

# === Temporal config === #
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

# === Tenacity config === #
RETRY_EXPONENTIAL_MULTIPLIER = 1
RETRY_MIN_WAIT_TIME = 1
RETRY_MAX_WAIT_TIME = 60
RETRY_STOP_AFTER_ATTEMPT = 5
