import logging
import os
import uuid
from typing import Literal, cast

from tracecat.auth.enums import AuthType
from tracecat.feature_flags.enums import FeatureFlag

# === Logger === #
logger = logging.getLogger(__name__)

# === Internal Services === #
TRACECAT__APP_ENV: Literal["development", "staging", "production"] = cast(
    Literal["development", "staging", "production"],
    os.environ.get("TRACECAT__APP_ENV", "development"),
)
TRACECAT__API_URL = os.environ.get("TRACECAT__API_URL", "http://localhost:8000")
TRACECAT__API_ROOT_PATH = os.environ.get("TRACECAT__API_ROOT_PATH", "/api")
TRACECAT__PUBLIC_API_URL = os.environ.get(
    "TRACECAT__PUBLIC_API_URL", "http://localhost/api"
)
TRACECAT__PUBLIC_APP_URL = os.environ.get(
    "TRACECAT__PUBLIC_APP_URL", "http://localhost"
)

TRACECAT__LOOP_MAX_BATCH_SIZE = int(
    os.environ.get("TRACECAT__LOOP_MAX_BATCH_SIZE") or 64
)
"""Maximum number of parallel requests to the worker service."""

TRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS = int(
    os.environ.get("TRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS", 16)
)
"""Maximum number of scheduler task coroutines allowed in-flight."""

TRACECAT__CHILD_WORKFLOW_MAX_IN_FLIGHT = int(
    os.environ.get("TRACECAT__CHILD_WORKFLOW_MAX_IN_FLIGHT", 8)
)
"""Hard cap on concurrent child workflows for looped subflow execution."""

TRACECAT__WORKFLOW_PERMIT_MAX_WAIT_SECONDS = int(
    os.environ.get("TRACECAT__WORKFLOW_PERMIT_MAX_WAIT_SECONDS", 300)
)
"""Maximum seconds to wait for a workflow concurrency permit before failing."""

TRACECAT__WORKFLOW_PERMIT_BACKOFF_BASE_SECONDS = float(
    os.environ.get("TRACECAT__WORKFLOW_PERMIT_BACKOFF_BASE_SECONDS", 1)
)
"""Base backoff in seconds when retrying workflow permit acquisition."""

TRACECAT__WORKFLOW_PERMIT_BACKOFF_MAX_SECONDS = float(
    os.environ.get("TRACECAT__WORKFLOW_PERMIT_BACKOFF_MAX_SECONDS", 30)
)
"""Maximum backoff in seconds when retrying workflow permit acquisition."""

TRACECAT__WORKFLOW_PERMIT_HEARTBEAT_SECONDS = float(
    os.environ.get("TRACECAT__WORKFLOW_PERMIT_HEARTBEAT_SECONDS", 60)
)
"""Interval in seconds between workflow permit heartbeat refreshes."""

TRACECAT__PERMIT_TTL_SECONDS = int(os.environ.get("TRACECAT__PERMIT_TTL_SECONDS", 300))
"""TTL in seconds for workflow/action concurrency permits before stale pruning."""

TRACECAT__ACTION_PERMIT_MAX_WAIT_SECONDS = int(
    os.environ.get("TRACECAT__ACTION_PERMIT_MAX_WAIT_SECONDS", 120)
)
"""Maximum seconds to wait for an action concurrency permit before failing."""

TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS = int(
    os.environ.get("TRACECAT__TIER_LIMITS_CACHE_TTL_SECONDS", 30)
)
"""TTL in seconds for cached per-organization effective tier limits."""

TRACECAT__EXECUTOR_QUEUE = os.environ.get(
    "TRACECAT__EXECUTOR_QUEUE", "shared-action-queue"
)
"""Task queue for the ExecutorWorker (Temporal activity queue)."""

TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR = os.environ.get(
    "TRACECAT__EXECUTOR_REGISTRY_CACHE_DIR", "/tmp/tracecat/registry-cache"
)
"""Directory for caching extracted registry tarballs in subprocess mode. Uses /tmp for ephemeral storage."""

# TODO: Set this as an environment variable
TRACECAT__SERVICE_ROLES_WHITELIST = [
    "tracecat-api",
    "tracecat-cli",
    "tracecat-llm-gateway",
    "tracecat-runner",
    "tracecat-schedule-runner",
    "tracecat-case-triggers",
    "tracecat-ui",
]
TRACECAT__DEFAULT_USER_ID = uuid.UUID(int=0)

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

