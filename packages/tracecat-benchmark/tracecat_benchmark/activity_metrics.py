"""Per-activity metrics for workflow load tests.

The measured interval uses two complementary sources:

* Temporal SDK Prometheus histograms are snapshotted immediately before and
  after measured load. Their deltas preserve retry-aware worker and queue
  timing without adding a high-frequency scrape to the benchmark.
* Temporal histories are read only after recovery. They provide authoritative
  logical completion outcomes and let Tracecat's generic
  ``execute_action_activity`` be grouped by the registry action in its input.

Only aggregate measurements are returned. Workflow execution IDs and activity
payloads never enter the resulting artifacts.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

import httpx
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import Client as TemporalClient

from .models import (
    ActivityMetricsHandoff,
    SdkMetricsEndpoint,
    deployment_value_fingerprint,
)

HISTORY_FETCH_CONCURRENCY: Final = 8
HISTORY_RPC_TIMEOUT_SECONDS: Final = 30.0
PROMETHEUS_TIMEOUT_SECONDS: Final = 5.0
PROMETHEUS_READINESS_TIMEOUT_SECONDS: Final = 30.0
EXECUTE_ACTION_ACTIVITY_TYPE: Final = "execute_action_activity"

SDK_ACTIVITY_COUNTERS: Final = frozenset(
    {
        "temporal_activity_execution_failed",
    }
)
SDK_ACTIVITY_HISTOGRAMS: Final = frozenset(
    {
        "temporal_activity_execution_latency",
        "temporal_activity_schedule_to_start_latency",
        "temporal_activity_succeed_endtoend_latency",
    }
)
SDK_ACTIVITY_METRICS: Final = SDK_ACTIVITY_COUNTERS | SDK_ACTIVITY_HISTOGRAMS

PROMETHEUS_SAMPLE_RE: Final = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|"
    r"[+-]Inf|NaN)"
    r"(?:\s+\d+)?$"
)
PROMETHEUS_LABEL_RE: Final = re.compile(
    r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)="'
    r'(?P<value>(?:\\.|[^"\\])*)"'
)
PROMETHEUS_SUFFIXES: Final = ("_bucket", "_count", "_sum", "_total")
SAFE_PROMETHEUS_LABELS: Final = frozenset(
    {
        "activity_type",
        "namespace",
        "operation",
        "poller_type",
        "task_queue",
        "worker_type",
        "workflow_type",
    }
)
FINGERPRINTED_PROMETHEUS_LABELS: Final = frozenset({"namespace", "task_queue"})

QueueRole = Literal["workflow", "executor", "other"]
ActivityOutcome = Literal["completed", "failed", "timed_out", "canceled"]


class ActivityMetricsCaptureError(RuntimeError):
    """Required Temporal history or SDK metric evidence could not be captured."""


class LatencyDistribution(TypedDict):
    """Exact percentiles over a set of completed history observations."""

    count: int
    p50_seconds: float | None
    p95_seconds: float | None
    p99_seconds: float | None
    minimum_seconds: float | None
    maximum_seconds: float | None


class ActivityGroupMetrics(TypedDict):
    """Aggregated logical activities for one queue/type/action tuple."""

    queue_role: QueueRole
    task_queue_fingerprint: str
    activity_type: str
    action_name: str | None
    scheduled: int
    started: int
    completed: int
    failed: int
    timed_out: int
    canceled: int
    open: int
    attempts: int
    retries: int
    input_decode_failures: int
    completed_per_second: float
    schedule_to_start_excluded_retried: int
    schedule_to_start: LatencyDistribution
    start_to_close: LatencyDistribution
    schedule_to_close: LatencyDistribution


class WorkflowHistorySizeMetrics(TypedDict):
    """Aggregate Temporal-reported workflow history sizes."""

    count: int
    total_bytes: int
    average_bytes: float | None
    maximum_bytes: int | None


class ActivityHistoryMetrics(TypedDict):
    """Post-recovery aggregate derived from raw Temporal histories."""

    schema_version: int
    source: Literal["temporal_history_post_recovery"]
    generated_at: str
    measurement_window_seconds: float
    measurement_started_at: str
    measurement_finished_at: str
    root_workflow_executions: int
    workflow_histories_fetched: int
    root_workflow_history_sizes: WorkflowHistorySizeMetrics
    workflow_history_sizes: WorkflowHistorySizeMetrics
    completed_activities: int
    completed_activities_per_second: float
    completed_tracecat_actions: int
    completed_tracecat_actions_per_second: float
    groups: list[ActivityGroupMetrics]
    notes: list[str]


class SdkHistogramDelta(TypedDict):
    """One baseline-to-final Temporal SDK histogram delta."""

    service: Literal["worker", "executor"]
    metric: str
    labels: dict[str, str]
    count: float
    sum_milliseconds: float
    rate_per_second: float
    p50_upper_bound_milliseconds: float | None
    p95_upper_bound_milliseconds: float | None
    p99_upper_bound_milliseconds: float | None
    counter_reset_detected: bool


class SdkCounterDelta(TypedDict):
    """One baseline-to-final Temporal SDK counter delta."""

    service: Literal["worker", "executor"]
    metric: str
    labels: dict[str, str]
    count: float
    rate_per_second: float
    counter_reset_detected: bool


class TemporalSdkMetrics(TypedDict):
    """Worker-side metrics isolated to the measured load interval."""

    schema_version: int
    source: Literal["temporal_sdk_prometheus_delta"]
    generated_at: str
    measurement_window_seconds: float
    duration_unit: Literal["milliseconds"]
    histograms: list[SdkHistogramDelta]
    counters: list[SdkCounterDelta]
    notes: list[str]


@dataclass(frozen=True, slots=True)
class WorkflowExecutionRef:
    """A workflow/run pair needed while recursively reading child histories."""

    workflow_id: str
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityGroupKey:
    """Stable grouping dimensions retained in the shareable artifact."""

    queue_role: QueueRole
    task_queue_fingerprint: str
    activity_type: str
    action_name: str | None


@dataclass(slots=True)
class ScheduledActivity:
    """One logical scheduled activity assembled from history events."""

    key: ActivityGroupKey
    scheduled_at: datetime
    input_decode_failed: bool
    started_at: datetime | None = None
    closed_at: datetime | None = None
    attempt: int = 0
    outcome: ActivityOutcome | None = None


@dataclass(frozen=True, slots=True)
class WorkflowHistoryRead:
    """One Temporal history read with its server-reported storage size."""

    activities: list[ScheduledActivity]
    children: list[WorkflowExecutionRef]
    history_size_bytes: int


@dataclass(slots=True)
class ActivityGroupAccumulator:
    """Mutable aggregate used while histories are folded."""

    scheduled: int = 0
    started: int = 0
    completed: int = 0
    failed: int = 0
    timed_out: int = 0
    canceled: int = 0
    attempts: int = 0
    retries: int = 0
    input_decode_failures: int = 0
    schedule_to_start_excluded_retried: int = 0
    schedule_to_start_seconds: list[float] = field(default_factory=list)
    start_to_close_seconds: list[float] = field(default_factory=list)
    schedule_to_close_seconds: list[float] = field(default_factory=list)

    def add(
        self,
        activity: ScheduledActivity,
        *,
        measurement_started_at: datetime,
        measurement_finished_at: datetime,
    ) -> None:
        if not (
            measurement_started_at <= activity.scheduled_at <= measurement_finished_at
        ):
            return

        started_at = (
            activity.started_at
            if activity.started_at is not None
            and activity.started_at <= measurement_finished_at
            else None
        )
        closed_at = (
            activity.closed_at
            if activity.closed_at is not None
            and activity.closed_at <= measurement_finished_at
            else None
        )
        outcome = activity.outcome if closed_at is not None else None

        self.scheduled += 1
        if started_at is not None:
            self.started += 1
        attempt = max(activity.attempt, 1) if started_at is not None else 0
        self.attempts += attempt
        self.retries += max(0, attempt - 1)
        if activity.input_decode_failed:
            self.input_decode_failures += 1

        match outcome:
            case "completed":
                self.completed += 1
            case "failed":
                self.failed += 1
            case "timed_out":
                self.timed_out += 1
            case "canceled":
                self.canceled += 1
            case None:
                pass

        if outcome == "completed" and started_at is not None and closed_at is not None:
            if attempt <= 1:
                self.schedule_to_start_seconds.append(
                    max(
                        0.0,
                        (started_at - activity.scheduled_at).total_seconds(),
                    )
                )
            else:
                # A history's scheduled timestamp precedes retry backoff, so it
                # is not the current attempt's true schedule-to-start value.
                self.schedule_to_start_excluded_retried += 1
            self.start_to_close_seconds.append(
                max(0.0, (closed_at - started_at).total_seconds())
            )
            self.schedule_to_close_seconds.append(
                max(0.0, (closed_at - activity.scheduled_at).total_seconds())
            )


@dataclass(frozen=True, slots=True)
class PrometheusSnapshot:
    """Selected monotonic SDK samples for one worker process."""

    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float]


@dataclass(slots=True)
class HistogramDeltaAccumulator:
    """Prometheus histogram components after baseline subtraction."""

    count: float = 0.0
    sum_value: float = 0.0
    buckets: dict[float, float] = field(default_factory=dict)
    counter_reset_detected: bool = False


@dataclass(slots=True)
class CounterDeltaAccumulator:
    """Prometheus counter delta summed after process-local subtraction."""

    count: float = 0.0
    counter_reset_detected: bool = False


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_measurement_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ActivityMetricsCaptureError(
            f"activity metrics handoff has an invalid {field_name}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ActivityMetricsCaptureError(
            f"activity metrics handoff has a naive {field_name}"
        )
    return parsed.astimezone(UTC)


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = min(
        len(sorted_values) - 1,
        int(round(fraction * (len(sorted_values) - 1))),
    )
    return sorted_values[index]


def _latency_distribution(values: list[float]) -> LatencyDistribution:
    ordered = sorted(values)
    return LatencyDistribution(
        count=len(ordered),
        p50_seconds=_percentile(ordered, 0.50),
        p95_seconds=_percentile(ordered, 0.95),
        p99_seconds=_percentile(ordered, 0.99),
        minimum_seconds=ordered[0] if ordered else None,
        maximum_seconds=ordered[-1] if ordered else None,
    )


def _event_time(event: HistoryEvent) -> datetime:
    return event.event_time.ToDatetime(UTC)


def _task_queue_role(
    task_queue: str,
    *,
    workflow_task_queue: str,
    executor_task_queue: str,
) -> QueueRole:
    if task_queue == executor_task_queue:
        return "executor"
    if task_queue == workflow_task_queue:
        return "workflow"
    return "other"


async def _decode_action_name(
    client: TemporalClient,
    event: HistoryEvent,
) -> tuple[str | None, bool]:
    attrs = event.activity_task_scheduled_event_attributes
    if not attrs.input.payloads:
        return None, False
    try:
        values = await client.data_converter.decode(attrs.input.payloads)
    except Exception:
        return None, True
    if not values:
        return None, False

    value = values[0]
    task: object | None = None
    if isinstance(value, dict):
        task = cast(dict[object, object], value).get("task")
    else:
        task = getattr(value, "task", None)

    action: object | None = None
    if isinstance(task, dict):
        action = cast(dict[object, object], task).get("action")
    elif task is not None:
        action = getattr(task, "action", None)
    return (action if isinstance(action, str) and action else None), False


def _close_event_source_and_outcome(
    event: HistoryEvent,
) -> tuple[int, ActivityOutcome] | None:
    match event.event_type:
        case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
            attrs = event.activity_task_completed_event_attributes
            return int(attrs.scheduled_event_id), "completed"
        case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
            attrs = event.activity_task_failed_event_attributes
            return int(attrs.scheduled_event_id), "failed"
        case EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
            attrs = event.activity_task_timed_out_event_attributes
            return int(attrs.scheduled_event_id), "timed_out"
        case EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED:
            attrs = event.activity_task_canceled_event_attributes
            return int(attrs.scheduled_event_id), "canceled"
        case _:
            return None


async def _read_workflow_history(
    client: TemporalClient,
    workflow_ref: WorkflowExecutionRef,
    *,
    workflow_task_queue: str,
    executor_task_queue: str,
    measurement_finished_at: datetime,
) -> WorkflowHistoryRead:
    handle = client.get_workflow_handle(
        workflow_ref.workflow_id,
        run_id=workflow_ref.run_id,
    )
    activities: dict[int, ScheduledActivity] = {}
    children: list[WorkflowExecutionRef] = []
    try:
        description = await handle.describe(
            rpc_timeout=timedelta(seconds=HISTORY_RPC_TIMEOUT_SECONDS)
        )
        history_size_bytes = description.raw_info.history_size_bytes
        if history_size_bytes < 0:
            raise ActivityMetricsCaptureError(
                "Temporal returned a negative workflow history size"
            )
        async for event in handle.fetch_history_events(
            rpc_timeout=timedelta(seconds=HISTORY_RPC_TIMEOUT_SECONDS)
        ):
            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                attrs = event.activity_task_scheduled_event_attributes
                task_queue = attrs.task_queue.name
                action_name, decode_failed = (
                    await _decode_action_name(client, event)
                    if attrs.activity_type.name == EXECUTE_ACTION_ACTIVITY_TYPE
                    else (None, False)
                )
                activities[int(event.event_id)] = ScheduledActivity(
                    key=ActivityGroupKey(
                        queue_role=_task_queue_role(
                            task_queue,
                            workflow_task_queue=workflow_task_queue,
                            executor_task_queue=executor_task_queue,
                        ),
                        task_queue_fingerprint=deployment_value_fingerprint(task_queue),
                        activity_type=attrs.activity_type.name,
                        action_name=action_name,
                    ),
                    scheduled_at=_event_time(event),
                    input_decode_failed=decode_failed,
                )
                continue

            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
                attrs = event.activity_task_started_event_attributes
                scheduled = activities.get(int(attrs.scheduled_event_id))
                if scheduled is not None:
                    scheduled.started_at = _event_time(event)
                    scheduled.attempt = max(scheduled.attempt, int(attrs.attempt))
                continue

            close = _close_event_source_and_outcome(event)
            if close is not None:
                scheduled_event_id, outcome = close
                scheduled = activities.get(scheduled_event_id)
                if scheduled is not None:
                    scheduled.closed_at = _event_time(event)
                    scheduled.outcome = outcome
                continue

            if (
                event.event_type
                == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED
            ):
                execution = event.child_workflow_execution_started_event_attributes.workflow_execution
                if (
                    execution.workflow_id
                    and _event_time(event) <= measurement_finished_at
                ):
                    children.append(
                        WorkflowExecutionRef(
                            workflow_id=execution.workflow_id,
                            run_id=execution.run_id or None,
                        )
                    )
    except Exception as exc:
        raise ActivityMetricsCaptureError(
            "Temporal history description or fetch failed for a measured workflow execution"
        ) from exc
    return WorkflowHistoryRead(
        activities=list(activities.values()),
        children=children,
        history_size_bytes=history_size_bytes,
    )


def _history_size_metrics(sizes: Iterable[int]) -> WorkflowHistorySizeMetrics:
    values = list(sizes)
    total_bytes = sum(values)
    return WorkflowHistorySizeMetrics(
        count=len(values),
        total_bytes=total_bytes,
        average_bytes=(total_bytes / len(values) if values else None),
        maximum_bytes=max(values, default=None),
    )


async def collect_activity_history_metrics(
    client: TemporalClient,
    handoff: ActivityMetricsHandoff,
    *,
    workflow_task_queue: str,
    executor_task_queue: str,
) -> ActivityHistoryMetrics:
    """Aggregate measured root and child histories after recovery."""
    measurement_window = float(handoff["measurement_window_seconds"])
    if not math.isfinite(measurement_window) or measurement_window <= 0:
        raise ActivityMetricsCaptureError(
            "activity metrics handoff has an invalid measurement window"
        )
    measurement_started_at = _parse_measurement_timestamp(
        handoff["measurement_started_at"],
        field_name="measurement start",
    )
    measurement_finished_at = _parse_measurement_timestamp(
        handoff["measurement_finished_at"],
        field_name="measurement finish",
    )
    if measurement_finished_at <= measurement_started_at:
        raise ActivityMetricsCaptureError(
            "activity metrics handoff has an invalid measurement interval"
        )

    roots = [
        WorkflowExecutionRef(workflow_id=workflow_id)
        for workflow_id in handoff["workflow_execution_ids"]
    ]
    root_refs = set(roots)
    pending = deque(roots)
    seen: set[WorkflowExecutionRef] = set()
    history_sizes: list[int] = []
    root_history_sizes: list[int] = []
    accumulators: defaultdict[ActivityGroupKey, ActivityGroupAccumulator] = defaultdict(
        ActivityGroupAccumulator
    )

    while pending:
        batch: list[WorkflowExecutionRef] = []
        while pending and len(batch) < HISTORY_FETCH_CONCURRENCY:
            candidate = pending.popleft()
            if candidate in seen:
                continue
            seen.add(candidate)
            batch.append(candidate)
        if not batch:
            continue
        histories = await asyncio.gather(
            *(
                _read_workflow_history(
                    client,
                    workflow_ref,
                    workflow_task_queue=workflow_task_queue,
                    executor_task_queue=executor_task_queue,
                    measurement_finished_at=measurement_finished_at,
                )
                for workflow_ref in batch
            )
        )
        for workflow_ref, history in zip(batch, histories, strict=True):
            history_sizes.append(history.history_size_bytes)
            if workflow_ref in root_refs:
                root_history_sizes.append(history.history_size_bytes)
            pending.extend(history.children)
            for activity in history.activities:
                accumulators[activity.key].add(
                    activity,
                    measurement_started_at=measurement_started_at,
                    measurement_finished_at=measurement_finished_at,
                )

    groups: list[ActivityGroupMetrics] = []
    for key, accumulator in sorted(
        accumulators.items(),
        key=lambda item: (
            item[0].queue_role,
            item[0].activity_type,
            item[0].action_name or "",
            item[0].task_queue_fingerprint,
        ),
    ):
        terminal = (
            accumulator.completed
            + accumulator.failed
            + accumulator.timed_out
            + accumulator.canceled
        )
        groups.append(
            ActivityGroupMetrics(
                queue_role=key.queue_role,
                task_queue_fingerprint=key.task_queue_fingerprint,
                activity_type=key.activity_type,
                action_name=key.action_name,
                scheduled=accumulator.scheduled,
                started=accumulator.started,
                completed=accumulator.completed,
                failed=accumulator.failed,
                timed_out=accumulator.timed_out,
                canceled=accumulator.canceled,
                open=max(0, accumulator.scheduled - terminal),
                attempts=accumulator.attempts,
                retries=accumulator.retries,
                input_decode_failures=accumulator.input_decode_failures,
                completed_per_second=(accumulator.completed / measurement_window),
                schedule_to_start_excluded_retried=(
                    accumulator.schedule_to_start_excluded_retried
                ),
                schedule_to_start=_latency_distribution(
                    accumulator.schedule_to_start_seconds
                ),
                start_to_close=_latency_distribution(
                    accumulator.start_to_close_seconds
                ),
                schedule_to_close=_latency_distribution(
                    accumulator.schedule_to_close_seconds
                ),
            )
        )

    completed_activities = sum(group["completed"] for group in groups)
    completed_actions = sum(
        group["completed"] for group in groups if group["action_name"] is not None
    )
    return ActivityHistoryMetrics(
        schema_version=2,
        source="temporal_history_post_recovery",
        generated_at=_utc_now_iso(),
        measurement_window_seconds=measurement_window,
        measurement_started_at=measurement_started_at.isoformat(),
        measurement_finished_at=measurement_finished_at.isoformat(),
        root_workflow_executions=len(roots),
        workflow_histories_fetched=len(seen),
        root_workflow_history_sizes=_history_size_metrics(root_history_sizes),
        workflow_history_sizes=_history_size_metrics(history_sizes),
        completed_activities=completed_activities,
        completed_activities_per_second=completed_activities / measurement_window,
        completed_tracecat_actions=completed_actions,
        completed_tracecat_actions_per_second=(completed_actions / measurement_window),
        groups=groups,
        notes=[
            "Counts and timestamps come from activities scheduled during the measured interval in measured root workflows and their child histories.",
            "A terminal event after the measured interval is treated as open and is excluded from throughput and latency distributions.",
            "Schedule-to-start history percentiles exclude retried activities because the original schedule timestamp includes retry backoff.",
            "Successful-completion start-to-close uses the final history-observed attempt; schedule-to-close includes queueing and retry backoff.",
            "Workflow history sizes come directly from Temporal DescribeWorkflowExecution after recovery; only aggregate byte counts are retained.",
        ],
    )


def _unescape_prometheus_label(value: str) -> str:
    unescaped: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character != "\\":
            unescaped.append(character)
            position += 1
            continue
        position += 1
        if position >= len(value):
            raise ActivityMetricsCaptureError(
                "Temporal SDK endpoint returned an invalid Prometheus label escape"
            )
        escaped = value[position]
        match escaped:
            case "\\":
                unescaped.append("\\")
            case '"':
                unescaped.append('"')
            case "n":
                unescaped.append("\n")
            case _:
                raise ActivityMetricsCaptureError(
                    "Temporal SDK endpoint returned an invalid Prometheus label escape"
                )
        position += 1
    return "".join(unescaped)


def _parse_prometheus_labels(raw_labels: str) -> dict[str, str]:
    if not raw_labels:
        return {}
    labels: dict[str, str] = {}
    position = 0
    while position < len(raw_labels):
        match = PROMETHEUS_LABEL_RE.match(raw_labels, position)
        if match is None:
            raise ActivityMetricsCaptureError(
                "Temporal SDK endpoint returned invalid Prometheus labels"
            )
        labels[match.group("name")] = _unescape_prometheus_label(match.group("value"))
        position = match.end()
        if position == len(raw_labels):
            break
        if raw_labels[position] != ",":
            raise ActivityMetricsCaptureError(
                "Temporal SDK endpoint returned invalid Prometheus labels"
            )
        position += 1
    return labels


def _base_metric_name(metric: str) -> str:
    for suffix in PROMETHEUS_SUFFIXES:
        if metric.endswith(suffix):
            candidate = metric[: -len(suffix)]
            if candidate in SDK_ACTIVITY_METRICS:
                return candidate
    return metric


def parse_temporal_sdk_metrics(text: str) -> PrometheusSnapshot:
    """Parse only the documented Temporal activity metric families."""
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PROMETHEUS_SAMPLE_RE.fullmatch(line)
        if match is None:
            continue
        metric = match.group("name")
        if _base_metric_name(metric) not in SDK_ACTIVITY_METRICS:
            continue
        labels = _parse_prometheus_labels(match.group("labels") or "")
        try:
            value = float(match.group("value"))
        except ValueError as exc:
            raise ActivityMetricsCaptureError(
                "Temporal SDK endpoint returned an invalid metric value"
            ) from exc
        if math.isnan(value):
            continue
        key = (metric, tuple(sorted(labels.items())))
        samples[key] = value
    return PrometheusSnapshot(samples=samples)


def _sanitize_prometheus_labels(
    labels: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for name, value in labels:
        if name in SAFE_PROMETHEUS_LABELS:
            sanitized[name] = (
                deployment_value_fingerprint(value)
                if name in FINGERPRINTED_PROMETHEUS_LABELS
                else value
            )
    return sanitized


def _subtract_monotonic(final: float, baseline: float) -> tuple[float, bool]:
    if final >= baseline:
        return final - baseline, False
    # A worker restart resets its process-local counters. Preserve the visible
    # post-restart value and make the loss explicit in the report.
    return max(0.0, final), True


def _histogram_upper_bound(
    buckets: dict[float, float],
    count: float,
    fraction: float,
) -> float | None:
    if count <= 0 or not buckets:
        return None
    target = count * fraction
    for upper_bound, cumulative_count in sorted(buckets.items()):
        if cumulative_count >= target:
            return upper_bound if math.isfinite(upper_bound) else None
    return None


def _labels_without_bucket(
    labels: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    return tuple((name, value) for name, value in labels if name != "le")


def build_temporal_sdk_metrics(
    baseline: dict[SdkMetricsEndpoint, PrometheusSnapshot],
    final: dict[SdkMetricsEndpoint, PrometheusSnapshot],
    *,
    measurement_window_seconds: float,
) -> TemporalSdkMetrics:
    """Build counter and histogram deltas for the measured interval."""
    if not math.isfinite(measurement_window_seconds) or measurement_window_seconds <= 0:
        raise ActivityMetricsCaptureError(
            "Temporal SDK metrics have an invalid measurement window"
        )

    histograms: dict[
        tuple[str, str, tuple[tuple[str, str], ...]],
        HistogramDeltaAccumulator,
    ] = {}
    counter_accumulators: dict[
        tuple[str, str, tuple[tuple[str, str], ...]],
        CounterDeltaAccumulator,
    ] = {}
    for endpoint, final_snapshot in final.items():
        baseline_samples = baseline.get(
            endpoint, PrometheusSnapshot(samples={})
        ).samples
        for (metric, labels), final_value in final_snapshot.samples.items():
            base_metric = _base_metric_name(metric)
            baseline_value = baseline_samples.get((metric, labels), 0.0)
            delta, reset = _subtract_monotonic(final_value, baseline_value)
            if base_metric in SDK_ACTIVITY_COUNTERS:
                if metric.endswith("_total") or metric == base_metric:
                    key = (endpoint.service, base_metric, labels)
                    accumulator = counter_accumulators.setdefault(
                        key, CounterDeltaAccumulator()
                    )
                    accumulator.count += delta
                    accumulator.counter_reset_detected |= reset
                continue
            if base_metric not in SDK_ACTIVITY_HISTOGRAMS:
                continue

            group_labels = _labels_without_bucket(labels)
            key = (endpoint.service, base_metric, group_labels)
            accumulator = histograms.setdefault(key, HistogramDeltaAccumulator())
            accumulator.counter_reset_detected |= reset
            if metric.endswith("_bucket"):
                raw_bound = dict(labels).get("le")
                if raw_bound is None:
                    continue
                try:
                    upper_bound = float(raw_bound)
                except ValueError:
                    continue
                accumulator.buckets[upper_bound] = (
                    accumulator.buckets.get(upper_bound, 0.0) + delta
                )
            elif metric.endswith("_count"):
                accumulator.count += delta
            elif metric.endswith("_sum"):
                accumulator.sum_value += delta

    counters = [
        SdkCounterDelta(
            service=cast(Literal["worker", "executor"], service),
            metric=metric,
            labels=_sanitize_prometheus_labels(labels),
            count=accumulator.count,
            rate_per_second=accumulator.count / measurement_window_seconds,
            counter_reset_detected=accumulator.counter_reset_detected,
        )
        for (service, metric, labels), accumulator in sorted(
            counter_accumulators.items()
        )
    ]

    histogram_rows = [
        SdkHistogramDelta(
            service=cast(Literal["worker", "executor"], service),
            metric=metric,
            labels=_sanitize_prometheus_labels(labels),
            count=accumulator.count,
            sum_milliseconds=accumulator.sum_value,
            rate_per_second=accumulator.count / measurement_window_seconds,
            p50_upper_bound_milliseconds=_histogram_upper_bound(
                accumulator.buckets, accumulator.count, 0.50
            ),
            p95_upper_bound_milliseconds=_histogram_upper_bound(
                accumulator.buckets, accumulator.count, 0.95
            ),
            p99_upper_bound_milliseconds=_histogram_upper_bound(
                accumulator.buckets, accumulator.count, 0.99
            ),
            counter_reset_detected=accumulator.counter_reset_detected,
        )
        for (service, metric, labels), accumulator in sorted(histograms.items())
    ]
    counters.sort(
        key=lambda row: (
            row["service"],
            row["metric"],
            tuple(sorted(row["labels"].items())),
        )
    )
    return TemporalSdkMetrics(
        schema_version=1,
        source="temporal_sdk_prometheus_delta",
        generated_at=_utc_now_iso(),
        measurement_window_seconds=measurement_window_seconds,
        duration_unit="milliseconds",
        histograms=histogram_rows,
        counters=counters,
        notes=[
            "The baseline is captured after warm-up and before measured submissions.",
            "The final snapshot is captured immediately after measured load and before recovery or history queries.",
            "Deltas sum all configured process replicas for each service during the measured interval.",
            "Schedule-to-start is a task-queue metric and is intentionally not attributed to a Tracecat action name.",
            "Histogram percentiles are reported as observed bucket upper bounds.",
        ],
    )


class TemporalSdkMetricsCapture:
    """Brackets the measured interval with low-overhead Prometheus snapshots."""

    def __init__(self, endpoints: tuple[SdkMetricsEndpoint, ...]) -> None:
        self._endpoints = endpoints
        self._baseline: dict[SdkMetricsEndpoint, PrometheusSnapshot] | None = None
        self._final: dict[SdkMetricsEndpoint, PrometheusSnapshot] | None = None

    async def _fetch_endpoint(
        self,
        endpoint: SdkMetricsEndpoint,
    ) -> tuple[SdkMetricsEndpoint, PrometheusSnapshot]:
        try:
            async with httpx.AsyncClient(timeout=PROMETHEUS_TIMEOUT_SECONDS) as client:
                response = await client.get(endpoint.url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ActivityMetricsCaptureError(
                f"Temporal SDK metrics endpoint for {endpoint.service} is unavailable"
            ) from exc
        return endpoint, parse_temporal_sdk_metrics(response.text)

    async def validate(
        self,
        *,
        timeout_seconds: float = PROMETHEUS_READINESS_TIMEOUT_SECONDS,
    ) -> None:
        """Require every configured endpoint to answer before releasing the runner."""
        if not self._endpoints:
            return
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ActivityMetricsCaptureError(
                "Temporal SDK metrics readiness timeout must be finite and positive"
            )
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            try:
                await asyncio.gather(
                    *(self._fetch_endpoint(endpoint) for endpoint in self._endpoints)
                )
            except ActivityMetricsCaptureError as exc:
                if asyncio.get_running_loop().time() >= deadline:
                    raise ActivityMetricsCaptureError(
                        "Temporal SDK metrics endpoints did not become ready"
                    ) from exc
                await asyncio.sleep(0.25)
            else:
                return
        raise ActivityMetricsCaptureError(
            "Temporal SDK metrics endpoints did not become ready"
        )

    async def capture_baseline(self) -> None:
        if not self._endpoints:
            self._baseline = {}
            return
        snapshots = await asyncio.gather(
            *(self._fetch_endpoint(endpoint) for endpoint in self._endpoints)
        )
        self._baseline = dict(snapshots)

    async def capture_final(self) -> None:
        if not self._endpoints:
            self._final = {}
            return
        snapshots = await asyncio.gather(
            *(self._fetch_endpoint(endpoint) for endpoint in self._endpoints)
        )
        self._final = dict(snapshots)

    async def capture_delta(
        self,
        *,
        measurement_window_seconds: float,
    ) -> TemporalSdkMetrics:
        if self._baseline is None:
            raise ActivityMetricsCaptureError(
                "Temporal SDK metric baseline was not captured"
            )
        if self._final is None:
            raise ActivityMetricsCaptureError(
                "Temporal SDK final metric snapshot was not captured"
            )
        return build_temporal_sdk_metrics(
            self._baseline,
            self._final,
            measurement_window_seconds=measurement_window_seconds,
        )


def load_activity_metrics_handoff(path: str) -> ActivityMetricsHandoff:
    """Load and validate the private runner-to-collector handoff."""
    try:
        payload: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActivityMetricsCaptureError(
            "activity metrics handoff is missing or invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise ActivityMetricsCaptureError(
            "activity metrics handoff must contain a JSON object"
        )
    raw = cast(dict[object, object], payload)
    run_id = raw.get("run_id")
    window = raw.get("measurement_window_seconds")
    measurement_started_at = raw.get("measurement_started_at")
    measurement_finished_at = raw.get("measurement_finished_at")
    workflow_ids = raw.get("workflow_execution_ids")
    workflow_ids_complete = raw.get("workflow_execution_ids_complete")
    if (
        not isinstance(run_id, str)
        or not isinstance(window, int | float)
        or isinstance(window, bool)
        or not isinstance(measurement_started_at, str)
        or not isinstance(measurement_finished_at, str)
        or not isinstance(workflow_ids, list)
        or not all(isinstance(value, str) and value for value in workflow_ids)
        or not isinstance(workflow_ids_complete, bool)
    ):
        raise ActivityMetricsCaptureError("activity metrics handoff has invalid fields")
    try:
        measurement_window = float(window)
    except (OverflowError, ValueError) as exc:
        raise ActivityMetricsCaptureError(
            "activity metrics handoff has an invalid measurement window"
        ) from exc
    if not math.isfinite(measurement_window) or measurement_window <= 0:
        raise ActivityMetricsCaptureError(
            "activity metrics handoff has an invalid measurement window"
        )
    started_at = _parse_measurement_timestamp(
        measurement_started_at,
        field_name="measurement start",
    )
    finished_at = _parse_measurement_timestamp(
        measurement_finished_at,
        field_name="measurement finish",
    )
    if finished_at <= started_at:
        raise ActivityMetricsCaptureError(
            "activity metrics handoff has an invalid measurement interval"
        )
    return ActivityMetricsHandoff(
        run_id=run_id,
        measurement_window_seconds=measurement_window,
        measurement_started_at=measurement_started_at,
        measurement_finished_at=measurement_finished_at,
        workflow_execution_ids=cast(list[str], workflow_ids),
        workflow_execution_ids_complete=workflow_ids_complete,
    )
