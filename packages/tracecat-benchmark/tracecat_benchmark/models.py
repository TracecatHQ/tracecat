"""Typed models shared by the workflow load-test runner and collector.

The PostgreSQL scatter plan is one experiment built on these models.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, TypedDict

# Terminal Temporal workflow execution statuses. Anything not in this set is
# still in flight. Mirrors WorkflowExecutionStatusLiteral in
# tracecat/workflow/executions/enums.py.
WorkflowExecutionStatus = Literal[
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELED",
    "TERMINATED",
    "CONTINUED_AS_NEW",
    "TIMED_OUT",
]

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "CANCELED",
        "TERMINATED",
        "CONTINUED_AS_NEW",
        "TIMED_OUT",
    }
)
RUNNER_COMPLETE_FILENAME: Final = "runner_complete.json"
RUNNER_MEASUREMENT_READY_FILENAME: Final = ".runner_measurement_ready.json"
COLLECTOR_MEASUREMENT_READY_FILENAME: Final = ".collector_measurement_ready.json"
RUNNER_MEASUREMENT_COMPLETE_FILENAME: Final = ".runner_measurement_complete.json"
COLLECTOR_MEASUREMENT_COMPLETE_FILENAME: Final = ".collector_measurement_complete.json"
ARTIFACT_ROOT_PLACEHOLDER: Final = "<artifact-root>"
MAX_LOADTEST_EXECUTOR_REPLICAS: Final = 10
MAX_BULK_BRANCH_COUNT: Final = 1000


def workspace_fingerprint(workspace_id: str) -> str:
    """Return a shareable correlation key without retaining the workspace ID."""
    digest = hashlib.sha256(workspace_id.encode()).hexdigest()
    return f"sha256:{digest}"


def run_id_fingerprint(run_id: str) -> str:
    """Return a stable artifact key without retaining the user-provided run ID."""
    digest = hashlib.sha256(run_id.encode()).hexdigest()
    return f"sha256:{digest}"


def workflow_execution_fingerprint(workflow_execution_id: str) -> str:
    """Return a stable key without retaining a Temporal workflow execution ID."""
    digest = hashlib.sha256(workflow_execution_id.encode()).hexdigest()
    return f"sha256:{digest}"


def shareable_artifact_path(
    path: str | Path,
    artifact_dir: str | Path,
    run_id: str,
) -> str:
    """Return a path relative to a fixed, shareable artifact-root placeholder."""
    run_root = Path(artifact_dir).resolve(strict=False)
    try:
        relative = Path(path).resolve(strict=False).relative_to(run_root)
    except ValueError:
        return f"{ARTIFACT_ROOT_PLACEHOLDER}/[outside-run-directory]"

    shareable_root = f"{ARTIFACT_ROOT_PLACEHOLDER}/{run_id_fingerprint(run_id)}"
    if relative == Path():
        return shareable_root
    return f"{shareable_root}/{relative.as_posix()}"


def compose_project_fingerprint(compose_project: str) -> str:
    """Return a shareable key without retaining the worktree-derived project."""
    digest = hashlib.sha256(compose_project.encode()).hexdigest()
    return f"sha256:{digest}"


def deployment_value_fingerprint(value: str) -> str:
    """Return a shareable key without retaining a deployment identifier."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    return f"sha256:{digest}"


class LoadType(StrEnum):
    """Runnable workload families supported by the shared harness.

    Each member is a peer selected by ``--load-type``. Adding another workload,
    such as agent fan-out, should add a fixture definition instead of another
    runner lifecycle.
    """

    SCATTER = "scatter"
    """Independent static core.table.insert_row actions in each workflow.

    ``branch_count`` selects how many action nodes fixture setup materializes.
    There is no runtime ``for_each`` multiplier.
    """

    NOOP = "noop"
    """Independent static core.transform.reshape actions with literal inputs.

    This control retains the normal workflow, Temporal, executor, sandbox, and
    Action Gateway lifecycle while removing expression and database work.
    """

    BULK = "bulk"
    """Control: one batch table request in one action execution per workflow."""

    SUBFLOW = "subflow"
    """One child workflow execution per branch.

    Selects the parent fixture. Its child fixture is uploaded, aliased, and
    committed as a dependency by `fixtures.ensure_fixtures`.
    """

    @property
    def materializes_static_actions(self) -> bool:
        """Whether fixture setup expands ``branch_count`` into action nodes."""
        return self in {LoadType.SCATTER, LoadType.NOOP}

    @property
    def writes_fixture_rows(self) -> bool:
        """Whether each successful logical branch writes one fixture row."""
        return self is not LoadType.NOOP