TRACECAT__DB_MAX_OVERFLOW = int(os.environ.get("TRACECAT__DB_MAX_OVERFLOW") or 60)
"""The maximum number of connections to allow in the pool."""
TRACECAT__DB_POOL_SIZE = int(os.environ.get("TRACECAT__DB_POOL_SIZE") or 10)
"""The size of the connection pool."""
TRACECAT__DB_POOL_TIMEOUT = int(os.environ.get("TRACECAT__DB_POOL_TIMEOUT") or 30)
"""The timeout for the connection pool."""
TRACECAT__DB_POOL_RECYCLE = int(os.environ.get("TRACECAT__DB_POOL_RECYCLE") or 600)
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

# OIDC Login Flow
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "").strip().rstrip("/")
"""OIDC issuer URL (without trailing slash). If unset, legacy Google OAuth client is used."""

OIDC_CLIENT_ID = (
    os.environ.get("OIDC_CLIENT_ID")
    or os.environ.get("OAUTH_CLIENT_ID")
    or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    or ""
)
OIDC_CLIENT_SECRET = (
    os.environ.get("OIDC_CLIENT_SECRET")
    or os.environ.get("OAUTH_CLIENT_SECRET")
    or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    or ""
)
OIDC_SCOPES = tuple(
    scope
    for scope in (
        os.environ.get("OIDC_SCOPES", "openid profile email").replace(",", " ").split()
    )
    if scope
)

# Backward-compatible aliases for legacy config names.
OAUTH_CLIENT_ID = OIDC_CLIENT_ID
OAUTH_CLIENT_SECRET = OIDC_CLIENT_SECRET
USER_AUTH_SECRET = os.environ.get("USER_AUTH_SECRET", "")
TRACECAT__DB_ENCRYPTION_KEY = os.environ.get("TRACECAT__DB_ENCRYPTION_KEY")
TRACECAT__SIGNING_SECRET = os.environ.get("TRACECAT__SIGNING_SECRET")

# SAML SSO

SAML_PUBLIC_ACS_URL = f"{TRACECAT__PUBLIC_APP_URL}/auth/saml/acs"

SAML_IDP_METADATA_URL = os.environ.get("SAML_IDP_METADATA_URL")
"""Sets the default SAML metadata URL for cold start."""

SAML_ALLOW_UNSOLICITED = (
    os.environ.get("SAML_ALLOW_UNSOLICITED", "false").lower() == "true"
)
"""Whether to allow unsolicited SAML responses (default false)
Do not set to true if authn requests are signed are false
"""

SAML_AUTHN_REQUESTS_SIGNED = (
    os.environ.get("SAML_AUTHN_REQUESTS_SIGNED", "false").lower() == "true"
)
"""Whether to require signed SAML authentication requests. (default false)
Do not set to true if authn requests are signed are false
"""

SAML_SIGNED_ASSERTIONS = (
    os.environ.get("SAML_SIGNED_ASSERTIONS", "true").lower() == "true"
)
"""Whether to require signed SAML assertions."""

SAML_SIGNED_RESPONSES = (
    os.environ.get("SAML_SIGNED_RESPONSES", "true").lower() == "true"
)
"""Whether to require signed SAML responses."""

SAML_ACCEPTED_TIME_DIFF = int(os.environ.get("SAML_ACCEPTED_TIME_DIFF") or 3)
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
TEMPORAL__CONNECT_RETRIES = int(os.environ.get("TEMPORAL__CONNECT_RETRIES") or 10)
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
TEMPORAL__CLIENT_RPC_TIMEOUT = os.environ.get("TEMPORAL__CLIENT_RPC_TIMEOUT") or "900"
"""RPC timeout for Temporal workflows in seconds (default 900 seconds)."""

TEMPORAL__TASK_TIMEOUT = os.environ.get("TEMPORAL__TASK_TIMEOUT") or "900"
"""Temporal workflow task timeout in seconds (default 900 seconds)."""

TEMPORAL__METRICS_PORT = os.environ.get("TEMPORAL__METRICS_PORT")
"""Port for the Temporal metrics server."""


TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION = os.environ.get(
    "TEMPORAL__DISABLE_EAGER_ACTIVITY_EXECUTION", "true"
).lower() in ("true", "1")
"""Disable eager activity execution for Temporal workflows."""

# === Sentry config === #
SENTRY_ENVIRONMENT_OVERRIDE = os.environ.get("SENTRY_ENVIRONMENT_OVERRIDE")
"""Override the Sentry environment. If not set, defaults to '{app_env}-{temporal_namespace}'."""

