import os
import uuid
from typing import Literal

from tracecat.auth.enums import AuthType

# === Internal Services === #
TRACECAT__APP_ENV: Literal["development", "staging", "production"] = os.environ.get(
    "TRACECAT__APP_ENV", "development"
)  # type: ignore
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://localhost:8000")
TRACECAT__API_ROOT_PATH = os.environ.get("TRACECAT__API_ROOT_PATH", "/api")
TRACECAT__PUBLIC_API_URL = os.environ.get(
    "TRACECAT__PUBLIC_API_URL", "http://localhost/api"
)
TRACECAT__PUBLIC_APP_URL = os.environ.get(
    "TRACECAT__PUBLIC_APP_URL", "http://localhost"
)

TRACECAT__EXECUTOR_URL = os.environ.get(
    "TRACECAT__EXECUTOR_URL", "http://executor:8000"
)
TRACECAT__EXECUTOR_CLIENT_TIMEOUT = float(
    os.environ.get("TRACECAT__EXECUTOR_CLIENT_TIMEOUT", 120.0)
)
"""Timeout for the executor client in seconds (default 120s).

The `httpx.Client` default is 5s, which doesn't work for long-running actions.
"""
TRACECAT__LOOP_MAX_BATCH_SIZE = int(os.environ.get("TRACECAT__LOOP_MAX_BATCH_SIZE", 64))
"""Maximum number of parallel requests to the worker service."""

# TODO: Set this as an environment variable
TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-api",
    "tracecat-cli",
    "tracecat-runner",
    "tracecat-schedule-runner",
    "tracecat-ui",
]
TRACECAT__DEFAULT_USER_ID = uuid.UUID(int=0)
TRACECAT__DEFAULT_ORG_ID = uuid.UUID(int=0)

# === DB Config === #
TRACECAT__DB_URI = os.environ.get(
    "TRACECAT__DB_URI",
    "postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres",
)
TRACECAT__DB_NAME = os.environ.get("TRACECAT__DB_NAME")
"""The name of the database to connect to."""
TRACECAT__DB_USER = os.environ.get("TRACECAT__DB_USER")
"""The user to connect to the database with."""
TRACECAT__DB_PASS = os.environ.get("TRACECAT__DB_PASS")
"""The password to connect to the database with."""
TRACECAT__DB_ENDPOINT = os.environ.get("TRACECAT__DB_ENDPOINT")
"""The endpoint to connect to the database on."""
TRACECAT__DB_PORT = os.environ.get("TRACECAT__DB_PORT")
"""The port to connect to the database on."""
TRACECAT__DB_SSLMODE = os.environ.get("TRACECAT__DB_SSLMODE", "require")
"""The SSL mode to connect to the database with."""

TRACECAT__DB_PASS__ARN = os.environ.get("TRACECAT__DB_PASS__ARN")
"""(AWS only) ARN of the secret to connect to the database with."""

TRACECAT__DB_MAX_OVERFLOW = int(os.environ.get("TRACECAT__DB_MAX_OVERFLOW", 30))
"""The maximum number of connections to allow in the pool."""
TRACECAT__DB_POOL_SIZE = int(os.environ.get("TRACECAT__DB_POOL_SIZE", 30))
"""The size of the connection pool."""
TRACECAT__DB_POOL_TIMEOUT = int(os.environ.get("TRACECAT__DB_POOL_TIMEOUT", 30))
"""The timeout for the connection pool."""
TRACECAT__DB_POOL_RECYCLE = int(os.environ.get("TRACECAT__DB_POOL_RECYCLE", 1800))
"""The time to recycle the connection pool."""

# === Auth config === #
# Infrastructure config
TRACECAT__AUTH_TYPES = {
    AuthType(t.lower())
    for t in os.environ.get("TRACECAT__AUTH_TYPES", "basic,google_oauth").split(",")
}
"""The set of allowed auth types on the platform. If an auth type is not in this set,
it cannot be enabled."""

TRACECAT__AUTH_REQUIRE_EMAIL_VERIFICATION = os.environ.get(
    "TRACECAT__AUTH_REQUIRE_EMAIL_VERIFICATION", ""
).lower() in ("true", "1")  # Default to False
SESSION_EXPIRE_TIME_SECONDS = int(
    os.environ.get("SESSION_EXPIRE_TIME_SECONDS") or 86400 * 7
)  # 7 days
TRACECAT__AUTH_ALLOWED_DOMAINS = set(
    ((domains := os.getenv("TRACECAT__AUTH_ALLOWED_DOMAINS")) and domains.split(","))
    or []
)
"""Deprecated: This config has been moved into the settings service"""

TRACECAT__AUTH_MIN_PASSWORD_LENGTH = int(
    os.environ.get("TRACECAT__AUTH_MIN_PASSWORD_LENGTH") or 12
)