class FailureMode(StrEnum):
    """Primary failure classification from the load runner's vantage point.

    The plan's full classification also includes modes the runner cannot see
    (pool timeout, PostgreSQL connection-slot exhaustion, statement/lock
    timeout, Temporal schedule-to-start delay, executor saturation, container
    OOM). Those are attributed during analysis by correlating these records
    with the collector's PostgreSQL samples and aggregate service-log
    diagnostics.

    Classification here is derived only from structured signals - the HTTP
    status code of the submission and the terminal workflow status - never
    from error-message text.
    """

    NONE = "none"
    ADMISSION_REJECTED = "admission_rejected"
    """Submission returned a non-2xx status."""

    ADMISSION_OUTCOME_UNKNOWN = "admission_outcome_unknown"
    """A 2xx response omitted the execution ID, so it may already be running."""

    SUBMIT_TRANSPORT_ERROR = "submit_transport_error"
    """Submission never produced an HTTP status (connect/read/pool error)."""

    SUBMIT_TIMEOUT = "submit_timeout"
    """Submission did not finish before the per-run deadline."""

    WORKFLOW_FAILED = "workflow_failed"
    """Reached a terminal non-COMPLETED status."""

    RUN_TIMEOUT = "run_timeout"
    """Still non-terminal when the per-run timeout expired."""

    POLL_TRANSPORT_ERROR = "poll_transport_error"
    """Polling could not reach a terminal status because of transport errors."""

    ABORTED = "aborted"
    """Abort signal arrived before this execution reached a terminal status."""


class Phase(StrEnum):
    """Workload stage a submission belongs to."""

    WARMUP = "warmup"
    RAMP = "ramp"
    SUSTAIN = "sustain"


@dataclass(slots=True, frozen=True)
class TableColumnFixture:
    """One column of the synthetic fixture table."""

    name: str
    type: str
    nullable: bool


@dataclass(slots=True, frozen=True)
class TableFixture:
    """The synthetic fixture table definition loaded from fixtures/table.json."""

    name: str
    columns: tuple[TableColumnFixture, ...]
    unique_index_column: str
    unique_index_note: str


@dataclass(slots=True, frozen=True)
class WorkflowFixture:
    """A fixture workflow definition file and the identifiers it resolved to."""

    load_type: LoadType
    """The load type this fixture belongs to, parent and child alike."""
    path: str
    title: str
    alias: str | None = None
    """Reserved ownership alias assigned before commit.

    Parent aliases make fixture replacement safe; child aliases additionally
    provide the stable name used by `core.workflow.execute`. The external
    workflow format has no `alias` field, so setup uses the PATCH endpoint.
    """
    content: bytes | None = None
    """Generated YAML bytes, or ``None`` to upload ``path`` unchanged."""


@dataclass(slots=True, frozen=True)
class FixtureHandles:
    """Identifiers produced by fixture setup, reused across matrix cells."""

    workspace_id: str
    table_id: str
    table_name: str
    unique_index_column: str
    workflow_ids: dict[LoadType, str]


@dataclass(slots=True, frozen=True)
class AuthConfig:
    """How the runner authenticates against the local API.

    Exactly one of `password` (synthetic local user, fastapi-users cookie
    session) or `api_key` (service-account bearer token) is used.
    """

    email: str
    password: str | None
    api_key: str | None


@dataclass(slots=True, frozen=True)
class ScenarioConfig:
    """The exact runtime scenario configuration.

    `poll_interval_seconds` is deliberately part of the scenario config: the
    plan requires it to be identical across all phases so the baseline
    measurement absorbs the per-poll auth and membership query cost. Artifact
    serialization replaces `workspace_id` and `run_id` with fingerprints.
    """

    run_id: str
    base_url: str
    cluster_num: int | None
    workspace_id: str
    load_type: LoadType
    workflow_count: int
    branch_count: int
    ramp_seconds: float
    steady_state_seconds: float
    payload_bytes: int
    run_timeout_seconds: float
    poll_interval_seconds: float
    warmup: bool
    one_shot: bool
    collector_ready_timeout_seconds: float
    submit_concurrency: int
    max_connections: int
    artifact_dir: str
    tracecat_commit: str
    started_at: str
    auth_mode: Literal["password", "api_key"]
    abort_stops_polling: bool
    evidence_mode: Literal["compose_collector", "runner_only"] = "compose_collector"
    case_id: str | None = None


@dataclass(slots=True)
class ExecutionRecord:
    """One workflow execution, from submission to terminal state."""

    workflow_seq: int
    phase: Phase
    submitted_at: float
    accepted_at: float | None = None
    terminal_at: float | None = None
    wf_exec_id: str | None = None
    submit_status_code: int | None = None
    terminal_status: str | None = None
    history_length: int | None = None
    failure_mode: FailureMode = FailureMode.NONE
    poll_count: int = 0

    @property
    def accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def latency_seconds(self) -> float | None:
        if self.terminal_at is None:
            return None
        return self.terminal_at - self.submitted_at