# === Secrets manager config === #
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
TRACECAT__EXECUTOR_TOKEN_TTL_SECONDS = int(
    os.environ.get("TRACECAT__EXECUTOR_TOKEN_TTL_SECONDS") or 900
)
"""Executor JWT TTL in seconds (default: 900 seconds)."""

# === Remote registry === #
TRACECAT__ALLOWED_GIT_DOMAINS = set(
    os.environ.get(
        "TRACECAT__ALLOWED_GIT_DOMAINS", "github.com,gitlab.com,bitbucket.org"
    ).split(",")
)

# === Blob Storage Config === #

TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS = os.environ.get(
    "TRACECAT__BLOB_STORAGE_BUCKET_ATTACHMENTS", "tracecat-attachments"
)
"""Bucket for case attachments."""

TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY = os.environ.get(
    "TRACECAT__BLOB_STORAGE_BUCKET_REGISTRY", "tracecat-registry"
)
"""Bucket for registry tarball files and versioned artifacts."""

TRACECAT__BLOB_STORAGE_ENDPOINT = os.environ.get("TRACECAT__BLOB_STORAGE_ENDPOINT", "")
"""Endpoint URL for blob storage."""

TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT = os.environ.get(
    "TRACECAT__BLOB_STORAGE_PRESIGNED_URL_ENDPOINT", None
)
"""Public endpoint URL to use for presigned URLs."""

TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY = int(
    os.environ.get("TRACECAT__BLOB_STORAGE_PRESIGNED_URL_EXPIRY") or 10
)
"""Default expiry time for presigned URLs in seconds (default: 10 seconds for immediate use)."""

TRACECAT__DISABLE_PRESIGNED_URL_IP_CHECKING = (
    os.environ.get("TRACECAT__DISABLE_PRESIGNED_URL_IP_CHECKING", "true").lower()
    == "true"
)
"""Disable client IP checking for presigned URLs. Set to false for production with public S3, true for local MinIO (default: true)."""

# Bucket for workflow data (externalized results, triggers, etc.)
TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW = os.environ.get(
    "TRACECAT__BLOB_STORAGE_BUCKET_WORKFLOW", "tracecat-workflow"
)
"""Bucket for externalized workflow data (action results, triggers, etc.)."""

TRACECAT__WORKFLOW_ARTIFACT_RETENTION_DAYS = int(
    os.environ.get("TRACECAT__WORKFLOW_ARTIFACT_RETENTION_DAYS") or 30
)
"""Retention period in days for workflow artifacts in blob storage.

Objects older than this will be automatically deleted via S3 lifecycle rules.
Set to 0 to disable automatic expiration.
Default: 30 days (matches Temporal Cloud workflow history retention).
"""

# === Result Externalization Config === #
TRACECAT__RESULT_EXTERNALIZATION_ENABLED = os.environ.get(
    "TRACECAT__RESULT_EXTERNALIZATION_ENABLED", "true"
).lower() in ("true", "1")
"""Enable externalization of large action results and triggers to S3/MinIO.

When enabled, payloads exceeding the threshold are stored in blob storage with
only a small reference kept in Temporal workflow history. This prevents history
bloat for workflows with large payloads.

Default: true.
"""

TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES = int(
    os.environ.get("TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES") or 128 * 1024
)
"""Threshold in bytes above which payloads are externalized to blob storage.

Payloads smaller than this are kept inline in workflow history.
Default: 128 KB.
"""

# === Collection Manifests Config === #
TRACECAT__COLLECTION_MANIFESTS_ENABLED = os.environ.get(
    "TRACECAT__COLLECTION_MANIFESTS_ENABLED", "false"
).lower() in ("true", "1")
"""Feature gate for CollectionObject emission.

When enabled, large collections (above thresholds) are stored as chunked manifests
in blob storage, with only a small handle kept in Temporal workflow history.
When disabled (default), large collections use legacy InlineObject/ExternalObject.
"""

TRACECAT__COLLECTION_CHUNK_SIZE = int(
    os.environ.get("TRACECAT__COLLECTION_CHUNK_SIZE") or 256
)
"""Number of items per chunk in collection manifests. Default: 256."""

TRACECAT__COLLECTION_INLINE_MAX_ITEMS = int(
    os.environ.get("TRACECAT__COLLECTION_INLINE_MAX_ITEMS") or 100
)
"""Maximum items before using CollectionObject. Below this, use InlineObject/ExternalObject."""

TRACECAT__COLLECTION_INLINE_MAX_BYTES = int(
    os.environ.get("TRACECAT__COLLECTION_INLINE_MAX_BYTES") or 256 * 1024
)
"""Maximum bytes before using CollectionObject. Default: 256 KB."""