TRACECAT__AUTH_SUPERADMIN_EMAIL = os.environ.get("TRACECAT__AUTH_SUPERADMIN_EMAIL")
"""Email address that is allowed to become the first superuser. If not set, the first user logic is disabled for security."""

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

# SAML SSO

SAML_PUBLIC_ACS_URL = f"{TRACECAT__PUBLIC_APP_URL}/auth/saml/acs"

SAML_IDP_METADATA_URL = os.environ.get("SAML_IDP_METADATA_URL")
"""Sets the default SAML metadata URL for cold start."""

SAML_ALLOW_UNSOLICITED = (
    os.environ.get("SAML_ALLOW_UNSOLICITED", "true").lower() == "true"
)
"""Whether to allow unsolicited SAML responses."""

SAML_AUTHN_REQUESTS_SIGNED = (
    os.environ.get("SAML_AUTHN_REQUESTS_SIGNED", "true").lower() == "true"
)
"""Whether to require signed SAML authentication requests."""

SAML_SIGNED_ASSERTIONS = (
    os.environ.get("SAML_SIGNED_ASSERTIONS", "true").lower() == "true"
)
"""Whether to require signed SAML assertions."""

SAML_SIGNED_RESPONSES = (
    os.environ.get("SAML_SIGNED_RESPONSES", "true").lower() == "true"
)
"""Whether to require signed SAML responses."""

SAML_ACCEPTED_TIME_DIFF = int(os.environ.get("SAML_ACCEPTED_TIME_DIFF", "3"))
"""The time difference in seconds for SAML authentication."""

XMLSEC_BINARY_PATH = os.environ.get("XMLSEC_BINARY_PATH", "/usr/bin/xmlsec1")

SAML_CA_CERTS = os.environ.get("SAML_CA_CERTS")
"""Base64 encoded CA certificates for validating self-signed certificates."""

SAML_VERIFY_SSL_ENTITY = (
    os.environ.get("SAML_VERIFY_SSL_ENTITY", "true").lower() == "true"
)
"""Whether to verify SSL certificates for general SAML entity operations."""

SAML_VERIFY_SSL_METADATA = (
    os.environ.get("SAML_VERIFY_SSL_METADATA", "true").lower() == "true"
)
"""Whether to verify SSL certificates for SAML metadata operations."""

# === CORS config === #
# NOTE: If you are using Tracecat self-hosted, please replace with your
# own domain by setting the comma separated TRACECAT__ALLOW_ORIGINS env var.
TRACECAT__ALLOW_ORIGINS = os.environ.get("TRACECAT__ALLOW_ORIGINS")

# === Temporal config === #
TEMPORAL__CONNECT_RETRIES = int(os.environ.get("TEMPORAL__CONNECT_RETRIES", 10))
TEMPORAL__CLUSTER_URL = os.environ.get(
    "TEMPORAL__CLUSTER_URL", "http://localhost:7233"
)  # AKA TEMPORAL_HOST_URL
TEMPORAL__CLUSTER_NAMESPACE = os.environ.get(
    "TEMPORAL__CLUSTER_NAMESPACE", "default"
)  # AKA TEMPORAL_NAMESPACE
TEMPORAL__CLUSTER_QUEUE = os.environ.get(
    "TEMPORAL__CLUSTER_QUEUE", "tracecat-task-queue"
)
TEMPORAL__API_KEY__ARN = os.environ.get("TEMPORAL__API_KEY__ARN")
TEMPORAL__API_KEY = os.environ.get("TEMPORAL__API_KEY")
TEMPORAL__CLIENT_RPC_TIMEOUT = os.environ.get("TEMPORAL__CLIENT_RPC_TIMEOUT")
"""RPC timeout for Temporal workflows in seconds."""

TEMPORAL__TASK_TIMEOUT = os.environ.get("TEMPORAL__TASK_TIMEOUT")
"""Temporal workflow task timeout in seconds (default 10 seconds)."""

TEMPORAL__METRICS_PORT = os.environ.get("TEMPORAL__METRICS_PORT")
"""Port for the Temporal metrics server."""


TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION = os.environ.get(
    "TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION", "true"
).lower() in ("true", "1")
"""Disable eager activity execution for Temporal workflows."""

# Secrets manager config
TRACECAT__UNSAFE_DISABLE_SM_MASKING = os.environ.get(
    "TRACECAT__UNSAFE_DISABLE_SM_MASKING",
    "0",  # Default to False
).lower() in ("1", "true")
"""Disable masking of secrets in the secrets manager.
    WARNING: This is only be used for testing and debugging purposes during
    development and should never be enabled in production.
"""

# === M2M config === #
TRACECAT__SERVICE_KEY = os.environ.get("TRACECAT__SERVICE_KEY")

# === Remote registry === #
TRACECAT__ALLOWED_GIT_DOMAINS = set(
    os.environ.get(
        "TRACECAT__ALLOWED_GIT_DOMAINS", "github.com,gitlab.com,bitbucket.org"
    ).split(",")
)

