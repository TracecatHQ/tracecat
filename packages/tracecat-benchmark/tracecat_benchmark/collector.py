"""Metric collector for the current workflow load-test types.

Samples PostgreSQL activity at >=1Hz and captures the surrounding evidence a
run needs to be interpretable later: the resolved Compose model, effective
container limits and OOM/restart state, effective PostgreSQL settings, service
log diagnostic summaries, fixture row correctness and physical-table drift, and
the Tracecat commit plus container image identifiers.

Normally coordinated by ``just cluster loadtest``. For harness development and
diagnosis, run it alongside the load runner with the same artifact directory:

    uv run --all-packages python -m tracecat_benchmark.collector \\
        --run-id scatter-abc123 \\
        --workspace-id 00000000-0000-4000-8000-000000000000 \\
        --cluster-num 2 \\
        --public-api-url http://localhost:180/api \\
        --ee-multi-tenant true \\
        --compose-file docker-compose.dev.yml \\
        --compose-file docker-compose.sandbox.yml \\
        --compose-file packages/tracecat-benchmark/docker-compose.loadtest.yml \\
        --temporal-target localhost:7333 \\
        --temporal-namespace default \\
        --temporal-workflow-task-queue tracecat-task-queue \\
        --temporal-activity-task-queue tracecat-task-queue \\
        --temporal-activity-task-queue shared-action-queue \\
        --dsn-env TRACECAT_LOADTEST_MONITOR_DSN

The collector stops automatically after the runner completes plus the requested
recovery window. SIGINT remains available for a runner that exits before it can
publish its completion marker.

See scripts/benchmark/postgres-scatter-load-test-plan.md.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, TextIO, TypedDict, cast
from urllib.parse import urlsplit

import asyncpg
import httpx
import yaml
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client as TemporalClient
from temporalio.service import RPCError

from tracecat import config as tracecat_config
from tracecat.dsl._converter import get_data_converter
from tracecat.identifiers.workflow import WorkspaceUUID

from .activity_metrics import (
    ActivityMetricsCaptureError,
    TemporalSdkMetricsCapture,
    collect_activity_history_metrics,
    load_activity_metrics_handoff,
)
from .models import (
    COLLECTOR_MEASUREMENT_COMPLETE_FILENAME,
    COLLECTOR_MEASUREMENT_READY_FILENAME,
    MAX_LOADTEST_EXECUTOR_REPLICAS,
    RUNNER_COMPLETE_FILENAME,
    RUNNER_MEASUREMENT_COMPLETE_FILENAME,
    RUNNER_MEASUREMENT_READY_FILENAME,
    CollectorConfig,
    CollectorManifest,
    CollectorReady,
    ContainerResourceUsage,
    ContainerState,
    DbPoolMetricsEndpoint,
    HostResourceUsage,
    MeasurementBoundary,
    PgActivitySample,
    ResourceUsageSample,
    RowCorrectness,
    RunnerComplete,
    SdkMetricsEndpoint,
    ServiceLogSignalCounts,
    ServiceLogSummary,
    TableDrift,
    TemporalBacklogSample,
    TemporalTaskQueueStats,
    compose_project_fingerprint,
    deployment_value_fingerprint,
    run_id_fingerprint,
    shareable_artifact_path,
    workspace_fingerprint,
)
from .repository import resolve_repository_root

DEFAULT_ARTIFACT_ROOT: Final = "/tmp/tracecat-load-test"
DEFAULT_DSN_ENV: Final = "TRACECAT_LOADTEST_MONITOR_DSN"
DEFAULT_LOG_SERVICES: Final = ("api", "worker", "executor", "postgres_db")
# Long-running services used by the current table-backed load types. Migrations
# is an expected successful one-shot; future types may extend this set.
REQUIRED_LOAD_TEST_SERVICES: Final = frozenset(
    {
        "api",
        "caddy",
        "executor",
        "minio",
        "postgres_db",
        "redis",
        "temporal",
        "temporal_postgres_db",
        "worker",
    }
)
DEFAULT_TABLE_NAME: Final = "scatter_load_rows"
IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COMPOSE_SERVICE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
CASE_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SIZE_RE: Final = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kKMGTPE]?i?B)\s*$")
DEFAULT_RECOVERY_SECONDS: Final = 60.0
MIN_RECOVERY_SECONDS: Final = 5.0
DEFAULT_READINESS_TIMEOUT_SECONDS: Final = 120.0
COMPOSE_PROFILE_FILES: Final = {
    "docker-compose.dev.yml": "dev",
    "docker-compose.local.yml": "local",
    "docker-compose.yml": "prod",
}
SANDBOX_COMPOSE_FILE: Final = "docker-compose.sandbox.yml"
REDACTED_ENV_VALUE: Final = "[REDACTED]"
REDACTED_PATH_VALUE: Final = "[REDACTED_PATH]"
RUN_CLAIM_FILENAME: Final = "collector_claim.json"
_TRACECAT_APPLICATION_NAMES: Final = frozenset(
    {
        "api",
        "worker",
        "executor",
        "agent-worker",
        "litellm",
        "agent-executor",
        "mcp",
    }
)
KNOWN_APPLICATION_NAMES: Final = frozenset(
    {
        "unknown",
        "load-test-collector",
        "load-test-monitor-provisioner",
        "load-test-probe",
        *_TRACECAT_APPLICATION_NAMES,
        *(f"{name}-auth" for name in _TRACECAT_APPLICATION_NAMES),
    }
)
# A real ``docker stats --no-stream`` snapshot routinely takes just over two
# seconds on Docker Desktop. Keep resource sampling independent from the
# requested PostgreSQL/Temporal cadence and retain enough headroom to
# distinguish that expected capture time from a stalled sampler.
RESOURCE_SAMPLE_INTERVAL_SECONDS: Final = 5.0
SAMPLER_OPERATION_TIMEOUT_SECONDS: Final = 5.0
LOCAL_CLUSTER_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})
TEMPORAL_WORKFLOW_TASK_QUEUE_TYPE: Final[TaskQueueType.ValueType] = (
    TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW
)
TEMPORAL_ACTIVITY_TASK_QUEUE_TYPE: Final[TaskQueueType.ValueType] = (
    TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY
)

# These patterns only aggregate observational signals. They never select
# control flow, status codes, or retry behavior, and no matching text is
# retained in an artifact.
POSTGRES_CONNECTION_LIMIT_RE: Final = re.compile(
    r"remaining connection slots are reserved|too many clients already",
    re.IGNORECASE,
)
DATABASE_POOL_TIMEOUT_RE: Final = re.compile(
    r"queuepool limit|connection pool.{0,80}(?:timeout|timed out)|"
    r"pool timeout",
    re.IGNORECASE,
)
STATEMENT_TIMEOUT_RE: Final = re.compile(
    r"canceling statement due to statement timeout",
    re.IGNORECASE,
)
LOCK_TIMEOUT_RE: Final = re.compile(
    r"canceling statement due to lock timeout",
    re.IGNORECASE,
)
DEADLOCK_RE: Final = re.compile(r"deadlock detected", re.IGNORECASE)
SERIALIZATION_FAILURE_RE: Final = re.compile(
    r"could not serialize access", re.IGNORECASE
)
CONNECTION_REFUSED_RE: Final = re.compile(r"connection refused", re.IGNORECASE)
CONNECTION_RESET_RE: Final = re.compile(
    r"connectionreseterror|connection reset", re.IGNORECASE
)
TIMEOUT_RE: Final = re.compile(r"timeouterror|timed out", re.IGNORECASE)
HTTP_5XX_RE: Final = re.compile(
    r'HTTP/\d(?:\.\d)?[" ]+5\d\d\b|'
    r"""["']?status(?:_code)?["']?\s*[:=]\s*5\d\d\b""",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ClusterPorts:
    """Host endpoints resolved by the authoritative cluster wrapper."""

    public_api_url: str
    postgres_host: str
    postgres_port: int
    temporal_host: str
    temporal_port: int
    temporal_worker_metrics_url: str
    temporal_executor_metrics_url: str
    api_db_pool_metrics_url: str
    worker_db_pool_metrics_url: str
    executor_db_pool_metrics_url: str
    pgdog_metrics_url: str


@dataclass(frozen=True, slots=True)
class ComposePublicUrls:
    """Public URLs read from the containers' deployed Compose environment."""

    app: str
    api: str


class DbPoolLatencyMetrics(TypedDict):
    """Cumulative latency distribution returned by Tracecat services."""

    count: int
    sum_seconds: float
    max_seconds: float
    bucket_upper_bounds_seconds: list[float]
    cumulative_bucket_counts: list[int]


class DbPoolSnapshot(TypedDict):
    """One SQLAlchemy pool snapshot returned by a service process."""

    pool: str
    configured_size: int
    configured_max_overflow: int
    configured_timeout_seconds: float
    checkouts_total: int
    connections_created_total: int
    checkout_timeouts_total: int
    returns_total: int
    checked_in: int
    checked_out: int
    overflow: int
    checked_out_high_water: int
    overflow_high_water: int
    checkout_wait: DbPoolLatencyMetrics
    connection_open: DbPoolLatencyMetrics
    connection_hold: DbPoolLatencyMetrics


class DbPoolMetricsPayload(TypedDict):
    """Versioned process-local pool metrics document."""

    schema_version: int
    sampled_at_unix_seconds: float
    pools: list[DbPoolSnapshot]


class DbPoolEndpointSample(TypedDict):
    """Pool metrics attributed to one service replica."""

    service: Literal["api", "worker", "executor"]
    replica_index: int
    metrics: DbPoolMetricsPayload


class DbPoolMetricsSample(TypedDict):
    """One collector-aligned sample from every service replica."""

    sampled_at: str
    endpoints: list[DbPoolEndpointSample]


class PgDogMetricsSample(TypedDict):
    """One raw PgDog OpenMetrics exposition sampled during a run."""

    sampled_at: str
    openmetrics: str


class CadenceSamplingGap(TypedDict):
    """Structured non-fatal record for one delayed periodic observation."""

    sampled_at: str
    sampling_gap: Literal["cadence_delayed"]
    signal: str
    elapsed_seconds: float
    target_interval_seconds: float


class SamplerErrorGap(TypedDict):
    """Structured non-fatal record for a sampler error that later recovers."""

    sampled_at: str
    sampling_gap: Literal["sampler_error"]
    signal: str
    error_type: str


SENSITIVE_ENV_SUFFIXES: Final = (
    "ACCOUNT",
    "ACCOUNT_ID",
    "ACCESS_KEY_ID",
    "ADDRESS",
    "API_KEY",
    "ARN",
    "CLIENT_ID",
    "CLIENT_SECRET",
    "CREDENTIAL",
    "CREDENTIALS",
    "DATABASE_URL",
    "DB_URI",
    "DOMAIN",
    "DOMAINS",
    "DSN",
    "EMAIL",
    "ENCRYPTION_KEY",
    "ENCRYPTION_KEYRING",
    "ENDPOINT",
    "ENDPOINT_URL",
    "HOST",
    "HOSTNAME",
    "ISSUER",
    "METADATA_URL",
    "NAMESPACE",
    "PASSWORD",
    "PASSWD",
    "PRIVATE_KEY",
    "PRINCIPAL",
    "PRINCIPAL_ARN",
    "PRINCIPAL_ID",
    "PWD",
    "QUEUE",
    "ORG_ID",
    "ORGANIZATION_ID",
    "ORIGIN",
    "ORIGINS",
    "RESOURCE_GROUP",
    "SECRET",
    "SECRET_ACCESS_KEY",
    "SECRET_KEY",
    "SERVICE_KEY",
    "SIGNING_KEY",
    "SUBSCRIPTION_ID",
    "TENANT_ID",
    "TOKEN",
    "URI",
    "URL",
    "USER",
    "USERNAME",
    "WORKSPACE_ID",
)

ACTIVITY_SQL: Final = """
SELECT
    count(*)::int AS total,
    count(*) FILTER (WHERE state = 'active')::int AS active,
    count(*) FILTER (WHERE state = 'idle')::int AS idle,
    count(*) FILTER (WHERE state = 'idle in transaction')::int AS idle_in_txn,
    count(*) FILTER (
        WHERE state = 'idle in transaction (aborted)'
    )::int AS idle_in_txn_aborted,
    count(*) FILTER (
        WHERE state = 'active' AND wait_event IS NOT NULL
    )::int AS waiting,
    max(EXTRACT(EPOCH FROM (now() - xact_start)))::float8 AS longest_txn,
    (
        max(EXTRACT(EPOCH FROM (now() - query_start)))
        FILTER (WHERE state = 'active')
    )::float8 AS longest_query
FROM pg_stat_activity
WHERE backend_type = 'client backend'
"""

WAIT_EVENT_SQL: Final = """
SELECT
    coalesce(wait_event_type, 'none') || ':' || coalesce(wait_event, 'none') AS event,
    count(*)::int AS sessions
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY 1
"""

APPLICATION_NAME_SQL: Final = """
SELECT
    coalesce(nullif(application_name, ''), 'unknown') AS application_name,
    count(*)::int AS sessions
FROM pg_stat_activity
WHERE backend_type = 'client backend'
GROUP BY 1
"""


def _application_name_artifact_label(application_name: str) -> str:
    """Retain fixed harness labels and fingerprint every other client label."""
    if application_name in KNOWN_APPLICATION_NAMES:
        return application_name
    return deployment_value_fingerprint(application_name)


DATABASE_COUNTERS_SQL: Final = """
SELECT
    coalesce(sum(xact_commit), 0)::bigint AS xact_commit,
    coalesce(sum(xact_rollback), 0)::bigint AS xact_rollback,
    coalesce(sum(deadlocks), 0)::bigint AS deadlocks
FROM pg_stat_database
WHERE datname IS NOT NULL
"""

SETTINGS_SQL: Final = """
SELECT name, setting, unit, source, boot_val
FROM pg_settings
WHERE name = ANY($1::text[])
"""

TABLE_EXISTS_SQL: Final = """
SELECT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = $1 AND table_name = $2
)
"""

WORKSPACE_RLS_CONTEXT_SQL: Final = """
SELECT
    set_config('app.rls_bypass', 'off', true),
    set_config('app.current_workspace_id', $1, true)
"""

TABLE_DRIFT_SQL: Final = """
SELECT
    pg_relation_size(c.oid)::bigint AS table_bytes,
    pg_indexes_size(c.oid)::bigint AS indexes_bytes,
    pg_total_relation_size(c.oid)::bigint AS total_relation_bytes,
    coalesce(s.n_live_tup, 0)::bigint AS live_tuples,
    coalesce(s.n_dead_tup, 0)::bigint AS dead_tuples,
    coalesce(s.n_tup_ins, 0)::bigint AS inserts,
    coalesce(s.n_tup_upd, 0)::bigint AS updates,
    coalesce(s.n_tup_del, 0)::bigint AS deletes,
    coalesce(s.n_tup_hot_upd, 0)::bigint AS hot_updates,
    coalesce(s.vacuum_count, 0)::bigint AS vacuum_count,
    coalesce(s.autovacuum_count, 0)::bigint AS autovacuum_count,
    coalesce(s.analyze_count, 0)::bigint AS analyze_count,
    coalesce(s.autoanalyze_count, 0)::bigint AS autoanalyze_count,
    s.last_vacuum,
    s.last_autovacuum,
    s.last_analyze,
    s.last_autoanalyze
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables AS s ON s.relid = c.oid
WHERE n.nspname = $1
  AND c.relname = $2
  AND c.relkind IN ('r', 'p')
"""

MONITOR_ROLE_SQL: Final = """
SELECT
    rolsuper AS is_superuser,
    pg_has_role(current_user, 'pg_read_all_stats', 'member') AS can_read_all_stats
FROM pg_roles
WHERE rolname = current_user
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _observability_failure_name(failure: BaseException) -> str:
    """Return useful leaf exception names for task-group failures."""
    if not isinstance(failure, BaseExceptionGroup):
        return type(failure).__name__

    leaf_names: list[str] = []
    pending: list[BaseException] = list(failure.exceptions)
    while pending:
        nested = pending.pop(0)
        if isinstance(nested, BaseExceptionGroup):
            pending[0:0] = nested.exceptions
            continue
        name = type(nested).__name__
        if name not in leaf_names:
            leaf_names.append(name)
    if len(leaf_names) == 1:
        return leaf_names[0]
    return f"ExceptionGroup[{','.join(leaf_names)}]"


def _append_observability_failure(path: Path, failure_name: str) -> None:
    """Best-effort gap record that cannot prevent the final manifest."""
    with contextlib.suppress(OSError):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "sampled_at": _utc_now_iso(),
                        "observability_failure": failure_name,
                    }
                )
                + "\n"
            )


class CollectorConfigurationError(ValueError):
    """The collector was not given a reproducible cluster invocation."""


class CollectorPreflightTimeoutError(CollectorConfigurationError):
    """Cluster validation exceeded the collector's shared readiness deadline."""


class CollectorStartupInterruptedError(RuntimeError):
    """The collector received a stop signal during synchronous preflight."""


def _run_command(
    args: list[str],
    *,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
    raise_on_timeout: bool = False,
) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        if raise_on_timeout:
            raise CollectorPreflightTimeoutError(
                "cluster validation command exceeded the readiness deadline"
            ) from exc
        return 1, "", f"{type(exc).__name__}: {exc}"
    except OSError as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout, result.stderr


def _run_preflight_command(
    args: list[str],
    *,
    deadline: float | None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run one cluster validation command within a shared absolute deadline."""
    if deadline is None:
        return _run_command(args, env=env)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CollectorPreflightTimeoutError(
            "cluster validation exceeded the readiness deadline"
        )
    return _run_command(
        args,
        timeout=remaining,
        env=env,
        raise_on_timeout=True,
    )


def _as_int(value: object) -> int:
    """Coerce a driver-typed scalar to int, treating NULL as zero."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(str(value))


def _parse_size_bytes(value: str) -> int:
    """Parse the human-readable byte units emitted by `docker stats`."""
    match = SIZE_RE.fullmatch(value)
    if match is None:
        raise ResourceUsageCaptureError(f"invalid Docker size value: {value!r}")
    amount = float(match.group(1))
    unit = match.group(2)
    decimal_units = {
        "B": 1,
        "kB": 1_000,
        "MB": 1_000_000,
        "GB": 1_000_000_000,
        "TB": 1_000_000_000_000,
        "PB": 1_000_000_000_000_000,
        "EB": 1_000_000_000_000_000_000,
    }
    binary_units = {
        "KiB": 1 << 10,
        "MiB": 1 << 20,
        "GiB": 1 << 30,
        "TiB": 1 << 40,
        "PiB": 1 << 50,
        "EiB": 1 << 60,
    }
    multiplier = decimal_units.get(unit) or binary_units.get(unit)
    if multiplier is None:
        raise ResourceUsageCaptureError(f"unsupported Docker size unit: {unit!r}")
    return round(amount * multiplier)


def _parse_size_pair(value: str) -> tuple[int, int]:
    left, separator, right = value.partition("/")
    if not separator:
        raise ResourceUsageCaptureError(f"invalid Docker I/O pair: {value!r}")
    return _parse_size_bytes(left), _parse_size_bytes(right)


def _parse_percent(value: str) -> float:
    stripped = value.strip()
    if not stripped.endswith("%"):
        raise ResourceUsageCaptureError(f"invalid percentage value: {value!r}")
    try:
        return float(stripped.removesuffix("%"))
    except ValueError as exc:
        raise ResourceUsageCaptureError(f"invalid percentage value: {value!r}") from exc


def _host_resource_usage() -> HostResourceUsage:
    """Sample portable host load plus Linux or macOS memory pressure."""
    load_average = os.getloadavg()
    logical_cpu_count = os.cpu_count() or 1
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.is_file():
        # /proc/meminfo is a dynamic name/value mapping rather than a fixed
        # application data shape, so a dictionary is appropriate here.
        meminfo: dict[str, int] = {}
        for line in meminfo_path.read_text(encoding="utf-8").splitlines():
            name, separator, raw_value = line.partition(":")
            if not separator:
                continue
            value_kib = raw_value.strip().split(maxsplit=1)[0]
            meminfo[name] = int(value_kib) * 1024
        total_bytes = meminfo.get("MemTotal", 0)
        available_bytes = meminfo.get("MemAvailable", 0)
    else:
        code, stdout, _ = _run_command(
            ["memory_pressure", "-Q"],
            timeout=SAMPLER_OPERATION_TIMEOUT_SECONDS,
        )
        total_match = re.search(r"The system has (\d+) ", stdout)
        free_match = re.search(r"System-wide memory free percentage: (\d+)%", stdout)
        if code != 0 or total_match is None or free_match is None:
            raise ResourceUsageCaptureError(
                "host memory pressure is unavailable on this platform"
            )
        total_bytes = int(total_match.group(1))
        available_bytes = round(total_bytes * int(free_match.group(1)) / 100)

    if total_bytes <= 0 or not 0 <= available_bytes <= total_bytes:
        raise ResourceUsageCaptureError("host memory pressure returned invalid values")
    return HostResourceUsage(
        logical_cpu_count=logical_cpu_count,
        load_average_1m=float(load_average[0]),
        load_average_5m=float(load_average[1]),
        load_average_15m=float(load_average[2]),
        memory_total_bytes=total_bytes,
        memory_available_bytes=available_bytes,
        memory_used_percent=(total_bytes - available_bytes) / total_bytes * 100,
    )


def _quote_identifier(value: str) -> str:
    """Validate then quote an SQL identifier.

    Only identifiers matching a strict pattern are accepted, because schema and
    table names have to be interpolated into DDL-shaped SQL that cannot use
    bind parameters.
    """
    if not IDENTIFIER_RE.match(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def _is_sensitive_environment_name(name: str) -> bool:
    return name.upper().endswith(SENSITIVE_ENV_SUFFIXES)


def _shareable_compose_path(value: str, repo_root: Path | None) -> str:
    """Relativize repository paths and redact other absolute host paths."""
    if value.startswith("~"):
        return REDACTED_PATH_VALUE
    path = Path(value)
    if not path.is_absolute():
        return value
    if repo_root is not None:
        try:
            relative = path.relative_to(repo_root.resolve())
        except ValueError:
            pass
        else:
            return "<repo>" if relative == Path() else f"<repo>/{relative.as_posix()}"
    return REDACTED_PATH_VALUE


def _redact_short_volume_path(value: str, repo_root: Path | None) -> str:
    """Redact only the host source in a short-syntax bind mount."""
    source, separator, remainder = value.partition(":")
    if not separator:
        return value
    shareable_source = _shareable_compose_path(source, repo_root)
    return f"{shareable_source}:{remainder}"


def _redact_compose_paths(
    value: dict[str, object] | list[object],
    repo_root: Path | None,
) -> None:
    """Redact path-bearing fields in the open-ended Compose schema."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                _redact_compose_paths(item, repo_root)
        return

    for key, item in tuple(value.items()):
        if isinstance(item, str) and key in {
            "build",
            "context",
            "dockerfile",
            "file",
            "path",
            "source",
        }:
            value[key] = _shareable_compose_path(item, repo_root)
        elif key == "volumes" and isinstance(item, list):
            for index, mount in enumerate(item):
                if isinstance(mount, str):
                    item[index] = _redact_short_volume_path(mount, repo_root)
                elif isinstance(mount, (dict, list)):
                    _redact_compose_paths(mount, repo_root)
        elif key in {"additional_contexts", "env_file", "include"}:
            if isinstance(item, str):
                value[key] = _shareable_compose_path(item, repo_root)
            elif isinstance(item, list):
                for index, path in enumerate(item):
                    if isinstance(path, str):
                        item[index] = _shareable_compose_path(path, repo_root)
                    elif isinstance(path, (dict, list)):
                        _redact_compose_paths(path, repo_root)
            elif isinstance(item, dict):
                for name, path in tuple(item.items()):
                    if isinstance(path, str):
                        item[name] = _shareable_compose_path(path, repo_root)
                    elif isinstance(path, (dict, list)):
                        _redact_compose_paths(path, repo_root)
        elif isinstance(item, (dict, list)):
            _redact_compose_paths(item, repo_root)


def _redact_sensitive_compose_values(
    value: dict[str, object] | list[object],
) -> None:
    """Redact sensitive named values anywhere in the open-ended Compose model."""
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                name, separator, _ = item.partition("=")
                if separator and _is_sensitive_environment_name(name):
                    value[index] = f"{name}={REDACTED_ENV_VALUE}"
            elif isinstance(item, (dict, list)):
                _redact_sensitive_compose_values(item)
        return

    for name, item in tuple(value.items()):
        if _is_sensitive_environment_name(name):
            value[name] = REDACTED_ENV_VALUE
        elif isinstance(item, (dict, list)):
            _redact_sensitive_compose_values(item)


def _redact_compose_config(
    rendered_config: str,
    *,
    repo_root: Path | None = None,
) -> str:
    """Redact sensitive and worktree-derived values from the Compose model."""
    loaded: object = yaml.safe_load(rendered_config)
    if not isinstance(loaded, dict):
        raise ValueError("Compose configuration is not a mapping")

    # Compose is an open-ended third-party schema, so traverse its loaded
    # mapping without pretending it has a fixed internal type.
    root = cast(dict[str, object], loaded)
    _redact_compose_paths(root, repo_root)
    _redact_sensitive_compose_values(root)
    project_name = root.get("name")
    if isinstance(project_name, str):
        root["name"] = compose_project_fingerprint(project_name)
    for section_name in ("configs", "networks", "secrets", "volumes"):
        section_obj = root.get(section_name)
        if not isinstance(section_obj, dict):
            continue
        section = cast(dict[str, object], section_obj)
        for resource_obj in section.values():
            if not isinstance(resource_obj, dict):
                continue
            resource = cast(dict[str, object], resource_obj)
            resource_name = resource.get("name")
            if isinstance(resource_name, str):
                resource["name"] = deployment_value_fingerprint(resource_name)
    services_obj = root.get("services")
    if not isinstance(services_obj, dict):
        raise ValueError("Compose configuration has no services mapping")
    services = cast(dict[str, object], services_obj)
    for service_obj in services.values():
        if not isinstance(service_obj, dict):
            continue
        service = cast(dict[str, object], service_obj)
        if "container_name" in service:
            service["container_name"] = REDACTED_ENV_VALUE
        image = service.get("image")
        if isinstance(image, str):
            service["image"] = deployment_value_fingerprint(image)

    return yaml.safe_dump(root, sort_keys=False)


def _workspace_schema_name(workspace_id: str) -> str:
    """Return the exact physical table schema for one Tracecat workspace."""
    return f"tables_{WorkspaceUUID.new(workspace_id).short()}"


class ArtifactDirectoryReuseError(RuntimeError):
    """The requested run directory already contains experiment artifacts."""


class ResourceUsageCaptureError(RuntimeError):
    """Runtime container or host resource usage could not be sampled."""


class DbPoolMetricsCaptureError(RuntimeError):
    """A required SQLAlchemy pool-metrics sample could not be captured."""


class PgDogMetricsCaptureError(RuntimeError):
    """A required PgDog OpenMetrics sample could not be captured."""


class SamplingReadinessError(RuntimeError):
    """The collector stopped before every required signal produced a sample."""


class RunnerLifecycleError(RuntimeError):
    """The runner completion marker is malformed or targets another run."""


class RecoveryWindowInterruptedError(RuntimeError):
    """The collector was stopped before its required recovery window elapsed."""


class MonitoringRoleError(RuntimeError):
    """The collector DSN does not identify the required least-privileged role."""


class RequiredArtifactCaptureError(RuntimeError):
    """A required reproducibility artifact could not be captured."""


class ComposeConfigCaptureError(RequiredArtifactCaptureError):
    """The resolved Compose configuration could not be captured."""


class ContainerStateCaptureError(RequiredArtifactCaptureError):
    """The selected cluster's complete container state could not be captured."""


class RowCorrectnessCaptureError(RequiredArtifactCaptureError):
    """The fixture table's row correctness could not be captured."""


class TableDriftCaptureError(RequiredArtifactCaptureError):
    """The fixture table's physical drift metrics could not be captured."""


class ServiceLogCaptureError(RequiredArtifactCaptureError):
    """A required aggregate service-log diagnostic could not be captured."""


class RunnerArtifactsCaptureError(RequiredArtifactCaptureError):
    """One or more required runner artifacts are missing."""


class CommitCaptureError(RequiredArtifactCaptureError):
    """The tested Tracecat commit could not be captured."""


def _claim_artifact_directory(artifact_dir: Path, run_id: str) -> Path:
    """Atomically claim an empty run directory without overwriting evidence."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if any(artifact_dir.iterdir()):
        raise ArtifactDirectoryReuseError(
            f"artifact directory is not empty: {artifact_dir}"
        )

    claim_path = artifact_dir / RUN_CLAIM_FILENAME
    try:
        with claim_path.open("x", encoding="utf-8") as handle:
            json.dump(
                {
                    "run_id": run_id_fingerprint(run_id),
                    "claimed_at": _utc_now_iso(),
                },
                handle,
                indent=2,
            )
    except FileExistsError as exc:
        raise ArtifactDirectoryReuseError(
            f"artifact directory is already claimed: {artifact_dir}"
        ) from exc

    unexpected_entries = tuple(
        path for path in artifact_dir.iterdir() if path != claim_path
    )
    if unexpected_entries:
        claim_path.unlink(missing_ok=True)
        raise ArtifactDirectoryReuseError(
            f"artifact directory changed while being claimed: {artifact_dir}"
        )
    return claim_path


def _validate_artifact_directory_claim(
    artifact_dir: Path,
    claim_path: Path,
) -> None:
    """Verify that a preflight claim still exclusively owns the run directory."""
    expected_claim_path = artifact_dir / RUN_CLAIM_FILENAME
    if claim_path != expected_claim_path or not claim_path.is_file():
        raise ArtifactDirectoryReuseError(
            f"artifact directory claim is invalid: {artifact_dir}"
        )
    unexpected_entries = tuple(
        path for path in artifact_dir.iterdir() if path != claim_path
    )
    if unexpected_entries:
        raise ArtifactDirectoryReuseError(
            f"artifact directory changed after being claimed: {artifact_dir}"
        )


class PgSampler:
    """Holds one long-lived sampling connection plus a periodic slot probe."""

    def __init__(self, dsn: str, settings_of_interest: tuple[str, ...]) -> None:
        self._dsn = dsn
        self._settings_of_interest = settings_of_interest
        self._conn: asyncpg.Connection | None = None
        self._prev_commit = 0
        self._prev_rollback = 0
        self._prev_deadlocks = 0
        self._primed = False
        self._connection_slot_errors = 0
        self._max_connections = 0
        self._superuser_reserved = 0

    async def connect(self) -> None:
        conn = await asyncpg.connect(
            self._dsn, server_settings={"application_name": "load-test-collector"}
        )
        self._conn = conn
        role = await conn.fetchrow(MONITOR_ROLE_SQL)
        if (
            role is None
            or bool(role["is_superuser"])
            or not bool(role["can_read_all_stats"])
        ):
            await self.close()
            raise MonitoringRoleError(
                "monitoring DSN must use a non-superuser member of pg_read_all_stats"
            )
        self._max_connections = _as_int(await conn.fetchval("SHOW max_connections"))
        self._superuser_reserved = _as_int(
            await conn.fetchval("SHOW superuser_reserved_connections")
        )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def probe_connection_slots(self) -> None:
        """Open and close a throwaway connection to detect slot exhaustion.

        Uses asyncpg's typed `TooManyConnectionsError` (SQLSTATE 53300) rather
        than matching error text. The required monitoring DSN must identify a
        non-superuser role, so a failure detects exhaustion of ordinary
        connection slots while PostgreSQL's administrative reserve stays
        protected.
        """
        try:
            probe = await asyncpg.connect(
                self._dsn,
                server_settings={"application_name": "load-test-probe"},
                timeout=5,
            )
        except asyncpg.exceptions.TooManyConnectionsError:
            self._connection_slot_errors += 1
            return
        except (OSError, asyncpg.PostgresError, TimeoutError):
            return
        await probe.close()

    async def sample(self) -> PgActivitySample:
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")

        activity = await conn.fetchrow(ACTIVITY_SQL)
        wait_rows = await conn.fetch(WAIT_EVENT_SQL)
        app_rows = await conn.fetch(APPLICATION_NAME_SQL)
        counters = await conn.fetchrow(DATABASE_COUNTERS_SQL)
        if activity is None or counters is None:
            raise RuntimeError("PostgreSQL returned no activity rows")

        commit = int(counters["xact_commit"])
        rollback = int(counters["xact_rollback"])
        deadlocks = int(counters["deadlocks"])
        if not self._primed:
            self._prev_commit, self._prev_rollback, self._prev_deadlocks = (
                commit,
                rollback,
                deadlocks,
            )
            self._primed = True

        sample = PgActivitySample(
            sampled_at=_utc_now_iso(),
            monotonic=time.monotonic(),
            max_connections=self._max_connections,
            superuser_reserved_connections=self._superuser_reserved,
            total_connections=int(activity["total"]),
            active=int(activity["active"]),
            idle=int(activity["idle"]),
            idle_in_transaction=int(activity["idle_in_txn"]),
            idle_in_transaction_aborted=int(activity["idle_in_txn_aborted"]),
            waiting=int(activity["waiting"]),
            wait_events={str(r["event"]): int(r["sessions"]) for r in wait_rows},
            application_names={
                _application_name_artifact_label(str(r["application_name"])): int(
                    r["sessions"]
                )
                for r in app_rows
            },
            longest_transaction_seconds=(
                float(activity["longest_txn"])
                if activity["longest_txn"] is not None
                else None
            ),
            longest_query_seconds=(
                float(activity["longest_query"])
                if activity["longest_query"] is not None
                else None
            ),
            xact_commit_delta=commit - self._prev_commit,
            xact_rollback_delta=rollback - self._prev_rollback,
            deadlocks_delta=deadlocks - self._prev_deadlocks,
            connection_slot_errors=self._connection_slot_errors,
        )
        self._prev_commit, self._prev_rollback, self._prev_deadlocks = (
            commit,
            rollback,
            deadlocks,
        )
        return sample

    async def effective_settings(self) -> list[dict[str, str | None]]:
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")
        rows = await conn.fetch(SETTINGS_SQL, list(self._settings_of_interest))
        return [
            {
                "name": str(row["name"]),
                "setting": str(row["setting"]),
                "unit": str(row["unit"]) if row["unit"] is not None else None,
                "source": str(row["source"]),
                "boot_val": str(row["boot_val"])
                if row["boot_val"] is not None
                else None,
            }
            for row in rows
        ]

    async def row_correctness(
        self, workspace_id: str, table_name: str, run_id: str | None
    ) -> RowCorrectness | None:
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")
        schema_name = _workspace_schema_name(workspace_id)
        workspace_uuid = str(WorkspaceUUID.new(workspace_id))
        async with conn.transaction():
            # The monitor is deliberately not allowed to bypass RLS. Set the
            # workspace context transaction-locally so fixture counts observe
            # exactly this workspace's rows.
            await conn.execute(WORKSPACE_RLS_CONTEXT_SQL, workspace_uuid)
            table_exists = await conn.fetchval(
                TABLE_EXISTS_SQL, schema_name, table_name
            )
            if not table_exists:
                return None

            qualified = (
                f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"
            )
            totals = await conn.fetchrow(
                f"SELECT count(*)::bigint AS total, "  # noqa: S608 - identifiers validated
                f"count(DISTINCT dedupe_key)::bigint AS distinct_keys FROM {qualified}"
            )
            per_run = await conn.fetchrow(
                f"SELECT count(*)::bigint AS total, "  # noqa: S608 - identifiers validated
                f"count(DISTINCT dedupe_key)::bigint AS distinct_keys "
                f"FROM {qualified} WHERE run_id = $1",
                run_id or "",
            )
        if totals is None or per_run is None:
            return None

        total_rows = int(totals["total"])
        distinct_keys = int(totals["distinct_keys"])
        return RowCorrectness(
            workspace_fingerprint=workspace_fingerprint(workspace_uuid),
            table_name=table_name,
            total_rows=total_rows,
            distinct_dedupe_keys=distinct_keys,
            duplicate_dedupe_keys=total_rows - distinct_keys,
            rows_for_run=int(per_run["total"]),
            distinct_dedupe_keys_for_run=int(per_run["distinct_keys"]),
        )

    async def table_drift(
        self, workspace_id: str, table_name: str
    ) -> TableDrift | None:
        """Capture relation sizes and pg_stat_user_tables maintenance counters."""
        conn = self._conn
        if conn is None:
            raise RuntimeError("sampler is not connected")
        schema_name = _workspace_schema_name(workspace_id)
        row = await conn.fetchrow(TABLE_DRIFT_SQL, schema_name, table_name)
        if row is None:
            return None

        def optional_string(name: str) -> str | None:
            value = row[name]
            return str(value) if value is not None else None

        return TableDrift(
            workspace_fingerprint=workspace_fingerprint(
                str(WorkspaceUUID.new(workspace_id))
            ),
            table_name=table_name,
            table_bytes=_as_int(row["table_bytes"]),
            indexes_bytes=_as_int(row["indexes_bytes"]),
            total_relation_bytes=_as_int(row["total_relation_bytes"]),
            live_tuples=_as_int(row["live_tuples"]),
            dead_tuples=_as_int(row["dead_tuples"]),
            inserts=_as_int(row["inserts"]),
            updates=_as_int(row["updates"]),
            deletes=_as_int(row["deletes"]),
            hot_updates=_as_int(row["hot_updates"]),
            vacuum_count=_as_int(row["vacuum_count"]),
            autovacuum_count=_as_int(row["autovacuum_count"]),
            analyze_count=_as_int(row["analyze_count"]),
            autoanalyze_count=_as_int(row["autoanalyze_count"]),
            last_vacuum=optional_string("last_vacuum"),
            last_autovacuum=optional_string("last_autovacuum"),
            last_analyze=optional_string("last_analyze"),
            last_autoanalyze=optional_string("last_autoanalyze"),
        )


class TemporalSampler:
    """Samples workflow and activity task-queue backlog through Temporal RPC."""

    def __init__(
        self,
        target: str,
        namespace: str,
        workflow_task_queues: tuple[str, ...],
        activity_task_queues: tuple[str, ...],
    ) -> None:
        self._target = target
        self._namespace = namespace
        self._workflow_task_queues = workflow_task_queues
        self._activity_task_queues = activity_task_queues
        self._client: TemporalClient | None = None

    async def connect(self) -> None:
        self._client = await TemporalClient.connect(
            target_host=self._target,
            namespace=self._namespace,
            identity="load-test-collector",
            data_converter=get_data_converter(
                compression_enabled=(
                    tracecat_config.TRACECAT__CONTEXT_COMPRESSION_ENABLED
                )
            ),
        )

    @property
    def client(self) -> TemporalClient:
        if self._client is None:
            raise RuntimeError("Temporal sampler is not connected")
        return self._client

    async def _describe(
        self,
        task_queue: str,
        task_queue_type: TaskQueueType.ValueType,
    ) -> TemporalTaskQueueStats:
        client = self._client
        if client is None:
            raise RuntimeError("Temporal sampler is not connected")
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=self._namespace,
                task_queue=TaskQueue(name=task_queue),
                task_queue_type=task_queue_type,
                report_stats=True,
            )
        )
        stats = response.stats
        backlog_age = stats.approximate_backlog_age
        return TemporalTaskQueueStats(
            approximate_backlog_count=int(stats.approximate_backlog_count),
            approximate_backlog_age_seconds=(
                float(backlog_age.seconds) + float(backlog_age.nanos) / 1_000_000_000
            ),
            tasks_add_rate=float(stats.tasks_add_rate),
            tasks_dispatch_rate=float(stats.tasks_dispatch_rate),
        )

    async def _sample_queues(
        self,
        task_queues: tuple[str, ...],
        task_queue_type: TaskQueueType.ValueType,
    ) -> dict[str, TemporalTaskQueueStats]:
        stats = await asyncio.gather(
            *(self._describe(queue, task_queue_type) for queue in task_queues)
        )
        return {
            deployment_value_fingerprint(queue): queue_stats
            for queue, queue_stats in zip(task_queues, stats, strict=True)
        }

    async def sample(self) -> TemporalBacklogSample:
        workflow_stats, activity_stats = await asyncio.gather(
            self._sample_queues(
                self._workflow_task_queues,
                TEMPORAL_WORKFLOW_TASK_QUEUE_TYPE,
            ),
            self._sample_queues(
                self._activity_task_queues,
                TEMPORAL_ACTIVITY_TASK_QUEUE_TYPE,
            ),
        )
        return TemporalBacklogSample(
            sampled_at=_utc_now_iso(),
            monotonic=time.monotonic(),
            workflow_task_queues=workflow_stats,
            activity_task_queues=activity_stats,
        )


class DockerResourceSampler:
    """Samples selected containers plus host load and memory pressure."""

    def __init__(self, compose_project: str) -> None:
        self._compose_project = compose_project
        self._initial_sample: ResourceUsageSample | None = None

    async def connect(self) -> None:
        """Validate that host memory pressure and selected containers are visible."""
        self._initial_sample = await self._capture()

    async def sample(self) -> ResourceUsageSample:
        if self._initial_sample is not None:
            sample = self._initial_sample
            self._initial_sample = None
            return sample
        return await self._capture()

    async def _capture(self) -> ResourceUsageSample:
        ps_code, ps_stdout, _ = await asyncio.to_thread(
            _run_command,
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={self._compose_project}",
                "--format",
                '{{.ID}}\t{{.Label "com.docker.compose.service"}}',
            ],
            timeout=SAMPLER_OPERATION_TIMEOUT_SECONDS,
        )
        container_rows = [
            line.strip() for line in ps_stdout.splitlines() if line.strip()
        ]
        if ps_code != 0 or not container_rows:
            raise ResourceUsageCaptureError(
                "docker ps could not find running containers for the selected project"
            )
        container_services: dict[str, str] = {}
        for row in container_rows:
            container_id, separator, service = row.partition("\t")
            if (
                not separator
                or not container_id
                or not COMPOSE_SERVICE_RE.fullmatch(service)
            ):
                raise ResourceUsageCaptureError(
                    "docker ps returned invalid Compose service metadata"
                )
            container_services[container_id] = service

        stats_code, stats_stdout, _ = await asyncio.to_thread(
            _run_command,
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                *container_services,
            ],
            timeout=SAMPLER_OPERATION_TIMEOUT_SECONDS,
        )
        if stats_code != 0:
            raise ResourceUsageCaptureError(
                "docker stats could not sample the selected project"
            )

        containers: list[ContainerResourceUsage] = []
        try:
            for line in stats_stdout.splitlines():
                if not line.strip():
                    continue
                parsed: object = json.loads(line)
                if not isinstance(parsed, dict):
                    raise TypeError("docker stats record is not an object")
                payload = cast(dict[str, object], parsed)
                memory_usage, memory_limit = _parse_size_pair(
                    str(payload.get("MemUsage", ""))
                )
                network_input, network_output = _parse_size_pair(
                    str(payload.get("NetIO", ""))
                )
                block_read, block_write = _parse_size_pair(
                    str(payload.get("BlockIO", ""))
                )
                container_id = str(payload.get("ID", ""))
                service = container_services.get(container_id)
                if service is None:
                    raise ValueError("docker stats returned an unknown container")
                containers.append(
                    ContainerResourceUsage(
                        container_id=container_id,
                        service=service,
                        cpu_percent=_parse_percent(str(payload.get("CPUPerc", ""))),
                        memory_usage_bytes=memory_usage,
                        memory_limit_bytes=memory_limit,
                        memory_percent=_parse_percent(str(payload.get("MemPerc", ""))),
                        network_input_bytes=network_input,
                        network_output_bytes=network_output,
                        block_read_bytes=block_read,
                        block_write_bytes=block_write,
                        pids=int(str(payload.get("PIDs", "0"))),
                    )
                )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ResourceUsageCaptureError(
                "docker stats returned an invalid resource record"
            ) from exc
        if not containers:
            raise ResourceUsageCaptureError("docker stats returned no resource records")

        host = await asyncio.to_thread(_host_resource_usage)
        return ResourceUsageSample(
            sampled_at=_utc_now_iso(),
            monotonic=time.monotonic(),
            host=host,
            containers=containers,
        )


def _parse_db_pool_metrics_payload(payload: object) -> DbPoolMetricsPayload:
    """Validate the versioned shape served by ``tracecat.db.pool_metrics``."""
    if not isinstance(payload, dict):
        raise DbPoolMetricsCaptureError("DB pool metrics response is not an object")
    document = cast(dict[str, object], payload)
    if document.get("schema_version") != 2:
        raise DbPoolMetricsCaptureError("DB pool metrics schema version is unsupported")
    sampled_at = document.get("sampled_at_unix_seconds")
    pools = document.get("pools")
    if not isinstance(sampled_at, int | float) or not isinstance(pools, list):
        raise DbPoolMetricsCaptureError("DB pool metrics document is incomplete")

    required_pool_fields = {
        "pool",
        "configured_size",
        "configured_max_overflow",
        "configured_timeout_seconds",
        "checkouts_total",
        "connections_created_total",
        "checkout_timeouts_total",
        "returns_total",
        "checked_in",
        "checked_out",
        "overflow",
        "checked_out_high_water",
        "overflow_high_water",
        "checkout_wait",
        "connection_open",
        "connection_hold",
    }
    for pool in pools:
        if not isinstance(pool, dict) or not required_pool_fields <= pool.keys():
            raise DbPoolMetricsCaptureError("DB pool metrics pool record is incomplete")
        for latency_name in (
            "checkout_wait",
            "connection_open",
            "connection_hold",
        ):
            latency = pool[latency_name]
            if (
                not isinstance(latency, dict)
                or not {
                    "count",
                    "sum_seconds",
                    "max_seconds",
                    "bucket_upper_bounds_seconds",
                    "cumulative_bucket_counts",
                }
                <= latency.keys()
            ):
                raise DbPoolMetricsCaptureError(
                    "DB pool metrics latency record is incomplete"
                )
    return cast(DbPoolMetricsPayload, document)


class DbPoolMetricsSampler:
    """Samples process-local SQLAlchemy metrics from every service replica."""

    def __init__(self, endpoints: tuple[DbPoolMetricsEndpoint, ...]) -> None:
        self._endpoints = endpoints
        self._client = httpx.AsyncClient(timeout=SAMPLER_OPERATION_TIMEOUT_SECONDS)
        self._initial_sample: DbPoolMetricsSample | None = None

    async def connect(self) -> None:
        self._initial_sample = await self._capture()

    async def close(self) -> None:
        await self._client.aclose()

    async def sample(self) -> DbPoolMetricsSample:
        if self._initial_sample is not None:
            sample = self._initial_sample
            self._initial_sample = None
            return sample
        return await self._capture()

    async def _sample_endpoint(
        self,
        endpoint: DbPoolMetricsEndpoint,
    ) -> DbPoolEndpointSample:
        response = await self._client.get(endpoint.url)
        response.raise_for_status()
        try:
            payload: object = response.json()
        except json.JSONDecodeError as exc:
            raise DbPoolMetricsCaptureError(
                "DB pool metrics endpoint returned invalid JSON"
            ) from exc
        return DbPoolEndpointSample(
            service=endpoint.service,
            replica_index=endpoint.replica_index,
            metrics=_parse_db_pool_metrics_payload(payload),
        )

    async def _capture(self) -> DbPoolMetricsSample:
        endpoints = await asyncio.gather(
            *(self._sample_endpoint(endpoint) for endpoint in self._endpoints)
        )
        return DbPoolMetricsSample(
            sampled_at=_utc_now_iso(),
            endpoints=list(endpoints),
        )


class PgDogMetricsSampler:
    """Samples PgDog's native OpenMetrics endpoint without dropping dimensions."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = httpx.AsyncClient(timeout=SAMPLER_OPERATION_TIMEOUT_SECONDS)
        self._initial_sample: PgDogMetricsSample | None = None

    async def connect(self) -> None:
        self._initial_sample = await self._capture()

    async def close(self) -> None:
        await self._client.aclose()

    async def sample(self) -> PgDogMetricsSample:
        if self._initial_sample is not None:
            sample = self._initial_sample
            self._initial_sample = None
            return sample
        return await self._capture()

    async def _capture(self) -> PgDogMetricsSample:
        response = await self._client.get(self._url)
        response.raise_for_status()
        exposition = response.text
        if not exposition.strip():
            raise PgDogMetricsCaptureError("PgDog metrics endpoint returned no data")
        return PgDogMetricsSample(
            sampled_at=_utc_now_iso(),
            openmetrics=exposition,
        )


def _cluster_wrapper_prefix(
    repo_root: Path,
    cluster_num: int,
    compose_files: tuple[str, ...],
    ee_multi_tenant: bool,
) -> list[str]:
    """Reconstruct the cluster wrapper invocation from its resolved file list."""
    profile: str | None = None
    sandbox_enabled = False
    overrides: list[Path] = []

    for raw_path in compose_files:
        path = Path(raw_path)
        resolved = (repo_root / path).resolve() if not path.is_absolute() else path
        if resolved.parent == repo_root and resolved.name in COMPOSE_PROFILE_FILES:
            if profile is not None:
                raise CollectorConfigurationError(
                    "compose file list contains more than one profile file"
                )
            profile = COMPOSE_PROFILE_FILES[resolved.name]
        elif resolved == repo_root / SANDBOX_COMPOSE_FILE:
            sandbox_enabled = True
        else:
            overrides.append(resolved)

    if profile is None:
        raise CollectorConfigurationError(
            "compose file list must include one repository profile file"
        )

    args = [
        str(repo_root / "scripts/cluster"),
        str(cluster_num),
        "--profile",
        profile,
        "--sandbox" if sandbox_enabled else "--no-sandbox",
        "--ee-multi-tenant",
        "true" if ee_multi_tenant else "false",
    ]
    for override in overrides:
        args.extend(("--compose-override", str(override)))
    return args


def _cluster_command_env(
    public_api_url: str,
    *,
    public_app_url: str | None = None,
) -> dict[str, str]:
    """Override public URLs while letting the wrapper rebuild all other ports."""
    normalized_api_url = public_api_url.rstrip("/")
    if not normalized_api_url.endswith("/api"):
        raise CollectorConfigurationError("--public-api-url must end with /api")
    normalized_app_url = (
        public_app_url.rstrip("/")
        if public_app_url is not None
        else normalized_api_url.removesuffix("/api")
    )
    env = dict(os.environ)
    env["CLUSTER_PUBLIC_API_URL_OVERRIDE"] = normalized_api_url
    env["CLUSTER_PUBLIC_APP_URL_OVERRIDE"] = normalized_app_url
    return env


def resolve_compose_project(
    repo_root: Path,
    cluster_num: int,
    compose_files: tuple[str, ...],
    ee_multi_tenant: bool,
    public_api_url: str,
    *,
    deadline: float | None = None,
) -> str:
    """Ask the cluster wrapper for the authoritative selected project name."""
    args = _cluster_wrapper_prefix(
        repo_root, cluster_num, compose_files, ee_multi_tenant
    )
    code, stdout, stderr = _run_preflight_command(
        [*args, "compose-files"],
        deadline=deadline,
        env=_cluster_command_env(public_api_url),
    )
    if code != 0:
        raise CollectorConfigurationError(
            f"cluster wrapper could not resolve Compose context: {stderr.strip()}"
        )
    for line in stdout.splitlines():
        label, separator, value = line.partition(":")
        if separator and label.strip() == "Project":
            project = value.strip()
            if project:
                return project
    raise CollectorConfigurationError("cluster wrapper did not report a project")


def _parse_compose_service_hashes(rendered_hashes: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in rendered_hashes.splitlines():
        parts = line.split()
        if (
            len(parts) != 2
            or not re.fullmatch(r"[a-f0-9]{64}", parts[1])
            or parts[0] in hashes
        ):
            raise CollectorConfigurationError(
                "cluster wrapper returned invalid Compose service hashes"
            )
        hashes[parts[0]] = parts[1]
    if not hashes:
        raise CollectorConfigurationError(
            "cluster wrapper returned no Compose service hashes"
        )
    return hashes


def _deployed_compose_public_urls(
    container_environments: list[dict[str, str]],
) -> ComposePublicUrls:
    """Resolve one consistent app/API URL pair without retaining other env."""
    app_keys = {"TRACECAT__PUBLIC_APP_URL", "NEXT_PUBLIC_APP_URL"}
    api_keys = {"TRACECAT__PUBLIC_API_URL", "NEXT_PUBLIC_API_URL"}
    app_urls = {
        value
        for environment in container_environments
        for key, value in environment.items()
        if key in app_keys
    }
    api_urls = {
        value
        for environment in container_environments
        for key, value in environment.items()
        if key in api_keys
    }
    if len(app_urls) != 1 or len(api_urls) != 1:
        raise CollectorConfigurationError(
            "deployed Compose containers do not expose one consistent public "
            "app/API URL pair"
        )
    return ComposePublicUrls(app=app_urls.pop(), api=api_urls.pop())


def validate_running_compose_project(
    repo_root: Path,
    cluster_num: int,
    compose_files: tuple[str, ...],
    ee_multi_tenant: bool,
    compose_project: str,
    *,
    deadline: float | None = None,
) -> ComposePublicUrls:
    """Bind the supplied files and environment to the deployed Compose project."""
    args = _cluster_wrapper_prefix(
        repo_root, cluster_num, compose_files, ee_multi_tenant
    )
    expected_files = ",".join(
        str(
            (
                repo_root / raw_path
                if not Path(raw_path).is_absolute()
                else Path(raw_path)
            ).resolve()
        )
        for raw_path in compose_files
    )

    code, stdout, stderr = _run_preflight_command(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={compose_project}",
            "--filter",
            "label=com.docker.compose.oneoff=False",
            "--format",
            "{{.ID}}",
        ],
        deadline=deadline,
    )
    container_ids = tuple(stdout.split())
    if code != 0 or not container_ids:
        raise CollectorConfigurationError(
            f"selected Compose project has no deployed containers: {stderr.strip()}"
        )

    code, stdout, stderr = _run_preflight_command(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .Config.Labels}}\t{{.State.Running}}\t{{json .Config.Env}}",
            *container_ids,
        ],
        deadline=deadline,
    )
    if code != 0:
        raise CollectorConfigurationError(
            f"could not inspect selected Compose project: {stderr.strip()}"
        )

    observed_services: set[str] = set()
    running_services: set[str] = set()
    observed_hashes: dict[str, str] = {}
    container_environments: list[dict[str, str]] = []
    records = stdout.splitlines()
    if len(records) != len(container_ids):
        raise CollectorConfigurationError(
            "Docker returned an incomplete Compose project inspection"
        )
    for record in records:
        parts = record.split("\t", 2)
        if len(parts) != 3:
            raise CollectorConfigurationError(
                "Docker returned an invalid Compose project inspection"
            )
        raw_labels, raw_running, raw_environment = parts
        try:
            payload: object = json.loads(raw_labels)
            environment_payload: object = json.loads(raw_environment)
        except json.JSONDecodeError as exc:
            raise CollectorConfigurationError(
                "Docker returned invalid Compose project metadata"
            ) from exc
        if not isinstance(payload, dict):
            raise CollectorConfigurationError(
                "Docker returned non-object Compose project labels"
            )
        if not isinstance(environment_payload, list) or not all(
            isinstance(value, str) for value in environment_payload
        ):
            raise CollectorConfigurationError(
                "Docker returned an invalid Compose container environment"
            )
        environment = {
            key: value
            for item in cast(list[str], environment_payload)
            for key, separator, value in (item.partition("="),)
            if separator
        }
        container_environments.append(environment)
        labels = cast(dict[str, object], payload)
        project = labels.get("com.docker.compose.project")
        config_files = labels.get("com.docker.compose.project.config_files")
        working_dir = labels.get("com.docker.compose.project.working_dir")
        service = labels.get("com.docker.compose.service")
        config_hash = labels.get("com.docker.compose.config-hash")
        if (
            project != compose_project
            or config_files != expected_files
            or working_dir != str(repo_root.resolve())
            or not isinstance(service, str)
            or not isinstance(config_hash, str)
        ):
            raise CollectorConfigurationError(
                "supplied ordered Compose files do not match the deployed project"
            )
        observed_services.add(service)
        observed_hashes[service] = config_hash
        if raw_running == "true":
            running_services.add(service)

    deployed_public_urls = _deployed_compose_public_urls(container_environments)
    code, stdout, stderr = _run_preflight_command(
        [*args, "config", "--hash", "*"],
        deadline=deadline,
        env=_cluster_command_env(
            deployed_public_urls.api,
            public_app_url=deployed_public_urls.app,
        ),
    )
    if code != 0:
        raise CollectorConfigurationError(
            f"cluster wrapper could not hash Compose configuration: {stderr.strip()}"
        )
    expected_hashes = _parse_compose_service_hashes(stdout)
    for service, config_hash in observed_hashes.items():
        if expected_hashes.get(service) != config_hash:
            raise CollectorConfigurationError(
                f"rendered Compose configuration for {service} does not match "
                "the deployed container"
            )
    if observed_services != expected_hashes.keys():
        raise CollectorConfigurationError(
            "rendered Compose services do not match the deployed project"
        )
    stopped_required_services = REQUIRED_LOAD_TEST_SERVICES - running_services
    if stopped_required_services:
        raise CollectorConfigurationError(
            "selected Compose project has stopped required services: "
            f"{', '.join(sorted(stopped_required_services))}"
        )
    return deployed_public_urls