# === Local registry === #
TRACECAT__LOCAL_REPOSITORY_ENABLED = os.getenv(
    "TRACECAT__LOCAL_REPOSITORY_ENABLED", "0"
).lower() in ("1", "true")
TRACECAT__LOCAL_REPOSITORY_PATH = os.getenv("TRACECAT__LOCAL_REPOSITORY_PATH")
TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH = "/app/local_registry"

# === Python Script Execution (nsjail Sandbox) === #
TRACECAT__SANDBOX_NSJAIL_PATH = os.environ.get(
    "TRACECAT__SANDBOX_NSJAIL_PATH", "/usr/local/bin/nsjail"
)
"""Path to the nsjail binary for sandbox execution."""

TRACECAT__SANDBOX_ROOTFS_PATH = os.environ.get(
    "TRACECAT__SANDBOX_ROOTFS_PATH", "/var/lib/tracecat/sandbox-rootfs"
)
"""Path to the sandbox rootfs directory containing Python 3.12 + uv.

Used by both action sandbox and agent sandbox. Runtime code is copied
to job directory at spawn time, site-packages mounted read-only.
"""

TRACECAT__SANDBOX_CACHE_DIR = os.environ.get(
    "TRACECAT__SANDBOX_CACHE_DIR", "/var/lib/tracecat/sandbox-cache"
)
"""Base directory for sandbox caching (packages, uv cache)."""

TRACECAT__SANDBOX_DEFAULT_TIMEOUT = int(
    os.environ.get("TRACECAT__SANDBOX_DEFAULT_TIMEOUT") or 300
)
"""Default timeout for sandbox script execution in seconds."""

TRACECAT__SANDBOX_DEFAULT_MEMORY_MB = int(
    os.environ.get("TRACECAT__SANDBOX_DEFAULT_MEMORY_MB") or 2048
)
"""Default memory limit for sandbox execution in megabytes (2 GiB)."""

TRACECAT__SANDBOX_PYPI_INDEX_URL = os.environ.get(
    "TRACECAT__SANDBOX_PYPI_INDEX_URL", "https://pypi.org/simple"
)
"""Primary PyPI index URL for package installation. Supports private mirrors and air-gapped deployments."""

TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS = [
    url.strip()
    for url in os.environ.get("TRACECAT__SANDBOX_PYPI_EXTRA_INDEX_URLS", "").split(",")
    if url.strip()
]
"""Additional PyPI index URLs (comma-separated). Used as fallback sources for package installation."""

TRACECAT__DISABLE_NSJAIL = os.environ.get(
    "TRACECAT__DISABLE_NSJAIL", "true"
).lower() in ("true", "1")
"""Disable nsjail sandbox and use the unsafe PID executor instead.

When True (default), uses UnsafePidExecutor with best-effort PID namespace
isolation. This mode works without privileged Docker mode but has less isolation.

When False, uses nsjail sandbox for full OS-level isolation. Requires:
- Linux with kernel >= 4.6
- Docker privileged mode or CAP_SYS_ADMIN capability
- nsjail binary at TRACECAT__SANDBOX_NSJAIL_PATH
- Sandbox rootfs at TRACECAT__SANDBOX_ROOTFS_PATH
"""

# === Action Executor === #
TRACECAT__EXECUTOR_BACKEND = os.environ.get("TRACECAT__EXECUTOR_BACKEND", "direct")
"""Executor backend for running actions.

Supported values:
- 'pool': Warm nsjail workers (single-tenant, high throughput, ~100-200ms)
- 'ephemeral': Cold nsjail subprocess per action (multitenant, full isolation, ~4000ms)
- 'direct': Direct subprocess execution (no warm workers, no in-process state sharing)
- 'test': In-process execution for tests only (no isolation, no subprocess overhead)
- 'auto': Auto-select based on environment (pool if nsjail available, else direct)

Trust mode is derived from the backend type:
- pool: untrusted (secrets pre-resolved, no DB creds)
- ephemeral: untrusted (secrets pre-resolved, no DB creds)
- direct: untrusted subprocess execution (secrets pre-resolved, no DB creds)
- test: trusted in-process execution (no sandbox)

WARNING: 'test' backend provides NO isolation between actions. Actions share
the same process memory, env vars can leak, and crashes affect the whole worker.
Only use 'test' for tests.
"""

TRACECAT__EXECUTOR_CLIENT_TIMEOUT = float(
    os.environ.get("TRACECAT__EXECUTOR_CLIENT_TIMEOUT") or 300
)
"""Default timeout in seconds for executor client operations (default: 300s)."""

