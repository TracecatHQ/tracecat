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

TRACECAT__DB_URI = os.environ.get(
    "TRACECAT__DB_URI",
    "postgresql+psycopg://postgres:postgres@postgres_db:5432/postgres",
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
TRACECAT__DB_NAME = os.environ.get("TRACECAT__DB_NAME")
TRACECAT__DB_USER = os.environ.get("TRACECAT__DB_USER")
TRACECAT__DB_PASS = os.environ.get("TRACECAT__DB_PASS")
TRACECAT__DB_PASS__ARN = os.environ.get("TRACECAT__DB_PASS__ARN")
TRACECAT__DB_ENDPOINT = os.environ.get("TRACECAT__DB_ENDPOINT")
TRACECAT__DB_PORT = os.environ.get("TRACECAT__DB_PORT")

# TODO: Set this as an environment variable
TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-runner",
    "tracecat-api",
    "tracecat-cli",
    "tracecat-schedule-runner",
]
TRACECAT__DEFAULT_USER_ID = uuid.UUID(int=0)
TRACECAT__DEFAULT_ORG_ID = uuid.UUID(int=0)

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
"""Deprecated: This config has been moved into the settings service"""

XMLSEC_BINARY_PATH = os.environ.get("XMLSEC_BINARY_PATH", "/usr/bin/xmlsec1")

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
TEMPORAL__MTLS_ENABLED = os.environ.get("TEMPORAL__MTLS_ENABLED", "").lower() in (
    "1",
    "true",
)
TEMPORAL__MTLS_CERT__ARN = os.environ.get("TEMPORAL__MTLS_CERT__ARN")
TEMPORAL__CLIENT_RPC_TIMEOUT = os.environ.get("TEMPORAL__CLIENT_RPC_TIMEOUT")
"""RPC timeout for Temporal workflows in seconds."""

TEMPORAL__TASK_TIMEOUT = os.environ.get("TEMPORAL__TASK_TIMEOUT")
"""Temporal workflow task timeout in seconds (default 10 seconds)."""

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
"""Deprecated: This config has been moved into the settings service"""
# If you wish to use a remote registry, set the URL here
# If the url is unset, this will be set to None
TRACECAT__REMOTE_REPOSITORY_URL = (
    os.environ.get("TRACECAT__REMOTE_REPOSITORY_URL") or None
)
"""Deprecated: This config has been moved into the settings service"""
TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME = os.getenv(
    "TRACECAT__REMOTE_REPOSITORY_PACKAGE_NAME"
)
"""If not provided, the package name will be inferred from the git remote URL.

Deprecated: This config has been moved into the settings service
"""

# === Email settings === #
TRACECAT__ALLOWED_EMAIL_ATTRIBUTES = os.environ.get(
    "TRACECAT__ALLOWED_EMAIL_ATTRIBUTES"
)
# === AI settings === #
TRACECAT__PRELOAD_OSS_MODELS = (
    (models := os.getenv("TRACECAT__PRELOAD_OSS_MODELS")) and models.split(",")
) or []

OLLAMA__API_URL = os.environ.get("OLLAMA__API_URL", "http://ollama:11434")

# === Local registry === #
TRACECAT__LOCAL_REPOSITORY_PATH = os.getenv("TRACECAT__LOCAL_REPOSITORY_PATH")
TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH = "/app/local_registry"