def resolve_cluster_ports(
    repo_root: Path,
    cluster_num: int,
    compose_files: tuple[str, ...],
    ee_multi_tenant: bool,
    *,
    deadline: float | None = None,
) -> ClusterPorts:
    """Resolve API, PostgreSQL, and Temporal endpoints for one numbered cluster."""
    args = _cluster_wrapper_prefix(
        repo_root, cluster_num, compose_files, ee_multi_tenant
    )
    code, stdout, stderr = _run_preflight_command(
        [*args, "ports"],
        deadline=deadline,
    )
    if code != 0:
        raise CollectorConfigurationError(
            f"cluster wrapper could not resolve service endpoints: {stderr.strip()}"
        )

    endpoints: dict[str, tuple[str, int]] = {}
    metrics_urls: dict[str, str] = {}
    public_api_url: str | None = None
    for line in stdout.splitlines():
        api_match = re.match(r"^\s*API:\s+(\S+)(?:\s+\(internal:.*\))?\s*$", line)
        if api_match:
            public_api_url = api_match.group(1)
            continue
        metrics_match = re.match(
            r"^\s*(Worker metrics|Executor metrics|API DB pool metrics|"
            r"Worker DB pool metrics|Executor DB pool metrics|PgDog metrics):"
            r"\s+(\S+)\s*$",
            line,
        )
        if metrics_match:
            metrics_urls[metrics_match.group(1)] = metrics_match.group(2)
            continue
        match = re.match(r"^\s*(PostgreSQL|Temporal):\s+(\S+):(\d+)\s*$", line)
        if match:
            endpoints[match.group(1)] = (
                match.group(2).lower().rstrip("."),
                int(match.group(3)),
            )
    if (
        public_api_url is None
        or "PostgreSQL" not in endpoints
        or "Temporal" not in endpoints
        or "Worker metrics" not in metrics_urls
        or "Executor metrics" not in metrics_urls
        or "API DB pool metrics" not in metrics_urls
        or "Worker DB pool metrics" not in metrics_urls
        or "Executor DB pool metrics" not in metrics_urls
        or "PgDog metrics" not in metrics_urls
    ):
        raise CollectorConfigurationError(
            "cluster wrapper did not report API, PostgreSQL, Temporal, and SDK "
            "metrics endpoints"
        )
    postgres_host, postgres_port = endpoints["PostgreSQL"]
    temporal_host, temporal_port = endpoints["Temporal"]
    return ClusterPorts(
        public_api_url=public_api_url,
        postgres_host=postgres_host,
        postgres_port=postgres_port,
        temporal_host=temporal_host,
        temporal_port=temporal_port,
        temporal_worker_metrics_url=metrics_urls["Worker metrics"],
        temporal_executor_metrics_url=metrics_urls["Executor metrics"],
        api_db_pool_metrics_url=metrics_urls["API DB pool metrics"],
        worker_db_pool_metrics_url=metrics_urls["Worker DB pool metrics"],
        executor_db_pool_metrics_url=metrics_urls["Executor DB pool metrics"],
        pgdog_metrics_url=metrics_urls["PgDog metrics"],
    )