# === Action Executor Sandbox === #
TRACECAT__EXECUTOR_SANDBOX_ENABLED = os.environ.get(
    "TRACECAT__EXECUTOR_SANDBOX_ENABLED", "false"
).lower() in ("true", "1")
"""Enable nsjail sandbox for action execution in subprocess mode.

When True, actions run in an nsjail sandbox with:
- Filesystem isolation (tmpdir VFS)
- Resource limits (CPU, memory, file size, processes)
- Network access (for DB, S3, external APIs)

When False (default), actions run in direct subprocesses without sandboxing.

Requires:
- TRACECAT__EXECUTOR_BACKEND=pool, ephemeral, or direct
- nsjail binary at TRACECAT__SANDBOX_NSJAIL_PATH
- Sandbox rootfs at TRACECAT__SANDBOX_ROOTFS_PATH
"""

TRACECAT__EXECUTOR_TRACECAT_APP_DIR = os.environ.get(
    "TRACECAT__EXECUTOR_TRACECAT_APP_DIR", ""
)
"""Path to the tracecat package directory for sandbox mounting.
If not set, will be auto-detected from the installed tracecat package location.
"""

TRACECAT__EXECUTOR_SITE_PACKAGES_DIR = os.environ.get(
    "TRACECAT__EXECUTOR_SITE_PACKAGES_DIR", ""
)
"""Path to the Python site-packages directory containing tracecat dependencies.
If not set, will be auto-detected from a known dependency's location.
"""

TRACECAT__EXECUTOR_POOL_METRICS_ENABLED = os.environ.get(
    "TRACECAT__EXECUTOR_POOL_METRICS_ENABLED", "false"
).lower() in ("true", "1")
"""Enable periodic metrics emission for the worker pool.

When True, the pool emits metrics every 10 seconds including:
- Pool utilization and capacity
- Worker states (alive, dead, recycling)
- Lock contention stats
- Throughput metrics

When False (default), metrics are not emitted to reduce log noise.
"""

# === Agent Sandbox (NSJail for ClaudeAgentRuntime) === #
TRACECAT__AGENT_SANDBOX_TIMEOUT = int(
    os.environ.get("TRACECAT__AGENT_SANDBOX_TIMEOUT") or 600
)
"""Default timeout for agent sandbox execution in seconds (10 minutes)."""

TRACECAT__AGENT_SANDBOX_MEMORY_MB = int(
    os.environ.get("TRACECAT__AGENT_SANDBOX_MEMORY_MB") or 4096
)
"""Default memory limit for agent sandbox execution in megabytes (4 GiB)."""

TRACECAT__AGENT_QUEUE = os.environ.get("TRACECAT__AGENT_QUEUE", "shared-agent-queue")
"""Task queue for the AgentWorker (Temporal workflow queue).

This is the dedicated queue for agent workflow execution, separate from the main
tracecat-task-queue used by DSLWorkflow."""

# === Rate Limiting === #
TRACECAT__RATE_LIMIT_ENABLED = (
    os.environ.get("TRACECAT__RATE_LIMIT_ENABLED", "true").lower() == "true"
)
"""Whether rate limiting is enabled for the executor service."""

TRACECAT__RATE_LIMIT_RATE = float(os.environ.get("TRACECAT__RATE_LIMIT_RATE") or 40.0)
"""The rate at which tokens are added to the bucket (tokens per second)."""

TRACECAT__RATE_LIMIT_CAPACITY = float(
    os.environ.get("TRACECAT__RATE_LIMIT_CAPACITY") or 80.0
)
"""The maximum number of tokens the bucket can hold."""

TRACECAT__RATE_LIMIT_WINDOW_SIZE = int(
    os.environ.get("TRACECAT__RATE_LIMIT_WINDOW_SIZE") or 60
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
    os.environ.get("TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES") or 1024 * 1024
)
"""The maximum size of a payload in bytes the executor can return. Defaults to 1MB"""

TRACECAT__MAX_FILE_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_FILE_SIZE_BYTES") or 20 * 1024 * 1024  # Default 20MB
)
"""The maximum size for file handling (e.g., uploads, downloads) in bytes. Defaults to 20MB."""

TRACECAT__MAX_TABLE_IMPORT_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_TABLE_IMPORT_SIZE_BYTES") or 5 * 1024 * 1024
)
"""Maximum CSV upload size for table imports in bytes. Defaults to 5MB."""

TRACECAT__MAX_UPLOAD_FILES_COUNT = int(
    os.environ.get("TRACECAT__MAX_UPLOAD_FILES_COUNT") or 5
)
"""The maximum number of files that can be uploaded at once. Defaults to 5."""

TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES") or 100 * 1024 * 1024
)
"""The maximum size of the aggregate upload size in bytes. Defaults to 100MB."""

# === System PATH config === #
TRACECAT__SYSTEM_PATH = os.environ.get(
    "TRACECAT__SYSTEM_PATH", "/usr/local/bin:/usr/bin:/bin"
)
"""System PATH for subprocess execution. Includes common binary locations."""

# === Concurrency Limits === #
TRACECAT__S3_CONCURRENCY_LIMIT = int(
    os.environ.get("TRACECAT__S3_CONCURRENCY_LIMIT") or 50
)
"""Maximum number of concurrent S3 operations to prevent resource exhaustion. Defaults to 50."""

# === API List/Search Limits === #
TRACECAT__LIMIT_MIN = 1
"""Minimum list/search page size."""

TRACECAT__LIMIT_DEFAULT = 20
"""Default list/search page size."""

TRACECAT__LIMIT_CURSOR_MAX = 200
"""Maximum page size for cursor-pagination and case list/search endpoints."""

TRACECAT__LIMIT_WORKFLOW_LIST_MIN = 0
"""Minimum workflow list limit (0 means unpaginated/all)."""

TRACECAT__LIMIT_AGENT_SESSIONS_DEFAULT = 50
"""Default page size for agent session listing."""

TRACECAT__LIMIT_REGISTRY_VERSIONS_DEFAULT = 50
"""Default page size for registry version listing."""

TRACECAT__LIMIT_COMMITS_DEFAULT = 10
"""Default page size for commit listing endpoints."""

TRACECAT__LIMIT_WORKFLOW_EXECUTIONS_DEFAULT = 100
"""Default page size for workflow execution listing."""

TRACECAT__LIMIT_WORKFLOW_EXECUTIONS_MAX = 1000
"""Maximum page size for workflow execution listing."""

TRACECAT__LIMIT_TABLE_SEARCH_DEFAULT = min(100, TRACECAT__LIMIT_CURSOR_MAX)
"""Default page size for internal table search."""

TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX = 1000
"""Maximum row count for internal table downloads."""

TRACECAT__LIMIT_TABLE_DOWNLOAD_DEFAULT = TRACECAT__LIMIT_TABLE_DOWNLOAD_MAX
"""Default row count for internal table download."""

# === Context Compression === #
TRACECAT__CONTEXT_COMPRESSION_ENABLED = os.environ.get(
    "TRACECAT__CONTEXT_COMPRESSION_ENABLED", "false"
).lower() in ("true", "1")
"""Enable compression of large action results in workflow contexts. Defaults to False."""

TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB = int(
    os.environ.get("TRACECAT__CONTEXT_COMPRESSION_THRESHOLD_KB") or 16
)
"""Threshold in KB above which action results are compressed. Defaults to 16KB."""

TRACECAT__CONTEXT_COMPRESSION_ALGORITHM = os.environ.get(
    "TRACECAT__CONTEXT_COMPRESSION_ALGORITHM", "zstd"
)
"""Compression algorithm to use. Supported: zstd, gzip, brotli. Defaults to zstd."""

TRACECAT__WORKFLOW_RETURN_STRATEGY: Literal["context", "minimal"] = cast(
    Literal["context", "minimal"],
    os.environ.get("TRACECAT__WORKFLOW_RETURN_STRATEGY", "minimal").lower(),
)
"""Strategy to use when returning a value from a workflow. Supported: context, minimal. Defaults to minimal."""

# === Redis config === #
REDIS_CHAT_TTL_SECONDS = int(
    os.environ.get("REDIS_CHAT_TTL_SECONDS") or 3 * 24 * 60 * 60  # 3 days
)
"""TTL for Redis chat history streams in seconds. Defaults to 3 days."""

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
"""URL for the Redis instance. Required for Redis chat history."""

REDIS_URL__ARN = os.environ.get("REDIS_URL__ARN")
"""(AWS only) ARN of the secret containing the Redis URL."""

TRACECAT__CASE_TRIGGERS_ENABLED = (
    os.environ.get("TRACECAT__CASE_TRIGGERS_ENABLED", "true").lower() == "true"
)
"""Enable case event workflow triggers. Defaults to true."""

TRACECAT__CASE_TRIGGERS_STREAM_KEY = os.environ.get(
    "TRACECAT__CASE_TRIGGERS_STREAM_KEY", "case-events"
)
"""Redis stream key for case events."""