@dataclass(slots=True, frozen=True)
class LatencySummary:
    """Percentile summary for end-to-end workflow latency."""

    count: int
    p50: float | None
    p95: float | None
    p99: float | None
    minimum: float | None
    maximum: float | None


@dataclass(slots=True, frozen=True)
class RunSummary:
    """Human- and machine-readable summary of one runner invocation."""

    run_id: str
    """SHA-256 fingerprint of the process-local run ID."""
    submitted: int
    accepted: int
    completed: int
    failed: int
    timed_out: int
    aborted: int
    failure_modes: dict[str, int]
    submit_status_codes: dict[str, int]
    wall_clock_seconds: float
    throughput_workflows_per_second: float
    latency: LatencySummary
    expected_rows: int
    submitted_row_target: int
    first_failure_at: float | None
    aborted_by_signal: bool


class PgActivitySample(TypedDict):
    """One >=1Hz sample derived from pg_stat_activity and pg_stat_database."""

    sampled_at: str
    monotonic: float
    max_connections: int
    superuser_reserved_connections: int
    total_connections: int
    active: int
    idle: int
    idle_in_transaction: int
    idle_in_transaction_aborted: int
    waiting: int
    wait_events: dict[str, int]
    application_names: dict[str, int]
    longest_transaction_seconds: float | None
    longest_query_seconds: float | None
    xact_commit_delta: int
    xact_rollback_delta: int
    deadlocks_delta: int
    connection_slot_errors: int


class TemporalTaskQueueStats(TypedDict):
    """One Temporal task queue's approximate backlog and flow rates."""

    approximate_backlog_count: int
    approximate_backlog_age_seconds: float
    tasks_add_rate: float
    tasks_dispatch_rate: float


class TemporalBacklogSample(TypedDict):
    """Workflow and activity task-queue state captured independently."""

    sampled_at: str
    monotonic: float
    workflow_task_queues: dict[str, TemporalTaskQueueStats]
    activity_task_queues: dict[str, TemporalTaskQueueStats]


class ContainerResourceUsage(TypedDict):
    """Runtime resource usage for one container in the selected Compose project."""

    container_id: str
    service: str
    cpu_percent: float
    memory_usage_bytes: int
    memory_limit_bytes: int
    memory_percent: float
    network_input_bytes: int
    network_output_bytes: int
    block_read_bytes: int
    block_write_bytes: int
    pids: int


class HostResourceUsage(TypedDict):
    """Host CPU load and memory pressure sampled alongside container usage."""

    logical_cpu_count: int
    load_average_1m: float
    load_average_5m: float
    load_average_15m: float
    memory_total_bytes: int
    memory_available_bytes: int
    memory_used_percent: float


class ResourceUsageSample(TypedDict):
    """Container and host resource usage during load submission and recovery."""

    sampled_at: str
    monotonic: float
    host: HostResourceUsage
    containers: list[ContainerResourceUsage]


class ServiceLogSignalCounts(TypedDict):
    """Aggregate overload signals derived without retaining raw log lines."""

    postgres_connection_limit: int
    database_pool_timeout: int
    statement_timeout: int
    lock_timeout: int
    deadlock: int
    serialization_failure: int
    connection_refused: int
    connection_reset: int
    timeout: int
    http_5xx: int


class ServiceLogSummary(TypedDict):
    """Shareable diagnostic counts for one service's current-run log window."""

    service: str
    since: str
    lines_scanned: int
    signal_counts: ServiceLogSignalCounts


class ContainerState(TypedDict):
    """Docker inspect state for one container in the cluster under test."""

    service: str
    image_id: str
    status: str
    restart_count: int
    oom_killed: bool
    exit_code: int | None
    nano_cpus: int | None
    memory_limit_bytes: int | None
    memory_swap_limit_bytes: int | None
    pids_limit: int | None


class RowCorrectness(TypedDict):
    """Expected-versus-actual unique row accounting for the fixture table."""

    workspace_fingerprint: str
    table_name: str
    total_rows: int
    distinct_dedupe_keys: int
    duplicate_dedupe_keys: int
    rows_for_run: int
    distinct_dedupe_keys_for_run: int