# === Blob Storage Config === #
TRACECAT__BLOB_STORAGE_PROTOCOL = os.environ.get(
    "TRACECAT__BLOB_STORAGE_PROTOCOL", "minio"
)
"""Blob storage protocol: 's3' for AWS S3, 'minio' for Minio."""

TRACECAT__BLOB_STORAGE_BUCKET = os.environ.get(
    "TRACECAT__BLOB_STORAGE_BUCKET", "tracecat"
)
"""The name of the blob storage bucket."""

TRACECAT__BLOB_STORAGE_ENDPOINT = os.environ.get(
    "TRACECAT__BLOB_STORAGE_ENDPOINT", "http://minio:9000"
)
"""Endpoint URL for Minio. Ignored when protocol is 's3'."""

# Presigned URL configuration
TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = int(
    os.environ.get("TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY", 3600)
)
"""Default expiry time for presigned URLs in seconds (default: 1 hour)."""

# === Local registry === #
TRACECAT__LOCAL_REPOSITORY_ENABLED = os.getenv(
    "TRACECAT__LOCAL_REPOSITORY_ENABLED", "0"
).lower() in ("1", "true")
TRACECAT__LOCAL_REPOSITORY_PATH = os.getenv("TRACECAT__LOCAL_REPOSITORY_PATH")
TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH = "/app/local_registry"

# === Python Script Execution === #
TRACECAT__PYODIDE_VERSION = os.environ.get("PYODIDE_VERSION", "0.27.6")
"""Version of Pyodide to use for Python script execution in WebAssembly sandbox."""

TRACECAT__NODE_MODULES_DIR = os.environ.get(
    "NODE_MODULES_DIR", "/home/apiuser/.local/lib/node_modules"
)
"""Directory where Node.js modules are installed for Deno/Pyodide execution."""

# === Rate Limiting === #
TRACECAT__RATE_LIMIT_ENABLED = (
    os.environ.get("TRACECAT__RATE_LIMIT_ENABLED", "true").lower() == "true"
)
"""Whether rate limiting is enabled for the executor service."""

TRACECAT__RATE_LIMIT_RATE = float(os.environ.get("TRACECAT__RATE_LIMIT_RATE", 40.0))
"""The rate at which tokens are added to the bucket (tokens per second)."""

TRACECAT__RATE_LIMIT_CAPACITY = float(
    os.environ.get("TRACECAT__RATE_LIMIT_CAPACITY", 80.0)
)
"""The maximum number of tokens the bucket can hold."""

TRACECAT__RATE_LIMIT_WINDOW_SIZE = int(
    os.environ.get("TRACECAT__RATE_LIMIT_WINDOW_SIZE", 60)
)
"""The time window in seconds for rate limiting."""

TRACECAT__RATE_LIMIT_BY_IP = (
    os.environ.get("TRACECAT__RATE_LIMIT_BY_IP", "true").lower() == "true"
)
"""Whether to rate limit by client IP."""

TRACECAT__RATE_LIMIT_BY_ENDPOINT = (
    os.environ.get("TRACECAT__RATE_LIMIT_BY_ENDPOINT", "true").lower() == "true"
)
"""Whether to rate limit by endpoint."""

TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES = int(
    os.environ.get("TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES", 1024 * 1024)
)
"""The maximum size of a payload in bytes the executor can return. Defaults to 1MB"""

TRACECAT__MAX_FILE_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_FILE_SIZE_BYTES", 20 * 1024 * 1024)  # Default 20MB
)
"""The maximum size for file handling (e.g., uploads, downloads) in bytes. Defaults to 20MB."""

TRACECAT__MAX_UPLOAD_FILES_COUNT = int(
    os.environ.get("TRACECAT__MAX_UPLOAD_FILES_COUNT", 5)
)
"""The maximum number of files that can be uploaded at once. Defaults to 5."""

TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES", 100 * 1024 * 1024)
)
"""The maximum size of the aggregate upload size in bytes. Defaults to 100MB."""

# === System PATH config === #
TRACECAT__SYSTEM_PATH = os.environ.get(
    "TRACECAT__SYSTEM_PATH", "/usr/local/bin:/usr/bin:/bin"
)
"""System PATH for subprocess execution. Includes common binary locations."""

# === Concurrency Limits === #
TRACECAT__S3_CONCURRENCY_LIMIT = int(
    os.environ.get("TRACECAT__S3_CONCURRENCY_LIMIT", 50)
)
"""Maximum number of concurrent S3 operations to prevent resource exhaustion. Defaults to 50."""

TRACECAT__MAX_ROWS_CLIENT_POSTGRES = int(
    os.environ.get("TRACECAT__MAX_ROWS_CLIENT_POSTGRES", 1000)
)
"""Maximum number of rows that can be returned from PostgreSQL client queries. Defaults to 1,000."""