TRACECAT__CASE_TRIGGERS_GROUP = os.environ.get(
    "TRACECAT__CASE_TRIGGERS_GROUP", "case-triggers"
)
"""Redis consumer group for case trigger processing."""

TRACECAT__CASE_TRIGGERS_BLOCK_MS = int(
    os.environ.get("TRACECAT__CASE_TRIGGERS_BLOCK_MS") or 2000
)
"""XREADGROUP block timeout in milliseconds."""

TRACECAT__CASE_TRIGGERS_BATCH = int(
    os.environ.get("TRACECAT__CASE_TRIGGERS_BATCH") or 100
)
"""Maximum number of events to read per batch."""

TRACECAT__CASE_TRIGGERS_CLAIM_IDLE_MS = int(
    os.environ.get("TRACECAT__CASE_TRIGGERS_CLAIM_IDLE_MS") or 300000
)
"""Idle time before claiming pending messages (milliseconds)."""

TRACECAT__CASE_TRIGGERS_MAXLEN = int(
    os.environ.get("TRACECAT__CASE_TRIGGERS_MAXLEN") or 30000
)
"""Approximate max length for the case events stream."""

TRACECAT__CASE_TRIGGERS_DEDUP_TTL_SECONDS = int(
    os.environ.get("TRACECAT__CASE_TRIGGERS_DEDUP_TTL_SECONDS") or 21600
)
"""TTL for case trigger dedup keys in seconds."""

TRACECAT__CASE_TRIGGERS_LOCK_TTL_SECONDS = int(
    os.environ.get("TRACECAT__CASE_TRIGGERS_LOCK_TTL_SECONDS") or 300
)
"""TTL for case trigger lock keys in seconds."""

# === File limits === #
TRACECAT__MAX_ATTACHMENT_SIZE_BYTES = int(
    os.environ.get("TRACECAT__MAX_ATTACHMENT_SIZE_BYTES") or 20 * 1024 * 1024
)
"""The maximum size for case attachment files in bytes. Defaults to 20MB."""

TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH = int(
    os.environ.get("TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH") or 255
)
"""The maximum length for attachment filenames. Defaults to 255 (Django FileField standard)."""

TRACECAT__MAX_CASE_STORAGE_BYTES = int(
    os.environ.get("TRACECAT__MAX_CASE_STORAGE_BYTES") or 200 * 1024 * 1024
)
"""The maximum total storage per case in bytes. Defaults to 200MB."""

TRACECAT__MAX_ATTACHMENTS_PER_CASE = int(
    os.environ.get("TRACECAT__MAX_ATTACHMENTS_PER_CASE") or 10
)
"""The maximum number of attachments allowed per case. Defaults to 10."""

TRACECAT__MAX_RECORDS_PER_CASE = int(
    os.environ.get("TRACECAT__MAX_RECORDS_PER_CASE") or 50
)
"""The maximum number of entity records allowed per case. Defaults to 50."""

# === File security === #

ALLOWED_ATTACHMENT_EXTENSIONS = ",".join(
    [
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".txt",
        ".csv",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
    ]
)
TRACECAT__ALLOWED_ATTACHMENT_EXTENSIONS = {
    ext.strip()
    for ext in os.environ.get(
        "TRACECAT__ALLOWED_ATTACHMENT_EXTENSIONS", ALLOWED_ATTACHMENT_EXTENSIONS
    ).split(",")
    if ext.strip()
}
"""The allowed extensions for case attachment files."""