class TableDrift(TypedDict):
    """Physical size and maintenance counters for the fixture table."""

    workspace_fingerprint: str
    table_name: str
    table_bytes: int
    indexes_bytes: int
    total_relation_bytes: int
    live_tuples: int
    dead_tuples: int
    inserts: int
    updates: int
    deletes: int
    hot_updates: int
    vacuum_count: int
    autovacuum_count: int
    analyze_count: int
    autoanalyze_count: int
    last_vacuum: str | None
    last_autovacuum: str | None
    last_analyze: str | None
    last_autoanalyze: str | None


class CollectorManifest(TypedDict):
    """Index of everything the collector wrote for a run."""

    run_id: str
    """SHA-256 fingerprint of the process-local run ID."""
    case_id: str | None
    status: Literal["completed", "aborted", "observability_failed"]
    observability_failure: str | None
    artifact_dir: str
    started_at: str
    finished_at: str
    sample_interval_seconds: float
    readiness_timeout_seconds: float
    sample_count: int
    temporal_sample_count: int
    resource_sample_count: int
    tracecat_commit: str
    tracecat_commit_dirty: bool
    cluster_num: int
    public_api_url: str
    workspace_fingerprint: str
    ee_multi_tenant: bool
    compose_project_fingerprint: str | None
    temporal_target: str
    """SHA-256 fingerprint of the configured Temporal endpoint."""
    temporal_namespace: str
    """SHA-256 fingerprint of the configured Temporal namespace."""
    temporal_workflow_task_queues: tuple[str, ...]
    """SHA-256 fingerprints of the configured workflow task queues."""
    temporal_activity_task_queues: tuple[str, ...]
    """SHA-256 fingerprints of the configured activity task queues."""
    artifacts: dict[str, str]


class CollectorReady(TypedDict):
    """Target-bound readiness record consumed by the load runner."""

    run_id: str
    """SHA-256 fingerprint of the process-local run ID."""
    status: Literal["ready"]
    sampled_at: str
    cluster_num: int
    public_api_url: str
    workspace_fingerprint: str
    sample_count: int
    temporal_sample_count: int
    resource_sample_count: int


class RunnerComplete(TypedDict):
    """Runner lifecycle marker consumed by the collector."""

    run_id: str
    """SHA-256 fingerprint of the process-local run ID."""
    status: Literal["completed", "aborted"]
    completed_at: str


class MeasurementBoundary(TypedDict):
    """One side of the runner/collector measured-interval handshake."""

    run_id: str
    """SHA-256 fingerprint of the process-local run ID."""
    status: Literal["ready"]
    recorded_at: str


class ActivityMetricsHandoff(TypedDict):
    """Private runner-to-collector data deleted after history aggregation."""

    run_id: str
    """SHA-256 fingerprint of the process-local run ID."""
    measurement_window_seconds: float
    measurement_started_at: str
    measurement_finished_at: str
    workflow_execution_ids: list[str]
    workflow_execution_ids_complete: bool


@dataclass(slots=True, frozen=True)
class SdkMetricsEndpoint:
    """One load-test-only Temporal SDK Prometheus endpoint."""

    service: Literal["worker", "executor"]
    replica_index: int
    url: str


@dataclass(slots=True, frozen=True)
class DbPoolMetricsEndpoint:
    """One load-test-only SQLAlchemy pool-metrics endpoint."""

    service: Literal["api", "worker", "executor"]
    replica_index: int
    url: str


@dataclass(slots=True, frozen=True)
class CollectorConfig:
    """Metric collector configuration."""

    run_id: str
    workspace_id: str
    artifact_dir: str
    dsn: str
    sample_interval_seconds: float
    readiness_timeout_seconds: float
    cluster_num: int
    public_api_url: str
    compose_public_app_url: str
    compose_public_api_url: str
    ee_multi_tenant: bool
    compose_project: str
    compose_files: tuple[str, ...]
    log_services: tuple[str, ...]
    recovery_seconds: float
    temporal_target: str
    temporal_namespace: str
    temporal_workflow_task_queues: tuple[str, ...]
    temporal_activity_task_queues: tuple[str, ...]
    temporal_executor_task_queue: str | None = None
    execution_id_handoff_path: str | None = None
    temporal_sdk_metrics_endpoints: tuple[SdkMetricsEndpoint, ...] = ()
    db_pool_metrics_endpoints: tuple[DbPoolMetricsEndpoint, ...] = ()
    pgdog_metrics_url: str | None = None
    case_id: str | None = None
    settings_of_interest: tuple[str, ...] = field(
        default=(
            "max_connections",
            "superuser_reserved_connections",
            "shared_buffers",
            "work_mem",
            "maintenance_work_mem",
            "effective_cache_size",
            "max_worker_processes",
            "autovacuum",
            "autovacuum_naptime",
            "statement_timeout",
            "idle_in_transaction_session_timeout",
            "lock_timeout",
            "deadlock_timeout",
            "server_version",
        )
    )
