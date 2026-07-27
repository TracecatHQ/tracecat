"""Typed models shared by the PostgreSQL scatter load-test runner and collector.

See scripts/benchmark/postgres-scatter-load-test-plan.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypedDict

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


class WritePath(StrEnum):
    """Which fixture workflow the scenario drives."""

    SCATTER = "scatter"
    """Adversarial: one core.table.insert_row action execution per branch."""

    BULK = "bulk"
    """Control: one core.table.insert_rows action execution per workflow."""


class FailureMode(StrEnum):
    """Primary failure classification from the load runner's vantage point.

    The plan's full classification also includes modes the runner cannot see
    (pool timeout, PostgreSQL connection-slot exhaustion, statement/lock
    timeout, Temporal schedule-to-start delay, executor saturation, container
    OOM). Those are attributed during analysis by correlating these records
    with the collector's PostgreSQL samples and service logs.

    Classification here is derived only from structured signals - the HTTP
    status code of the submission and the terminal workflow status - never
    from error-message text.
    """

    NONE = "none"
    ADMISSION_REJECTED = "admission_rejected"
    """Submission returned a non-2xx status."""

    SUBMIT_TRANSPORT_ERROR = "submit_transport_error"
    """Submission never produced an HTTP status (connect/read/pool error)."""

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

    write_path: WritePath
    path: str
    title: str


@dataclass(slots=True, frozen=True)
class FixtureHandles:
    """Identifiers produced by fixture setup, reused across matrix cells."""

    workspace_id: str
    table_id: str
    table_name: str
    unique_index_column: str
    workflow_ids: dict[WritePath, str]


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
    """The exact scenario configuration, recorded verbatim in the artifacts.

    `poll_interval_seconds` is deliberately part of the scenario config: the
    plan requires it to be identical across all phases so the baseline
    measurement absorbs the per-poll auth and membership query cost.
    """

    run_id: str
    base_url: str
    workspace_id: str
    write_path: WritePath
    workflow_count: int
    branch_count: int
    ramp_seconds: float
    steady_state_seconds: float
    payload_bytes: int
    run_timeout_seconds: float
    poll_interval_seconds: float
    warmup: bool
    submit_concurrency: int
    max_connections: int
    artifact_dir: str
    tracecat_commit: str
    started_at: str
    auth_mode: Literal["password", "api_key"]
    auth_email: str
    abort_stops_polling: bool

    @property
    def expected_rows(self) -> int:
        """Unique logical rows a fully successful run should produce."""
        return self.workflow_count * self.branch_count


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
    failure_mode: FailureMode = FailureMode.NONE
    poll_count: int = 0
    detail: str | None = None
    """Raw server detail, recorded for analysis only. Never branched on."""

    @property
    def accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def latency_seconds(self) -> float | None:
        if self.accepted_at is None or self.terminal_at is None:
            return None
        return self.terminal_at - self.accepted_at


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


class ContainerState(TypedDict):
    """Docker inspect state for one container in the cluster under test."""

    name: str
    service: str
    image: str
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

    schema_name: str
    table_name: str
    total_rows: int
    distinct_dedupe_keys: int
    duplicate_dedupe_keys: int
    rows_for_run: int
    distinct_dedupe_keys_for_run: int


class CollectorManifest(TypedDict):
    """Index of everything the collector wrote for a run."""

    run_id: str
    artifact_dir: str
    started_at: str
    finished_at: str
    sample_interval_seconds: float
    sample_count: int
    tracecat_commit: str
    tracecat_commit_dirty: bool
    compose_project: str
    artifacts: dict[str, str]


@dataclass(slots=True, frozen=True)
class CollectorConfig:
    """Metric collector configuration."""

    run_id: str
    artifact_dir: str
    dsn: str
    sample_interval_seconds: float
    compose_project: str
    compose_files: tuple[str, ...]
    log_services: tuple[str, ...]
    log_tail: int
    table_name: str
    duration_seconds: float | None
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