ALLOWED_ATTACHMENT_MIME_TYPES = ",".join(
    [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/csv",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    ]
)
TRACECAT__ALLOWED_ATTACHMENT_MIME_TYPES = {
    mime_type.strip()
    for mime_type in os.environ.get(
        "TRACECAT__ALLOWED_ATTACHMENT_MIME_TYPES", ALLOWED_ATTACHMENT_MIME_TYPES
    ).split(",")
    if mime_type.strip()
}
"""The allowed MIME types for case attachment files."""

# === Enterprise Edition === #
ENTERPRISE_EDITION = os.environ.get("ENTERPRISE_EDITION", "false").lower() in (
    "true",
    "1",
)
"""Whether the enterprise edition is enabled."""

TRACECAT__EE_MULTI_TENANT = os.environ.get(
    "TRACECAT__EE_MULTI_TENANT", "false"
).lower() in ("true", "1")
"""Whether multi-tenant features are enabled for Enterprise Edition."""

# === Feature Flags === #
TRACECAT__FEATURE_FLAGS: set[FeatureFlag] = set()
for _flag in os.environ.get("TRACECAT__FEATURE_FLAGS", "").split(","):
    if not (_flag_value := _flag.strip()):
        continue
    try:
        TRACECAT__FEATURE_FLAGS.add(FeatureFlag(_flag_value))
    except ValueError:
        logger.warning(
            "Ignoring unknown feature flag '%s' from TRACECAT__FEATURE_FLAGS",
            _flag_value,
        )
"""Set of enabled feature flags."""


# === Agent config === #
TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED = os.environ.get(
    "TRACECAT__UNIFIED_AGENT_STREAMING_ENABLED", "false"
).lower() in ("true", "1")
"""Whether to enable unified streaming for agent execution."""

TRACECAT__AGENT_MAX_TOOLS = int(os.environ.get("TRACECAT__AGENT_MAX_TOOLS") or 30)
"""The maximum number of tools that can be used in an agent."""

TRACECAT__AGENT_MAX_TOOL_CALLS = int(
    os.environ.get("TRACECAT__AGENT_MAX_TOOL_CALLS") or 40
)
"""The maximum number of tool calls that can be made per agent run."""

TRACECAT__AGENT_MAX_REQUESTS = int(
    os.environ.get("TRACECAT__AGENT_MAX_REQUESTS") or 120
)
"""The maximum number of requests that can be made per agent run."""

TRACECAT__AGENT_MAX_RETRIES = int(os.environ.get("TRACECAT__AGENT_MAX_RETRIES") or 20)
"""The maximum number of retries that can be made per agent run."""

TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT = int(
    os.environ.get("TRACECAT__AGENT_DEFAULT_CONTEXT_LIMIT") or 128_000
)
"""Default character limit for agent message history when truncating context."""

TRACECAT__AGENT_TOOL_OUTPUT_LIMIT = int(
    os.environ.get("TRACECAT__AGENT_TOOL_OUTPUT_LIMIT") or 80_000
)
"""Default character limit for individual tool outputs when truncating context."""

TRACECAT__MODEL_CONTEXT_LIMITS = {
    "gpt-4o-mini": 100_000,
    "gpt-5-mini": 350_000,
    "gpt-5-nano": 350_000,
    "gpt-5": 350_000,
    "claude-sonnet-4-5-20250929": 180_000,
    "claude-haiku-4-5-20251001": 180_000,
    "claude-opus-4-5-20251101": 180_000,
    "anthropic.claude-sonnet-4-5-20250929-v1:0": 180_000,
    "anthropic.claude-haiku-4-5-20251001-v1:0": 180_000,
}
"""Model-specific character limits for agent message history truncation."""

# === Registry Sync === #
TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED = os.environ.get(
    "TRACECAT__REGISTRY_SYNC_SANDBOX_ENABLED", "true"
).lower() in ("true", "1")
"""Enable sandboxed registry sync via Temporal workflow on ExecutorWorker.

When True (default), registry sync operations run on the ExecutorWorker with:
- Git clone in subprocess with SSH credentials
- Package installation with network access
- Action discovery (currently subprocess, future: nsjail without network)
- Tarball build and upload to S3

When False, uses the existing subprocess approach from the API service.
"""

TRACECAT__REGISTRY_SYNC_INSTALL_TIMEOUT = int(
    os.environ.get("TRACECAT__REGISTRY_SYNC_INSTALL_TIMEOUT") or 600
)
"""Timeout for package installation during registry sync in seconds. Defaults to 600 (10 min)."""

TRACECAT__REGISTRY_SYNC_DISCOVER_TIMEOUT = int(
    os.environ.get("TRACECAT__REGISTRY_SYNC_DISCOVER_TIMEOUT") or 300
)
"""Timeout for action discovery during registry sync in seconds. Defaults to 300 (5 min)."""

TRACECAT__REGISTRY_SYNC_BUILTIN_USE_INSTALLED_SITE_PACKAGES = os.environ.get(
    "TRACECAT__REGISTRY_SYNC_BUILTIN_USE_INSTALLED_SITE_PACKAGES", "true"
).lower() in ("true", "1")
"""Use installed site-packages for builtin registry tarball builds.

When True (default), builtin tracecat_registry sync packages the current
interpreter's installed site-packages into a tarball. This avoids creating
a fresh venv and re-installing dependencies from package indexes at runtime.
"""

TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH = os.environ.get(
    "TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH", "/app/packages/tracecat-registry"
)
"""Path to the builtin tracecat_registry package source.

In Docker, packages are copied to /app/packages/tracecat-registry.
In development with editable install, falls back to checking relative to the installed package.
"""