def _hosts_match(left: str, right: str) -> bool:
    normalized_left = left.lower().rstrip(".")
    normalized_right = right.lower().rstrip(".")
    return normalized_left == normalized_right or (
        normalized_left in LOCAL_CLUSTER_HOSTS
        and normalized_right in LOCAL_CLUSTER_HOSTS
    )


def _parse_public_api_url(value: str, *, label: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise CollectorConfigurationError(f"{label} has an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CollectorConfigurationError(
            f"{label} must be an http:// or https:// URL without credentials, "
            "query parameters, or a fragment"
        )
    path = parsed.path.rstrip("/")
    if path != "/api":
        raise CollectorConfigurationError(f"{label} must end with /api")
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname, port or default_port, path


def validate_public_api_url(public_api_url: str, cluster_ports: ClusterPorts) -> None:
    """Require the runner and collector API URL to target the selected cluster."""

    requested = _parse_public_api_url(public_api_url, label="public API URL")
    expected = _parse_public_api_url(
        cluster_ports.public_api_url,
        label="cluster wrapper public API URL",
    )
    if (
        requested[0] != expected[0]
        or not _hosts_match(requested[1], expected[1])
        or requested[2:] != expected[2:]
    ):
        raise CollectorConfigurationError(
            "public API URL does not match the selected cluster's endpoint "
            f"{cluster_ports.public_api_url}"
        )


def _parse_metrics_url(
    value: str,
    *,
    label: str,
    expected_path: str = "/metrics",
) -> tuple[str, str, int, str]:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise CollectorConfigurationError(f"{label} has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
    ):
        raise CollectorConfigurationError(
            f"{label} must be an http://host:port{expected_path} URL without "
            "credentials, "
            "a query, or a fragment"
        )
    return parsed.scheme, parsed.hostname, port, parsed.path


def validate_temporal_sdk_metrics_urls(
    worker_url: str,
    executor_urls: tuple[str, ...],
    cluster_ports: ClusterPorts,
) -> None:
    """Require SDK scrapes to target the selected numbered cluster."""
    if not executor_urls:
        raise CollectorConfigurationError(
            "at least one executor metrics URL is required"
        )
    if len(executor_urls) > MAX_LOADTEST_EXECUTOR_REPLICAS:
        raise CollectorConfigurationError(
            "executor metrics URL count exceeds the supported replica limit"
        )
    if len(set(executor_urls)) != len(executor_urls):
        raise CollectorConfigurationError("executor metrics URLs must be unique")

    requested_worker = _parse_metrics_url(
        worker_url,
        label="worker metrics URL",
    )
    expected_worker = _parse_metrics_url(
        cluster_ports.temporal_worker_metrics_url,
        label="cluster wrapper worker metrics URL",
    )
    if (
        requested_worker[0] != expected_worker[0]
        or not _hosts_match(requested_worker[1], expected_worker[1])
        or requested_worker[2:] != expected_worker[2:]
    ):
        raise CollectorConfigurationError(
            "worker metrics URL does not match the selected cluster endpoint"
        )

    expected_executor = _parse_metrics_url(
        cluster_ports.temporal_executor_metrics_url,
        label="cluster wrapper executor metrics URL",
    )
    for executor_index, executor_url in enumerate(executor_urls, start=1):
        requested_executor = _parse_metrics_url(
            executor_url,
            label=f"executor metrics URL {executor_index}",
        )
        if (
            requested_executor[0] != expected_executor[0]
            or not _hosts_match(requested_executor[1], expected_executor[1])
            or requested_executor[3] != expected_executor[3]
            or not (
                expected_executor[2]
                <= requested_executor[2]
                < expected_executor[2] + MAX_LOADTEST_EXECUTOR_REPLICAS
            )
        ):
            raise CollectorConfigurationError(
                "executor metrics URL does not match the selected cluster endpoint"
            )


def validate_db_pool_metrics_urls(
    api_url: str,
    worker_url: str,
    executor_urls: tuple[str, ...],
    cluster_ports: ClusterPorts,
) -> None:
    """Require SQLAlchemy pool scrapes to target every selected service replica."""
    if not executor_urls:
        raise CollectorConfigurationError(
            "at least one executor DB pool metrics URL is required"
        )
    if len(executor_urls) > MAX_LOADTEST_EXECUTOR_REPLICAS:
        raise CollectorConfigurationError(
            "executor DB pool metrics URL count exceeds the supported replica limit"
        )
    if len(set(executor_urls)) != len(executor_urls):
        raise CollectorConfigurationError(
            "executor DB pool metrics URLs must be unique"
        )

    exact_pairs = (
        (api_url, cluster_ports.api_db_pool_metrics_url, "API"),
        (worker_url, cluster_ports.worker_db_pool_metrics_url, "worker"),
    )
    for requested_url, expected_url, label in exact_pairs:
        requested = _parse_metrics_url(
            requested_url,
            label=f"{label} DB pool metrics URL",
            expected_path="/db-pool-metrics",
        )
        expected = _parse_metrics_url(
            expected_url,
            label=f"cluster wrapper {label} DB pool metrics URL",
            expected_path="/db-pool-metrics",
        )
        if (
            requested[0] != expected[0]
            or not _hosts_match(requested[1], expected[1])
            or requested[2:] != expected[2:]
        ):
            raise CollectorConfigurationError(
                f"{label} DB pool metrics URL does not match the selected cluster"
            )

    expected_executor = _parse_metrics_url(
        cluster_ports.executor_db_pool_metrics_url,
        label="cluster wrapper executor DB pool metrics URL",
        expected_path="/db-pool-metrics",
    )
    for executor_index, executor_url in enumerate(executor_urls, start=1):
        requested_executor = _parse_metrics_url(
            executor_url,
            label=f"executor DB pool metrics URL {executor_index}",
            expected_path="/db-pool-metrics",
        )
        if (
            requested_executor[0] != expected_executor[0]
            or not _hosts_match(requested_executor[1], expected_executor[1])
            or requested_executor[3] != expected_executor[3]
            or not (
                expected_executor[2]
                <= requested_executor[2]
                < expected_executor[2] + MAX_LOADTEST_EXECUTOR_REPLICAS
            )
        ):
            raise CollectorConfigurationError(
                "executor DB pool metrics URL does not match the selected cluster"
            )


def validate_pgdog_metrics_url(url: str, cluster_ports: ClusterPorts) -> None:
    """Require the optional PgDog scrape to target the selected cluster."""
    requested = _parse_metrics_url(url, label="PgDog metrics URL")
    expected = _parse_metrics_url(
        cluster_ports.pgdog_metrics_url,
        label="cluster wrapper PgDog metrics URL",
    )
    if (
        requested[0] != expected[0]
        or not _hosts_match(requested[1], expected[1])
        or requested[2:] != expected[2:]
    ):
        raise CollectorConfigurationError(
            "PgDog metrics URL does not match the selected cluster"
        )


def validate_monitor_dsn_target(dsn: str, cluster_ports: ClusterPorts) -> None:
    """Require the monitoring DSN to target the selected cluster's PostgreSQL."""

    parsed = urlsplit(dsn)
    try:
        dsn_port = parsed.port
    except ValueError as exc:
        raise CollectorConfigurationError(
            "monitoring DSN has an invalid PostgreSQL port"
        ) from exc
    dsn_host = parsed.hostname
    if parsed.scheme not in {"postgres", "postgresql"} or dsn_host is None:
        raise CollectorConfigurationError(
            "monitoring DSN must be a postgres:// or postgresql:// URI with a host"
        )
    if (
        not _hosts_match(dsn_host, cluster_ports.postgres_host)
        or (dsn_port or 5432) != cluster_ports.postgres_port
    ):
        raise CollectorConfigurationError(
            "monitoring DSN endpoint does not match the selected cluster's "
            "PostgreSQL endpoint "
            f"{cluster_ports.postgres_host}:{cluster_ports.postgres_port}"
        )


def _compose_service_environment(
    rendered_config: str, service_name: str
) -> dict[str, str]:
    """Extract one service's normalized environment from Compose config output."""
    try:
        payload: object = yaml.safe_load(rendered_config)
    except yaml.YAMLError as exc:
        raise CollectorConfigurationError(
            "cluster wrapper returned invalid Compose configuration"
        ) from exc
    if not isinstance(payload, dict):
        raise CollectorConfigurationError(
            "cluster wrapper returned a non-object Compose configuration"
        )
    root = cast(dict[str, object], payload)
    services_value = root.get("services")
    if not isinstance(services_value, dict):
        raise CollectorConfigurationError(
            "cluster wrapper Compose configuration omitted services"
        )
    services = cast(dict[str, object], services_value)
    service_value = services.get(service_name)
    if not isinstance(service_value, dict):
        raise CollectorConfigurationError(
            f"cluster wrapper Compose configuration omitted {service_name}"
        )
    service = cast(dict[str, object], service_value)
    environment_value = service.get("environment")
    if not isinstance(environment_value, dict):
        raise CollectorConfigurationError(
            f"cluster wrapper Compose configuration omitted {service_name} environment"
        )
    environment = cast(dict[str, object], environment_value)
    return {key: value for key, value in environment.items() if isinstance(value, str)}


def validate_temporal_context(
    temporal_target: str,
    temporal_namespace: str,
    workflow_task_queues: tuple[str, ...],
    activity_task_queues: tuple[str, ...],
    cluster_ports: ClusterPorts,
    repo_root: Path,
    cluster_num: int,
    compose_files: tuple[str, ...],
    ee_multi_tenant: bool,
    public_api_url: str,
    *,
    deadline: float | None = None,
) -> None:
    """Bind Temporal sampling to the selected cluster and its configured queues."""
    if "://" in temporal_target:
        raise CollectorConfigurationError(
            "Temporal target must be a host:port without a URL scheme"
        )
    parsed_target = urlsplit(f"//{temporal_target}")
    try:
        target_port = parsed_target.port
    except ValueError as exc:
        raise CollectorConfigurationError(
            "Temporal target has an invalid port"
        ) from exc
    target_host = parsed_target.hostname
    if (
        target_host is None
        or target_port is None
        or parsed_target.username is not None
        or parsed_target.password is not None
        or parsed_target.path not in {"", "/"}
    ):
        raise CollectorConfigurationError("Temporal target must be a host:port")
    if (
        not _hosts_match(target_host, cluster_ports.temporal_host)
        or target_port != cluster_ports.temporal_port
    ):
        raise CollectorConfigurationError(
            "Temporal target does not match the selected cluster's endpoint "
            f"{cluster_ports.temporal_host}:{cluster_ports.temporal_port}"
        )

    args = _cluster_wrapper_prefix(
        repo_root, cluster_num, compose_files, ee_multi_tenant
    )
    code, stdout, stderr = _run_preflight_command(
        [*args, "config"],
        deadline=deadline,
        env=_cluster_command_env(public_api_url),
    )
    if code != 0 or not stdout.strip():
        raise CollectorConfigurationError(
            f"cluster wrapper could not resolve Temporal context: {stderr.strip()}"
        )
    worker_environment = _compose_service_environment(stdout, "worker")
    executor_environment = _compose_service_environment(stdout, "executor")
    expected_namespace = worker_environment.get("TEMPORAL__CLUSTER_NAMESPACE")
    expected_executor_namespace = executor_environment.get(
        "TEMPORAL__CLUSTER_NAMESPACE"
    )
    expected_workflow_queue = worker_environment.get("TEMPORAL__CLUSTER_QUEUE")
    expected_executor_activity_queue = executor_environment.get(
        "TRACECAT__EXECUTOR_QUEUE"
    )
    if (
        expected_namespace is None
        or expected_executor_namespace != expected_namespace
        or expected_workflow_queue is None
        or expected_executor_activity_queue is None
    ):
        raise CollectorConfigurationError(
            "selected Compose configuration has an incomplete Temporal context"
        )
    if temporal_namespace != expected_namespace:
        raise CollectorConfigurationError(
            "Temporal namespace does not match the selected Compose configuration"
        )
    if set(workflow_task_queues) != {expected_workflow_queue}:
        raise CollectorConfigurationError(
            "Temporal workflow task queues do not match the selected Compose "
            "configuration"
        )
    if set(activity_task_queues) != {
        expected_workflow_queue,
        expected_executor_activity_queue,
    }:
        raise CollectorConfigurationError(
            "Temporal activity task queues do not match the selected Compose "
            "configuration"
        )


def capture_compose_config(
    config: CollectorConfig, artifact_dir: Path, repo_root: Path
) -> str:
    """Write the wrapper-resolved Compose model for the cluster under test."""
    args = _cluster_wrapper_prefix(
        repo_root,
        config.cluster_num,
        config.compose_files,
        config.ee_multi_tenant,
    )
    code, stdout, _ = _run_command(
        [*args, "config"],
        env=_cluster_command_env(
            config.compose_public_api_url,
            public_app_url=config.compose_public_app_url,
        ),
    )
    target = artifact_dir / "compose_config.yml"
    if code != 0 or not stdout.strip():
        target.write_text("# capture failed\n", encoding="utf-8")
        raise ComposeConfigCaptureError(
            f"cluster wrapper could not capture Compose configuration: exit code {code}"
        )
    try:
        redacted_config = _redact_compose_config(stdout, repo_root=repo_root)
    except (ValueError, yaml.YAMLError) as exc:
        target.write_text("# capture failed\n", encoding="utf-8")
        raise ComposeConfigCaptureError(
            "cluster wrapper returned invalid Compose configuration"
        ) from exc
    target.write_text(redacted_config, encoding="utf-8")
    return str(target)


def capture_container_state(config: CollectorConfig, artifact_dir: Path) -> str:
    """Record effective limits plus restart/OOM state and image identifiers."""
    code, stdout, _ = _run_command(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={config.compose_project}",
            "--format",
            "{{.ID}}",
        ]
    )
    if code != 0:
        raise ContainerStateCaptureError(
            f"docker ps could not inspect project: exit code {code}"
        )
    container_ids = [line for line in stdout.splitlines() if line.strip()]
    if not container_ids:
        raise ContainerStateCaptureError(
            "docker ps found no containers for the selected project"
        )

    states: list[ContainerState] = []
    for container_id in container_ids:
        inspect_code, inspect_out, _ = _run_command(["docker", "inspect", container_id])
        if inspect_code != 0:
            raise ContainerStateCaptureError(
                f"docker inspect failed for a selected container: "
                f"exit code {inspect_code}"
            )
        try:
            parsed = json.loads(inspect_out)
            if not isinstance(parsed, list) or not parsed:
                raise ValueError("docker inspect returned no container object")
            info = parsed[0]
            if not isinstance(info, dict):
                raise TypeError("docker inspect returned a non-object")
            state = info.get("State", {})
            host_config = info.get("HostConfig", {})
            labels = info.get("Config", {}).get("Labels", {})
            service = str(labels.get("com.docker.compose.service", ""))
            if not COMPOSE_SERVICE_RE.fullmatch(service):
                raise ValueError("invalid Compose service label")
            states.append(
                ContainerState(
                    service=service,
                    image_id=str(info.get("Image", "")),
                    status=str(state.get("Status", "")),
                    restart_count=int(info.get("RestartCount", 0)),
                    oom_killed=bool(state.get("OOMKilled", False)),
                    exit_code=state.get("ExitCode"),
                    nano_cpus=host_config.get("NanoCpus"),
                    memory_limit_bytes=host_config.get("Memory"),
                    memory_swap_limit_bytes=host_config.get("MemorySwap"),
                    pids_limit=host_config.get("PidsLimit"),
                )
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ContainerStateCaptureError(
                "docker inspect returned invalid container state"
            ) from exc
    target = artifact_dir / "containers.json"
    target.write_text(json.dumps(states, indent=2, default=str), encoding="utf-8")
    return str(target)


def _service_log_summary(
    service: str,
    raw_log: str,
    *,
    since: str,
) -> ServiceLogSummary:
    """Reduce a raw log window to fixed counts, retaining none of its text."""
    lines = raw_log.splitlines()

    def count_matches(pattern: re.Pattern[str]) -> int:
        return sum(pattern.search(line) is not None for line in lines)

    return ServiceLogSummary(
        service=service,
        since=since,
        lines_scanned=len(lines),
        signal_counts=ServiceLogSignalCounts(
            postgres_connection_limit=count_matches(POSTGRES_CONNECTION_LIMIT_RE),
            database_pool_timeout=count_matches(DATABASE_POOL_TIMEOUT_RE),
            statement_timeout=count_matches(STATEMENT_TIMEOUT_RE),
            lock_timeout=count_matches(LOCK_TIMEOUT_RE),
            deadlock=count_matches(DEADLOCK_RE),
            serialization_failure=count_matches(SERIALIZATION_FAILURE_RE),
            connection_refused=count_matches(CONNECTION_REFUSED_RE),
            connection_reset=count_matches(CONNECTION_RESET_RE),
            timeout=count_matches(TIMEOUT_RE),
            http_5xx=count_matches(HTTP_5XX_RE),
        ),
    )


def capture_service_logs(
    config: CollectorConfig,
    artifact_dir: Path,
    repo_root: Path,
    *,
    since: str,
) -> dict[str, str]:
    """Write aggregate diagnostics from logs emitted during this collector run."""
    log_dir = artifact_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for service in config.log_services:
        if COMPOSE_SERVICE_RE.fullmatch(service) is None:
            raise ServiceLogCaptureError("invalid Compose service name")
        args = _cluster_wrapper_prefix(
            repo_root,
            config.cluster_num,
            config.compose_files,
            config.ee_multi_tenant,
        )
        code, stdout, _ = _run_command(
            [*args, "logs", "--no-color", "--since", since, service],
            env=_cluster_command_env(config.public_api_url),
        )
        target = log_dir / f"{service}.json"
        if code != 0:
            raise ServiceLogCaptureError(
                f"cluster wrapper could not capture {service} logs: exit code {code}"
            )
        target.write_text(
            json.dumps(
                _service_log_summary(service, stdout, since=since),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        written[service] = str(target)
    return written


def _resolve_log_services(extras: list[str] | None) -> tuple[str, ...]:
    """Retain required diagnostic services and append validated extras."""
    services: list[str] = list(DEFAULT_LOG_SERVICES)
    seen: set[str] = set(services)
    for service in extras or ():
        if COMPOSE_SERVICE_RE.fullmatch(service) is None:
            raise CollectorConfigurationError(
                f"invalid Compose service name: {service!r}"
            )
        if service not in seen:
            services.append(service)
            seen.add(service)
    return tuple(services)


def capture_commit(repo_root: Path) -> tuple[str, bool]:
    code, stdout, _ = _run_command(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
    commit = stdout.strip()
    if code != 0 or not commit:
        raise CommitCaptureError(
            f"git could not capture the tested commit: exit code {code}"
        )
    status_code, status_out, _ = _run_command(
        ["git", "-C", str(repo_root), "status", "--porcelain"]
    )
    if status_code != 0:
        raise CommitCaptureError(
            f"git could not capture the worktree state: exit code {status_code}"
        )
    dirty = bool(status_out.strip())
    return commit, dirty


class MetricCollector:
    """Owns the sampling loop and the one-shot environment captures."""

    def __init__(
        self,
        config: CollectorConfig,
        sampler: PgSampler | None = None,
        temporal_sampler: TemporalSampler | None = None,
        resource_sampler: DockerResourceSampler | None = None,
        sdk_metrics_capture: TemporalSdkMetricsCapture | None = None,
        db_pool_metrics_sampler: DbPoolMetricsSampler | None = None,
        pgdog_metrics_sampler: PgDogMetricsSampler | None = None,
    ) -> None:
        self._config = config
        self._artifact_dir = Path(config.artifact_dir)
        self._sampler = sampler or PgSampler(config.dsn, config.settings_of_interest)
        self._temporal_sampler = temporal_sampler or TemporalSampler(
            config.temporal_target,
            config.temporal_namespace,
            config.temporal_workflow_task_queues,
            config.temporal_activity_task_queues,
        )
        self._resource_sampler = resource_sampler or DockerResourceSampler(
            config.compose_project
        )
        self._sdk_metrics_capture = sdk_metrics_capture or TemporalSdkMetricsCapture(
            config.temporal_sdk_metrics_endpoints
        )
        self._db_pool_metrics_sampler = db_pool_metrics_sampler
        if self._db_pool_metrics_sampler is None and config.db_pool_metrics_endpoints:
            self._db_pool_metrics_sampler = DbPoolMetricsSampler(
                config.db_pool_metrics_endpoints
            )
        self._pgdog_metrics_sampler = pgdog_metrics_sampler
        if self._pgdog_metrics_sampler is None and config.pgdog_metrics_url is not None:
            self._pgdog_metrics_sampler = PgDogMetricsSampler(config.pgdog_metrics_url)
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._pg_ready = asyncio.Event()
        self._temporal_ready = asyncio.Event()
        self._resource_ready = asyncio.Event()
        self._db_pool_metrics_ready = asyncio.Event()
        self._pgdog_metrics_ready = asyncio.Event()
        self._sample_count = 0
        self._temporal_sample_count = 0
        self._resource_sample_count = 0
        self._db_pool_metrics_sample_count = 0
        self._pgdog_metrics_sample_count = 0
        self._pending_sampling_failures: dict[str, str] = {}
        self._runner_status: Literal["completed", "aborted"] | None = None
        self._external_stop_requested = False

    def request_stop(self) -> None:
        self._external_stop_requested = True
        self._stop.set()

    def _claim_artifact_directory(self) -> Path:
        return _claim_artifact_directory(
            self._artifact_dir,
            self._config.run_id,
        )

    def _write_sampling_failure(
        self,
        handle: TextIO,
        failure: BaseException,
        *,
        signal_name: str,
    ) -> None:
        """Persist a gap and keep it pending until that signal samples again."""
        failure_name = type(failure).__name__
        self._pending_sampling_failures[signal_name] = failure_name
        handle.write(
            json.dumps(
                SamplerErrorGap(
                    sampled_at=_utc_now_iso(),
                    sampling_gap="sampler_error",
                    signal=signal_name,
                    error_type=failure_name,
                )
            )
            + "\n"
        )
        handle.flush()

    def _record_sampling_success(self, signal_name: str) -> None:
        """Clear a transient sampler failure after the endpoint recovers."""
        self._pending_sampling_failures.pop(signal_name, None)

    async def _wait_for_next_sample(
        self,
        tick: float,
        interval_seconds: float,
    ) -> bool:
        if self._stop.is_set():
            return False
        now = time.monotonic()
        delay = max(0.0, interval_seconds - (now - tick))
        if delay > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
        return not self._stop.is_set()

    def _record_cadence_failure(
        self,
        handle: TextIO,
        *,
        signal_name: str,
        tick: float,
        interval_seconds: float,
    ) -> None:
        elapsed = time.monotonic() - tick
        if elapsed > interval_seconds:
            # A delayed gauge sample reduces local time-series resolution, but
            # it is not signal loss: the successful observation is retained
            # immediately before this structured gap. Endpoint/query failures
            # are fatal only when that signal does not recover before shutdown.
            gap = CadenceSamplingGap(
                sampled_at=_utc_now_iso(),
                sampling_gap="cadence_delayed",
                signal=signal_name,
                elapsed_seconds=elapsed,
                target_interval_seconds=interval_seconds,
            )
            handle.write(json.dumps(gap) + "\n")
            handle.flush()

    async def _pg_sample_loop(self, sink: Path) -> None:
        interval = self._config.sample_interval_seconds
        with sink.open("x", encoding="utf-8") as handle:
            while not self._stop.is_set():
                tick = time.monotonic()
                try:
                    async with asyncio.timeout(SAMPLER_OPERATION_TIMEOUT_SECONDS):
                        sample = await self._sampler.sample()
                except (
                    asyncpg.PostgresError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                ) as exc:
                    self._write_sampling_failure(
                        handle,
                        exc,
                        signal_name="PostgreSQL",
                    )
                else:
                    self._record_sampling_success("PostgreSQL")
                    handle.write(json.dumps(sample) + "\n")
                    handle.flush()
                    self._sample_count += 1
                    self._pg_ready.set()
                    self._record_cadence_failure(
                        handle,
                        signal_name="PostgreSQL",
                        tick=tick,
                        interval_seconds=interval,
                    )
                if not await self._wait_for_next_sample(tick, interval):
                    return

    async def _temporal_sample_loop(self, sink: Path) -> None:
        interval = self._config.sample_interval_seconds
        with sink.open("x", encoding="utf-8") as handle:
            while not self._stop.is_set():
                tick = time.monotonic()
                try:
                    async with asyncio.timeout(SAMPLER_OPERATION_TIMEOUT_SECONDS):
                        sample = await self._temporal_sampler.sample()
                except (OSError, RPCError, RuntimeError, TimeoutError) as exc:
                    self._write_sampling_failure(
                        handle,
                        exc,
                        signal_name="Temporal",
                    )
                else:
                    self._record_sampling_success("Temporal")
                    handle.write(json.dumps(sample) + "\n")
                    handle.flush()
                    self._temporal_sample_count += 1
                    self._temporal_ready.set()
                    self._record_cadence_failure(
                        handle,
                        signal_name="Temporal",
                        tick=tick,
                        interval_seconds=interval,
                    )
                if not await self._wait_for_next_sample(tick, interval):
                    return

    async def _resource_sample_loop(self, sink: Path) -> None:
        with sink.open("x", encoding="utf-8") as handle:
            while not self._stop.is_set():
                tick = time.monotonic()
                try:
                    async with asyncio.timeout(SAMPLER_OPERATION_TIMEOUT_SECONDS):
                        sample = await self._resource_sampler.sample()
                except (
                    OSError,
                    ResourceUsageCaptureError,
                    RuntimeError,
                    TimeoutError,
                ) as exc:
                    self._write_sampling_failure(
                        handle,
                        exc,
                        signal_name="Resource",
                    )
                else:
                    self._record_sampling_success("Resource")
                    handle.write(json.dumps(sample) + "\n")
                    handle.flush()
                    self._resource_sample_count += 1
                    self._resource_ready.set()
                    self._record_cadence_failure(
                        handle,
                        signal_name="Resource",
                        tick=tick,
                        interval_seconds=RESOURCE_SAMPLE_INTERVAL_SECONDS,
                    )
                if not await self._wait_for_next_sample(
                    tick,
                    RESOURCE_SAMPLE_INTERVAL_SECONDS,
                ):
                    return

    async def _db_pool_metrics_sample_loop(self, sink: Path) -> None:
        sampler = self._db_pool_metrics_sampler
        if sampler is None:
            return
        # These process-local counters need one-second resolution, not the
        # sub-second PostgreSQL cadence some smoke rows request. Scraping every
        # service replica more often adds avoidable HTTP and JSON work to the
        # system under test.
        interval = max(self._config.sample_interval_seconds, 1.0)
        with sink.open("x", encoding="utf-8") as handle:
            while not self._stop.is_set():
                tick = time.monotonic()
                try:
                    async with asyncio.timeout(SAMPLER_OPERATION_TIMEOUT_SECONDS):
                        sample = await sampler.sample()
                except (
                    DbPoolMetricsCaptureError,
                    httpx.HTTPError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                ) as exc:
                    self._write_sampling_failure(
                        handle,
                        exc,
                        signal_name="DB pool metrics",
                    )
                else:
                    self._record_sampling_success("DB pool metrics")
                    handle.write(json.dumps(sample) + "\n")
                    handle.flush()
                    self._db_pool_metrics_sample_count += 1
                    self._db_pool_metrics_ready.set()
                    # These are cumulative counters, histograms, and
                    # high-water marks. A delayed scrape retains every event,
                    # so timestamp jitter must not invalidate an otherwise
                    # complete run.
                if not await self._wait_for_next_sample(tick, interval):
                    return

    async def _pgdog_metrics_sample_loop(self, sink: Path) -> None:
        sampler = self._pgdog_metrics_sampler
        if sampler is None:
            return
        interval = max(self._config.sample_interval_seconds, 1.0)
        with sink.open("x", encoding="utf-8") as handle:
            while not self._stop.is_set():
                tick = time.monotonic()
                try:
                    async with asyncio.timeout(SAMPLER_OPERATION_TIMEOUT_SECONDS):
                        sample = await sampler.sample()
                except (
                    PgDogMetricsCaptureError,
                    httpx.HTTPError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                ) as exc:
                    self._write_sampling_failure(
                        handle,
                        exc,
                        signal_name="PgDog metrics",
                    )
                else:
                    self._record_sampling_success("PgDog metrics")
                    handle.write(json.dumps(sample) + "\n")
                    handle.flush()
                    self._pgdog_metrics_sample_count += 1
                    self._pgdog_metrics_ready.set()
                    self._record_cadence_failure(
                        handle,
                        signal_name="PgDog metrics",
                        tick=tick,
                        interval_seconds=interval,
                    )
                if not await self._wait_for_next_sample(tick, interval):
                    return

    async def _connection_probe_loop(self) -> None:
        while not self._stop.is_set():
            tick = time.monotonic()
            await self._sampler.probe_connection_slots()
            if not await self._wait_for_next_sample(tick, 5.0):
                return

    async def _watch_runner_completion(self) -> None:
        marker_path = self._artifact_dir / RUNNER_COMPLETE_FILENAME
        while not self._stop.is_set():
            if marker_path.is_file():
                try:
                    payload = json.loads(marker_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RunnerLifecycleError(
                        f"invalid runner completion marker: {marker_path}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise RunnerLifecycleError(
                        "runner completion marker must contain a JSON object"
                    )
                marker = cast(RunnerComplete, payload)
                if marker.get("run_id") != run_id_fingerprint(self._config.run_id):
                    raise RunnerLifecycleError(
                        "runner completion marker does not match collector run ID"
                    )
                marker_status = marker.get("status")
                if marker_status not in {"completed", "aborted"}:
                    raise RunnerLifecycleError(
                        "runner completion marker has an invalid status"
                    )
                if not isinstance(marker.get("completed_at"), str):
                    raise RunnerLifecycleError(
                        "runner completion marker is missing its completion time"
                    )
                self._runner_status = marker_status
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._config.recovery_seconds,
                    )
                self._stop.set()
                return

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=0.1)

    async def _watch_measurement_boundary(self) -> None:
        """Snapshot SDK metrics immediately before and after measured load."""
        if self._config.execution_id_handoff_path is None:
            return
        expected_run_id = run_id_fingerprint(self._config.run_id)

        async def wait_for_marker(filename: str) -> None:
            marker_path = self._artifact_dir / filename
            while not self._stop.is_set():
                if marker_path.is_file():
                    try:
                        payload: object = json.loads(
                            marker_path.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError) as exc:
                        raise RunnerLifecycleError(
                            "runner measurement-boundary marker is invalid"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise RunnerLifecycleError(
                            "runner measurement-boundary marker must be an object"
                        )
                    marker = cast(MeasurementBoundary, payload)
                    if (
                        marker.get("run_id") != expected_run_id
                        or marker.get("status") != "ready"
                        or not isinstance(marker.get("recorded_at"), str)
                    ):
                        raise RunnerLifecycleError(
                            "runner measurement-boundary marker does not match this run"
                        )
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=0.05)
            raise RunnerLifecycleError(
                "runner completed before publishing a measurement boundary"
            )

        def acknowledge(filename: str) -> None:
            acknowledgement = MeasurementBoundary(
                run_id=expected_run_id,
                status="ready",
                recorded_at=_utc_now_iso(),
            )
            (self._artifact_dir / filename).write_text(
                json.dumps(acknowledgement, indent=2) + "\n",
                encoding="utf-8",
            )

        await wait_for_marker(RUNNER_MEASUREMENT_READY_FILENAME)
        await self._sdk_metrics_capture.capture_baseline()
        acknowledge(COLLECTOR_MEASUREMENT_READY_FILENAME)

        await wait_for_marker(RUNNER_MEASUREMENT_COMPLETE_FILENAME)
        await self._sdk_metrics_capture.capture_final()
        acknowledge(COLLECTOR_MEASUREMENT_COMPLETE_FILENAME)

    async def _run_sampling_loops(
        self,
        pg_sink: Path,
        temporal_sink: Path,
        resource_sink: Path,
    ) -> None:
        db_pool_metrics_sink = resource_sink.with_name("db_pool_metrics.jsonl")
        pgdog_metrics_sink = resource_sink.with_name("pgdog_metrics.jsonl")
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._pg_sample_loop(pg_sink))
            tasks.create_task(self._temporal_sample_loop(temporal_sink))
            tasks.create_task(self._resource_sample_loop(resource_sink))
            if self._db_pool_metrics_sampler is not None:
                tasks.create_task(
                    self._db_pool_metrics_sample_loop(db_pool_metrics_sink)
                )
            if self._pgdog_metrics_sampler is not None:
                tasks.create_task(self._pgdog_metrics_sample_loop(pgdog_metrics_sink))
            tasks.create_task(self._connection_probe_loop())
            tasks.create_task(self._watch_runner_completion())
            if self._config.execution_id_handoff_path is not None:
                tasks.create_task(self._watch_measurement_boundary())

    async def _connect_samplers(self) -> list[dict[str, str | None]]:
        """Connect every sampler and capture settings within startup's deadline."""
        await self._sampler.connect()
        await self._temporal_sampler.connect()
        await self._resource_sampler.connect()
        await self._sdk_metrics_capture.validate(
            timeout_seconds=self._config.readiness_timeout_seconds
        )
        if self._db_pool_metrics_sampler is not None:
            await self._db_pool_metrics_sampler.connect()
        if self._pgdog_metrics_sampler is not None:
            await self._pgdog_metrics_sampler.connect()
        return await self._sampler.effective_settings()

    async def _publish_ready(self, ready_path: Path) -> None:
        readiness = [
            self._pg_ready.wait(),
            self._temporal_ready.wait(),
            self._resource_ready.wait(),
        ]
        if self._db_pool_metrics_sampler is not None:
            readiness.append(self._db_pool_metrics_ready.wait())
        if self._pgdog_metrics_sampler is not None:
            readiness.append(self._pgdog_metrics_ready.wait())
        await asyncio.gather(*readiness)
        readiness = CollectorReady(
            run_id=run_id_fingerprint(self._config.run_id),
            status="ready",
            sampled_at=_utc_now_iso(),
            cluster_num=self._config.cluster_num,
            public_api_url=self._config.public_api_url,
            workspace_fingerprint=workspace_fingerprint(
                str(WorkspaceUUID.new(self._config.workspace_id))
            ),
            sample_count=self._sample_count,
            temporal_sample_count=self._temporal_sample_count,
            resource_sample_count=self._resource_sample_count,
        )
        ready_path.write_text(json.dumps(readiness, indent=2), encoding="utf-8")
        self._ready.set()

    async def _capture_activity_metrics(self) -> dict[str, str]:
        """Capture SDK deltas and history aggregates after recovery sampling."""
        handoff_path_value = self._config.execution_id_handoff_path
        if handoff_path_value is None:
            return {}
        executor_task_queue = self._config.temporal_executor_task_queue
        if executor_task_queue is None:
            raise ActivityMetricsCaptureError(
                "post-recovery activity metrics require the executor task queue"
            )
        if not self._config.temporal_workflow_task_queues:
            raise ActivityMetricsCaptureError(
                "post-recovery activity metrics require a workflow task queue"
            )

        handoff_path = Path(handoff_path_value)
        request_path = self._artifact_dir / RUNNER_MEASUREMENT_READY_FILENAME
        acknowledgement_path = self._artifact_dir / COLLECTOR_MEASUREMENT_READY_FILENAME
        completion_request_path = (
            self._artifact_dir / RUNNER_MEASUREMENT_COMPLETE_FILENAME
        )
        completion_acknowledgement_path = (
            self._artifact_dir / COLLECTOR_MEASUREMENT_COMPLETE_FILENAME
        )
        try:
            handoff = load_activity_metrics_handoff(str(handoff_path))
            if handoff["run_id"] != run_id_fingerprint(self._config.run_id):
                raise ActivityMetricsCaptureError(
                    "activity metrics handoff does not match the collector run"
                )
            if not handoff["workflow_execution_ids_complete"]:
                raise ActivityMetricsCaptureError(
                    "activity metrics are incomplete because an admission outcome "
                    "has no workflow execution ID"
                )
            measurement_window = handoff["measurement_window_seconds"]
            sdk_metrics, history_metrics = await asyncio.gather(
                self._sdk_metrics_capture.capture_delta(
                    measurement_window_seconds=measurement_window
                ),
                collect_activity_history_metrics(
                    self._temporal_sampler.client,
                    handoff,
                    workflow_task_queue=(self._config.temporal_workflow_task_queues[0]),
                    executor_task_queue=executor_task_queue,
                ),
            )
            sdk_metrics_path = self._artifact_dir / "temporal_sdk_metrics.json"
            history_metrics_path = self._artifact_dir / "activity_metrics.json"
            sdk_metrics_path.write_text(
                json.dumps(sdk_metrics, indent=2) + "\n",
                encoding="utf-8",
            )
            history_metrics_path.write_text(
                json.dumps(history_metrics, indent=2) + "\n",
                encoding="utf-8",
            )
            return {
                "temporal_sdk_metrics": str(sdk_metrics_path),
                "activity_metrics": str(history_metrics_path),
            }
        finally:
            # Raw workflow execution IDs and synchronization markers are
            # process-local control data, never shareable benchmark evidence.
            handoff_path.unlink(missing_ok=True)
            request_path.unlink(missing_ok=True)
            acknowledgement_path.unlink(missing_ok=True)
            completion_request_path.unlink(missing_ok=True)
            completion_acknowledgement_path.unlink(missing_ok=True)

    async def run(
        self,
        repo_root: Path,
        *,
        claim_path: Path | None = None,
        startup_deadline: float | None = None,
    ) -> CollectorManifest:
        if claim_path is None:
            claim_path = self._claim_artifact_directory()
        else:
            _validate_artifact_directory_claim(self._artifact_dir, claim_path)
        started_at = _utc_now_iso()

        artifacts: dict[str, str] = {"collector_claim": str(claim_path)}
        observability_failure: str | None = None
        samples_path = self._artifact_dir / "pg_activity.jsonl"
        temporal_samples_path = self._artifact_dir / "temporal_backlog.jsonl"
        resource_samples_path = self._artifact_dir / "resource_usage.jsonl"
        db_pool_metrics_path = self._artifact_dir / "db_pool_metrics.jsonl"
        pgdog_metrics_path = self._artifact_dir / "pgdog_metrics.jsonl"
        ready_path = self._artifact_dir / "collector_ready.json"
        ready_path.unlink(missing_ok=True)
        artifacts["pg_activity"] = str(samples_path)
        artifacts["temporal_backlog"] = str(temporal_samples_path)
        artifacts["resource_usage"] = str(resource_samples_path)
        if self._db_pool_metrics_sampler is not None:
            artifacts["db_pool_metrics"] = str(db_pool_metrics_path)
        if self._pgdog_metrics_sampler is not None:
            artifacts["pgdog_metrics"] = str(pgdog_metrics_path)
        sample_task: asyncio.Task[None] | None = None
        ready_waiter: asyncio.Task[None] | None = None
        try:
            loop = asyncio.get_running_loop()
            if startup_deadline is None:
                startup_deadline = (
                    time.monotonic() + self._config.readiness_timeout_seconds
                )
            readiness_deadline = loop.time() + max(
                0.0,
                startup_deadline - time.monotonic(),
            )
            try:
                async with asyncio.timeout_at(readiness_deadline):
                    settings = await self._connect_samplers()
            except TimeoutError as exc:
                self._stop.set()
                raise SamplingReadinessError(
                    "collector connections and settings capture did not complete "
                    "within "
                    f"{self._config.readiness_timeout_seconds:g} seconds"
                ) from exc
            settings_path = self._artifact_dir / "pg_settings.json"
            settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            artifacts["pg_settings"] = str(settings_path)

            sample_task = asyncio.create_task(
                self._run_sampling_loops(
                    samples_path,
                    temporal_samples_path,
                    resource_samples_path,
                )
            )
            ready_waiter = asyncio.create_task(self._publish_ready(ready_path))
            done, _ = await asyncio.wait(
                (sample_task, ready_waiter),
                timeout=max(
                    0.0,
                    readiness_deadline - asyncio.get_running_loop().time(),
                ),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                self._stop.set()
                await sample_task
                raise SamplingReadinessError(
                    "sampling did not become ready within "
                    f"{self._config.readiness_timeout_seconds:g} seconds"
                )
            if sample_task in done and not (
                self._pg_ready.is_set()
                and self._temporal_ready.is_set()
                and self._resource_ready.is_set()
                and (
                    self._db_pool_metrics_sampler is None
                    or self._db_pool_metrics_ready.is_set()
                )
                and (
                    self._pgdog_metrics_sampler is None
                    or self._pgdog_metrics_ready.is_set()
                )
            ):
                await sample_task
                raise SamplingReadinessError(
                    "sampling stopped before all required signals were ready"
                )
            await ready_waiter
            artifacts["collector_ready"] = str(ready_path)

            # Keep sampling while the synchronous wrapper renders Compose. This
            # prevents a one-shot runner released by collector_ready.json from
            # landing entirely inside an observation gap.
            try:
                compose_config = await asyncio.to_thread(
                    capture_compose_config,
                    self._config,
                    self._artifact_dir,
                    repo_root,
                )
                artifacts["compose_config"] = compose_config
            except RequiredArtifactCaptureError as exc:
                observability_failure = type(exc).__name__

            await sample_task
            if self._pending_sampling_failures and observability_failure is None:
                observability_failure = next(
                    iter(self._pending_sampling_failures.values())
                )
            if self._external_stop_requested and observability_failure is None:
                observability_failure = RecoveryWindowInterruptedError.__name__

            correctness = await self._sampler.row_correctness(
                self._config.workspace_id,
                DEFAULT_TABLE_NAME,
                self._config.run_id,
            )
            if correctness is None:
                raise RowCorrectnessCaptureError(
                    "fixture row correctness is unavailable"
                )
            correctness_path = self._artifact_dir / "row_correctness.json"
            correctness_path.write_text(
                json.dumps(correctness, indent=2), encoding="utf-8"
            )
            artifacts["row_correctness"] = str(correctness_path)

            drift = await self._sampler.table_drift(
                self._config.workspace_id,
                DEFAULT_TABLE_NAME,
            )
            if drift is None:
                raise TableDriftCaptureError(
                    "fixture table drift metrics are unavailable"
                )
            drift_path = self._artifact_dir / "table_drift.json"
            drift_path.write_text(json.dumps(drift, indent=2), encoding="utf-8")
            artifacts["table_drift"] = str(drift_path)
        except (
            asyncpg.PostgresError,
            httpx.HTTPError,
            OSError,
            RPCError,
            RuntimeError,
            TimeoutError,
            ExceptionGroup,
        ) as exc:
            # Losing a required observability signal invalidates the run. Keep
            # collecting final process/container evidence, but mark the manifest
            # and make the CLI fail so the artifact set cannot look successful.
            if observability_failure is None:
                observability_failure = _observability_failure_name(exc)
            _append_observability_failure(samples_path, observability_failure)
            _append_observability_failure(temporal_samples_path, observability_failure)
            _append_observability_failure(resource_samples_path, observability_failure)
            if self._db_pool_metrics_sampler is not None:
                _append_observability_failure(
                    db_pool_metrics_path,
                    observability_failure,
                )
            if self._pgdog_metrics_sampler is not None:
                _append_observability_failure(pgdog_metrics_path, observability_failure)
        finally:
            if ready_waiter is not None and not ready_waiter.done():
                ready_waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ready_waiter
            if sample_task is not None and not sample_task.done():
                sample_task.cancel()
                try:
                    await sample_task
                except asyncio.CancelledError:
                    pass
                except ExceptionGroup as exc:
                    if observability_failure is None:
                        observability_failure = _observability_failure_name(exc)
            with contextlib.suppress(
                asyncpg.PostgresError,
                httpx.HTTPError,
                OSError,
                RuntimeError,
                TimeoutError,
            ):
                await self._sampler.close()
                if self._db_pool_metrics_sampler is not None:
                    await self._db_pool_metrics_sampler.close()
                if self._pgdog_metrics_sampler is not None:
                    await self._pgdog_metrics_sampler.close()

        if self._config.execution_id_handoff_path is not None:
            try:
                artifacts.update(await self._capture_activity_metrics())
            except (OSError, RuntimeError) as exc:
                if observability_failure is None:
                    observability_failure = type(exc).__name__

        try:
            containers = capture_container_state(self._config, self._artifact_dir)
            artifacts["containers"] = containers
        except RequiredArtifactCaptureError as exc:
            if observability_failure is None:
                observability_failure = type(exc).__name__
        try:
            for service, path in capture_service_logs(
                self._config,
                self._artifact_dir,
                repo_root,
                since=started_at,
            ).items():
                artifacts[f"log:{service}"] = path
        except RequiredArtifactCaptureError as exc:
            if observability_failure is None:
                observability_failure = type(exc).__name__

        # The runner writes these into the same directory by convention.
        missing_runner_artifacts: list[str] = []
        for name in (
            "scenario.json",
            "runner_results.jsonl",
            "summary.txt",
            RUNNER_COMPLETE_FILENAME,
        ):
            candidate = self._artifact_dir / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                artifacts[name] = str(candidate)
            else:
                missing_runner_artifacts.append(name)
        if missing_runner_artifacts and observability_failure is None:
            observability_failure = RunnerArtifactsCaptureError.__name__

        try:
            commit, dirty = capture_commit(repo_root)
        except RequiredArtifactCaptureError as exc:
            commit, dirty = "unknown", False
            if observability_failure is None:
                observability_failure = type(exc).__name__
        shareable_artifacts = {
            name: shareable_artifact_path(
                path,
                self._artifact_dir,
                self._config.run_id,
            )
            for name, path in artifacts.items()
        }
        manifest = CollectorManifest(
            run_id=run_id_fingerprint(self._config.run_id),
            case_id=self._config.case_id,
            status=(
                "observability_failed"
                if observability_failure is not None
                else self._runner_status or "completed"
            ),
            observability_failure=observability_failure,
            artifact_dir=shareable_artifact_path(
                self._artifact_dir,
                self._artifact_dir,
                self._config.run_id,
            ),
            started_at=started_at,
            finished_at=_utc_now_iso(),
            sample_interval_seconds=self._config.sample_interval_seconds,
            readiness_timeout_seconds=self._config.readiness_timeout_seconds,
            sample_count=self._sample_count,
            temporal_sample_count=self._temporal_sample_count,
            resource_sample_count=self._resource_sample_count,
            tracecat_commit=commit,
            tracecat_commit_dirty=dirty,
            cluster_num=self._config.cluster_num,
            public_api_url=self._config.public_api_url,
            workspace_fingerprint=workspace_fingerprint(
                str(WorkspaceUUID.new(self._config.workspace_id))
            ),
            ee_multi_tenant=self._config.ee_multi_tenant,
            compose_project_fingerprint=compose_project_fingerprint(
                self._config.compose_project
            ),
            temporal_target=deployment_value_fingerprint(self._config.temporal_target),
            temporal_namespace=deployment_value_fingerprint(
                self._config.temporal_namespace
            ),
            temporal_workflow_task_queues=tuple(
                deployment_value_fingerprint(queue)
                for queue in self._config.temporal_workflow_task_queues
            ),
            temporal_activity_task_queues=tuple(
                deployment_value_fingerprint(queue)
                for queue in self._config.temporal_activity_task_queues
            ),
            artifacts=shareable_artifacts,
        )
        (self._artifact_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest


def _write_preflight_failure_manifest(
    *,
    artifact_dir: Path,
    claim_path: Path,
    run_id: str,
    case_id: str | None,
    workspace_id: str,
    started_at: str,
    failure_name: str,
    sample_interval_seconds: float,
    readiness_timeout_seconds: float,
    cluster_num: int,
    ee_multi_tenant: bool,
    compose_project: str | None,
    temporal_target: str,
    temporal_namespace: str,
    temporal_workflow_task_queues: tuple[str, ...],
    temporal_activity_task_queues: tuple[str, ...],
) -> CollectorManifest:
    """Publish a complete failed manifest when startup validation cannot finish."""
    manifest = CollectorManifest(
        run_id=run_id_fingerprint(run_id),
        case_id=case_id,
        status="observability_failed",
        observability_failure=failure_name,
        artifact_dir=shareable_artifact_path(
            artifact_dir,
            artifact_dir,
            run_id,
        ),
        started_at=started_at,
        finished_at=_utc_now_iso(),
        sample_interval_seconds=sample_interval_seconds,
        readiness_timeout_seconds=readiness_timeout_seconds,
        sample_count=0,
        temporal_sample_count=0,
        resource_sample_count=0,
        tracecat_commit="unknown",
        tracecat_commit_dirty=False,
        cluster_num=cluster_num,
        # Startup can fail before this caller-supplied endpoint is validated.
        public_api_url=REDACTED_ENV_VALUE,
        workspace_fingerprint=workspace_fingerprint(
            str(WorkspaceUUID.new(workspace_id))
        ),
        ee_multi_tenant=ee_multi_tenant,
        compose_project_fingerprint=(
            compose_project_fingerprint(compose_project)
            if compose_project is not None
            else None
        ),
        temporal_target=deployment_value_fingerprint(temporal_target),
        temporal_namespace=deployment_value_fingerprint(temporal_namespace),
        temporal_workflow_task_queues=tuple(
            deployment_value_fingerprint(queue)
            for queue in temporal_workflow_task_queues
        ),
        temporal_activity_task_queues=tuple(
            deployment_value_fingerprint(queue)
            for queue in temporal_activity_task_queues
        ),
        artifacts={
            "collector_claim": shareable_artifact_path(
                claim_path,
                artifact_dir,
                run_id,
            )
        },
    )
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracecat_benchmark.collector",
        description="PostgreSQL and runtime collector for workflow load tests.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--case-id",
        default=None,
        help="Human-readable matrix case slug retained in the manifest.",
    )
    parser.add_argument(
        "--workspace-id",
        required=True,
        help="Workspace ID recorded by the runner; scopes the physical table schema.",
    )
    parser.add_argument("--artifact-root", default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "Explicit monitoring DSN. Must use a non-superuser role with "
            "pg_read_all_stats plus USAGE/SELECT on the fixture schema/table. "
            "Omit to read --dsn-env."
        ),
    )
    parser.add_argument(
        "--dsn-env",
        default=DEFAULT_DSN_ENV,
        help="Environment variable holding the required non-superuser monitoring DSN.",
    )
    parser.add_argument(
        "--sample-interval-seconds",
        type=float,
        default=0.5,
        help="Must be <= 1.0 to satisfy the plan's >=1Hz requirement.",
    )
    parser.add_argument(
        "--readiness-timeout-seconds",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_SECONDS,
        help=(
            "Fail the collector if all required signals do not produce an "
            "initial durable sample within this window."
        ),
    )
    parser.add_argument(
        "--cluster-num",
        required=True,
        type=int,
        help="Number of the existing cluster being measured.",
    )
    parser.add_argument(
        "--public-api-url",
        required=True,
        help="Exact public API URL used by the runner; must end with /api.",
    )
    parser.add_argument(
        "--ee-multi-tenant",
        required=True,
        choices=("true", "false"),
        help="Exact tenant mode used to start the selected cluster.",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        required=True,
        help=(
            "Repeat every path printed by `scripts/cluster ... compose-files`, "
            "in the same order (including the sandbox layer when enabled)."
        ),
    )
    parser.add_argument(
        "--log-service",
        action="append",
        default=None,
        help="Repeatable extra service; required diagnostic services are always kept.",
    )
    parser.add_argument(
        "--temporal-target",
        required=True,
        help=(
            "Exact host:port of the numbered cluster's Temporal frontend "
            "(for example localhost:7333; do not include http://)."
        ),
    )
    parser.add_argument(
        "--temporal-namespace",
        required=True,
        help="Temporal namespace used by the selected cluster.",
    )
    parser.add_argument(
        "--temporal-workflow-task-queue",
        action="append",
        required=True,
        help="Repeat for every workflow task queue used by the scenario.",
    )
    parser.add_argument(
        "--temporal-activity-task-queue",
        action="append",
        required=True,
        help="Repeat for every activity task queue used by the scenario.",
    )
    parser.add_argument(
        "--temporal-executor-task-queue",
        default=None,
        help=(
            "Executor queue used to label Tracecat action histories. Required "
            "with --activity-metrics-handoff."
        ),
    )
    parser.add_argument(
        "--activity-metrics-handoff",
        default=None,
        help=(
            "Private runner handoff containing measured workflow execution IDs. "
            "The collector deletes it after post-recovery aggregation."
        ),
    )
    parser.add_argument(
        "--temporal-worker-metrics-url",
        default=None,
        help="Load-test-only Temporal SDK Prometheus URL for the DSL worker.",
    )
    parser.add_argument(
        "--temporal-executor-metrics-url",
        action="append",
        default=None,
        help=(
            "Repeat for every load-test executor replica's Temporal SDK "
            "Prometheus endpoint."
        ),
    )
    parser.add_argument(
        "--api-db-pool-metrics-url",
        default=None,
        help="Load-test-only SQLAlchemy pool metrics URL for the API process.",
    )
    parser.add_argument(
        "--worker-db-pool-metrics-url",
        default=None,
        help="Load-test-only SQLAlchemy pool metrics URL for the DSL worker.",
    )
    parser.add_argument(
        "--executor-db-pool-metrics-url",
        action="append",
        default=None,
        help=(
            "Repeat for every load-test executor replica's SQLAlchemy pool "
            "metrics endpoint."
        ),
    )
    parser.add_argument(
        "--pgdog-metrics-url",
        default=None,
        help="Optional PgDog OpenMetrics URL for pooled benchmark runs.",
    )
    parser.add_argument(
        "--recovery-seconds",
        type=float,
        default=DEFAULT_RECOVERY_SECONDS,
        help=(
            "Continue sampling for this long after the runner publishes its "
            "completion marker (minimum 5 seconds)."
        ),
    )
    return parser


async def amain(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    repo_root = resolve_repository_root()

    if args.case_id is not None and CASE_ID_RE.fullmatch(args.case_id) is None:
        print(f"--case-id must match {CASE_ID_RE.pattern!r}", file=sys.stderr)
        return 2

    if not math.isfinite(args.sample_interval_seconds) or not (
        0.0 < args.sample_interval_seconds <= 1.0
    ):
        print(
            "--sample-interval-seconds must be finite and in (0, 1] (>=1Hz)",
            file=sys.stderr,
        )
        return 2
    if (
        not math.isfinite(args.readiness_timeout_seconds)
        or args.readiness_timeout_seconds <= 0
    ):
        print(
            "--readiness-timeout-seconds must be finite and positive",
            file=sys.stderr,
        )
        return 2
    if (
        not math.isfinite(args.recovery_seconds)
        or args.recovery_seconds < MIN_RECOVERY_SECONDS
    ):
        print(
            "--recovery-seconds must be finite and at least "
            f"{MIN_RECOVERY_SECONDS:g} seconds",
            file=sys.stderr,
        )
        return 2
    if not 1 <= args.cluster_num <= 99:
        print("--cluster-num must be between 1 and 99", file=sys.stderr)
        return 2
    try:
        workspace_id = str(WorkspaceUUID.new(args.workspace_id))
    except ValueError:
        print("--workspace-id must be a valid workspace ID", file=sys.stderr)
        return 2

    dsn = args.dsn or os.environ.get(args.dsn_env)
    if not dsn:
        print(
            f"No monitoring DSN provided; set {args.dsn_env} or pass --dsn",
            file=sys.stderr,
        )
        return 2

    try:
        log_services = _resolve_log_services(args.log_service)
    except CollectorConfigurationError as exc:
        print(f"Invalid log service: {exc}", file=sys.stderr)
        return 2

    compose_files = tuple(args.compose_file)
    ee_multi_tenant = args.ee_multi_tenant == "true"
    workflow_task_queues = tuple(args.temporal_workflow_task_queue)
    activity_task_queues = tuple(args.temporal_activity_task_queue)
    executor_metrics_urls = tuple(
        cast(list[str] | None, args.temporal_executor_metrics_url) or ()
    )
    executor_db_pool_metrics_urls = tuple(
        cast(list[str] | None, args.executor_db_pool_metrics_url) or ()
    )
    activity_metrics_values = (
        args.activity_metrics_handoff,
        args.temporal_executor_task_queue,
        args.temporal_worker_metrics_url,
    )
    activity_metrics_requested = any(
        value is not None for value in activity_metrics_values
    ) or bool(executor_metrics_urls)
    if activity_metrics_requested and (
        not all(isinstance(value, str) and value for value in activity_metrics_values)
        or not executor_metrics_urls
    ):
        print(
            "--activity-metrics-handoff, --temporal-executor-task-queue, "
            "--temporal-worker-metrics-url, and at least one "
            "--temporal-executor-metrics-url must be provided together",
            file=sys.stderr,
        )
        return 2
    if (
        args.temporal_executor_task_queue is not None
        and args.temporal_executor_task_queue not in activity_task_queues
    ):
        print(
            "--temporal-executor-task-queue must also be listed as a "
            "--temporal-activity-task-queue",
            file=sys.stderr,
        )
        return 2
    db_pool_metrics_values = (
        args.api_db_pool_metrics_url,
        args.worker_db_pool_metrics_url,
    )
    db_pool_metrics_requested = any(
        value is not None for value in db_pool_metrics_values
    ) or bool(executor_db_pool_metrics_urls)
    if db_pool_metrics_requested and (
        not all(isinstance(value, str) and value for value in db_pool_metrics_values)
        or not executor_db_pool_metrics_urls
    ):
        print(
            "--api-db-pool-metrics-url, --worker-db-pool-metrics-url, and at "
            "least one --executor-db-pool-metrics-url must be provided together",
            file=sys.stderr,
        )
        return 2
    sdk_metrics_endpoints = (
        (
            SdkMetricsEndpoint(
                service="worker",
                replica_index=1,
                url=args.temporal_worker_metrics_url,
            ),
            *(
                SdkMetricsEndpoint(
                    service="executor",
                    replica_index=replica_index,
                    url=url,
                )
                for replica_index, url in enumerate(executor_metrics_urls, start=1)
            ),
        )
        if args.temporal_worker_metrics_url is not None and executor_metrics_urls
        else ()
    )
    db_pool_metrics_endpoints = (
        (
            DbPoolMetricsEndpoint(
                service="api",
                replica_index=1,
                url=args.api_db_pool_metrics_url,
            ),
            DbPoolMetricsEndpoint(
                service="worker",
                replica_index=1,
                url=args.worker_db_pool_metrics_url,
            ),
            *(
                DbPoolMetricsEndpoint(
                    service="executor",
                    replica_index=replica_index,
                    url=url,
                )
                for replica_index, url in enumerate(
                    executor_db_pool_metrics_urls,
                    start=1,
                )
            ),
        )
        if args.api_db_pool_metrics_url is not None
        and args.worker_db_pool_metrics_url is not None
        and executor_db_pool_metrics_urls
        else ()
    )
    artifact_dir = Path(args.artifact_root) / run_id_fingerprint(args.run_id)
    started_at = _utc_now_iso()
    startup_deadline = time.monotonic() + args.readiness_timeout_seconds
    try:
        claim_path = _claim_artifact_directory(artifact_dir, args.run_id)
    except ArtifactDirectoryReuseError as exc:
        print(f"Artifact directory error: {exc}", file=sys.stderr)
        return 2

    compose_project: str | None = None
    startup_abort_requested = False
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    installed_signals: list[signal.Signals] = []

    def write_preflight_failure(failure_name: str) -> None:
        _write_preflight_failure_manifest(
            artifact_dir=artifact_dir,
            claim_path=claim_path,
            run_id=args.run_id,
            case_id=args.case_id,
            workspace_id=workspace_id,
            started_at=started_at,
            failure_name=failure_name,
            sample_interval_seconds=args.sample_interval_seconds,
            readiness_timeout_seconds=args.readiness_timeout_seconds,
            cluster_num=args.cluster_num,
            ee_multi_tenant=ee_multi_tenant,
            compose_project=compose_project,
            temporal_target=args.temporal_target,
            temporal_namespace=args.temporal_namespace,
            temporal_workflow_task_queues=workflow_task_queues,
            temporal_activity_task_queues=activity_task_queues,
        )

    def request_startup_abort() -> None:
        nonlocal startup_abort_requested
        if startup_abort_requested:
            return
        startup_abort_requested = True
        write_preflight_failure(CollectorStartupInterruptedError.__name__)
        if current_task is not None:
            current_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_startup_abort)
        except NotImplementedError:
            continue
        installed_signals.append(sig)

    try:
        try:
            compose_project = resolve_compose_project(
                repo_root,
                args.cluster_num,
                compose_files,
                ee_multi_tenant,
                args.public_api_url,
                deadline=startup_deadline,
            )
            compose_public_urls = validate_running_compose_project(
                repo_root,
                args.cluster_num,
                compose_files,
                ee_multi_tenant,
                compose_project,
                deadline=startup_deadline,
            )
            cluster_ports = resolve_cluster_ports(
                repo_root,
                args.cluster_num,
                compose_files,
                ee_multi_tenant,
                deadline=startup_deadline,
            )
            validate_public_api_url(args.public_api_url, cluster_ports)
            validate_monitor_dsn_target(dsn, cluster_ports)
            if args.temporal_worker_metrics_url is not None and executor_metrics_urls:
                validate_temporal_sdk_metrics_urls(
                    args.temporal_worker_metrics_url,
                    executor_metrics_urls,
                    cluster_ports,
                )
            if (
                args.api_db_pool_metrics_url is not None
                and args.worker_db_pool_metrics_url is not None
                and executor_db_pool_metrics_urls
            ):
                validate_db_pool_metrics_urls(
                    args.api_db_pool_metrics_url,
                    args.worker_db_pool_metrics_url,
                    executor_db_pool_metrics_urls,
                    cluster_ports,
                )
            if args.pgdog_metrics_url is not None:
                validate_pgdog_metrics_url(args.pgdog_metrics_url, cluster_ports)
            validate_temporal_context(
                args.temporal_target,
                args.temporal_namespace,
                workflow_task_queues,
                activity_task_queues,
                cluster_ports,
                repo_root,
                args.cluster_num,
                compose_files,
                ee_multi_tenant,
                args.public_api_url,
                deadline=startup_deadline,
            )
        except CollectorConfigurationError as exc:
            write_preflight_failure(type(exc).__name__)
            print(f"Invalid cluster context: {exc}", file=sys.stderr)
            return 2

        # Run a queued startup signal before installing the steady-state handler.
        await asyncio.sleep(0)

        if compose_project is None:
            raise AssertionError("validated Compose project is unavailable")
        config = CollectorConfig(
            run_id=args.run_id,
            workspace_id=workspace_id,
            artifact_dir=str(artifact_dir),
            dsn=dsn,
            sample_interval_seconds=args.sample_interval_seconds,
            readiness_timeout_seconds=args.readiness_timeout_seconds,
            cluster_num=args.cluster_num,
            public_api_url=args.public_api_url.rstrip("/"),
            compose_public_app_url=compose_public_urls.app,
            compose_public_api_url=compose_public_urls.api,
            ee_multi_tenant=ee_multi_tenant,
            compose_project=compose_project,
            compose_files=compose_files,
            log_services=log_services,
            recovery_seconds=args.recovery_seconds,
            temporal_target=args.temporal_target,
            temporal_namespace=args.temporal_namespace,
            temporal_workflow_task_queues=workflow_task_queues,
            temporal_activity_task_queues=activity_task_queues,
            temporal_executor_task_queue=args.temporal_executor_task_queue,
            execution_id_handoff_path=args.activity_metrics_handoff,
            temporal_sdk_metrics_endpoints=sdk_metrics_endpoints,
            db_pool_metrics_endpoints=db_pool_metrics_endpoints,
            pgdog_metrics_url=args.pgdog_metrics_url,
            case_id=args.case_id,
        )

        collector = MetricCollector(config)
        for sig in installed_signals:
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, collector.request_stop)

        try:
            manifest = await collector.run(
                repo_root,
                claim_path=claim_path,
                startup_deadline=startup_deadline,
            )
        except ArtifactDirectoryReuseError as exc:
            print(f"Artifact directory error: {exc}", file=sys.stderr)
            return 2
        print(f"status: {manifest['status']}")
        print(f"samples: {manifest['sample_count']}")
        print(f"artifacts: {config.artifact_dir}")
        return 0 if manifest["status"] == "completed" else 1
    except asyncio.CancelledError:
        if not startup_abort_requested:
            raise
        if current_task is not None:
            current_task.uncancel()
        print("Metric collector interrupted during startup", file=sys.stderr)
        return 1
    finally:
        for sig in installed_signals:
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)


def main() -> None:
    raise SystemExit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
