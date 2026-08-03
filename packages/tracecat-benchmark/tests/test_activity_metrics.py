from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
import respx
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.common.v1 import (
    ActivityType,
    Payloads,
    WorkflowExecution,
)
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import (
    ActivityTaskCompletedEventAttributes,
    ActivityTaskScheduledEventAttributes,
    ActivityTaskStartedEventAttributes,
    ChildWorkflowExecutionStartedEventAttributes,
    HistoryEvent,
)
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.client import Client as TemporalClient
from tracecat_benchmark.activity_metrics import (
    ActivityMetricsCaptureError,
    TemporalSdkMetricsCapture,
    build_temporal_sdk_metrics,
    collect_activity_history_metrics,
    parse_temporal_sdk_metrics,
)
from tracecat_benchmark.models import (
    ActivityMetricsHandoff,
    SdkMetricsEndpoint,
    deployment_value_fingerprint,
    run_id_fingerprint,
)
from tracecat_benchmark.runner import (
    _synchronize_measurement_baseline,
    _synchronize_measurement_complete,
)

from tracecat.dsl._converter import get_data_converter


def _timestamp(offset_seconds: float) -> Timestamp:
    value = Timestamp()
    value.FromDatetime(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds)
    )
    return value


async def _scheduled_activity(
    event_id: int,
    offset_seconds: float,
    *,
    task_queue: str,
    action_name: str,
) -> HistoryEvent:
    payloads = await get_data_converter().encode([{"task": {"action": action_name}}])
    return HistoryEvent(
        event_id=event_id,
        event_time=_timestamp(offset_seconds),
        event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,
        activity_task_scheduled_event_attributes=(
            ActivityTaskScheduledEventAttributes(
                activity_id=f"activity-{event_id}",
                activity_type=ActivityType(name="execute_action_activity"),
                task_queue=TaskQueue(name=task_queue),
                input=Payloads(payloads=payloads),
            )
        ),
    )


def _started_activity(
    event_id: int,
    offset_seconds: float,
    *,
    scheduled_event_id: int,
    attempt: int,
) -> HistoryEvent:
    return HistoryEvent(
        event_id=event_id,
        event_time=_timestamp(offset_seconds),
        event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED,
        activity_task_started_event_attributes=ActivityTaskStartedEventAttributes(
            scheduled_event_id=scheduled_event_id,
            attempt=attempt,
        ),
    )


def _completed_activity(
    event_id: int,
    offset_seconds: float,
    *,
    scheduled_event_id: int,
    started_event_id: int,
) -> HistoryEvent:
    return HistoryEvent(
        event_id=event_id,
        event_time=_timestamp(offset_seconds),
        event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED,
        activity_task_completed_event_attributes=(
            ActivityTaskCompletedEventAttributes(
                scheduled_event_id=scheduled_event_id,
                started_event_id=started_event_id,
            )
        ),
    )


@dataclass(frozen=True, slots=True)
class _FakeWorkflowInfo:
    history_size_bytes: int


@dataclass(frozen=True, slots=True)
class _FakeWorkflowDescription:
    raw_info: _FakeWorkflowInfo


@dataclass(frozen=True, slots=True)
class _FakeHistory:
    events: list[HistoryEvent]
    size_bytes: int


class _FakeHistoryHandle:
    def __init__(self, history: _FakeHistory) -> None:
        self._history = history

    async def describe(
        self,
        *,
        rpc_timeout: timedelta | None = None,
    ) -> _FakeWorkflowDescription:
        del rpc_timeout
        return _FakeWorkflowDescription(
            raw_info=_FakeWorkflowInfo(
                history_size_bytes=self._history.size_bytes,
            )
        )

    async def fetch_history_events(
        self,
        *,
        rpc_timeout: timedelta | None = None,
    ):
        del rpc_timeout
        for event in self._history.events:
            yield event


class _FakeTemporalClient:
    def __init__(self, histories: dict[tuple[str, str | None], _FakeHistory]):
        self.data_converter = get_data_converter()
        self._histories = histories

    def get_workflow_handle(
        self,
        workflow_id: str,
        *,
        run_id: str | None = None,
    ) -> _FakeHistoryHandle:
        return _FakeHistoryHandle(self._histories[(workflow_id, run_id)])


def test_history_metrics_group_actions_and_follow_child_workflows() -> None:
    async def exercise():
        root_events = [
            await _scheduled_activity(
                1,
                0,
                task_queue="executor-queue",
                action_name="core.table.insert_row",
            ),
            _started_activity(2, 1, scheduled_event_id=1, attempt=1),
            _completed_activity(3, 3, scheduled_event_id=1, started_event_id=2),
            HistoryEvent(
                event_id=4,
                event_time=_timestamp(4),
                event_type=(EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED),
                child_workflow_execution_started_event_attributes=(
                    ChildWorkflowExecutionStartedEventAttributes(
                        workflow_execution=WorkflowExecution(
                            workflow_id="child-workflow",
                            run_id="child-run",
                        )
                    )
                ),
            ),
        ]
        child_events = [
            await _scheduled_activity(
                1,
                4,
                task_queue="executor-queue",
                action_name="core.table.insert_row",
            ),
            _started_activity(2, 8, scheduled_event_id=1, attempt=2),
            _completed_activity(3, 9, scheduled_event_id=1, started_event_id=2),
            await _scheduled_activity(
                4,
                9,
                task_queue="executor-queue",
                action_name="core.table.insert_row",
            ),
            _started_activity(5, 9.5, scheduled_event_id=4, attempt=1),
            _completed_activity(6, 11, scheduled_event_id=4, started_event_id=5),
            await _scheduled_activity(
                7,
                11,
                task_queue="executor-queue",
                action_name="core.table.insert_row",
            ),
        ]
        client = cast(
            TemporalClient,
            _FakeTemporalClient(
                {
                    ("root-workflow", None): _FakeHistory(
                        events=root_events,
                        size_bytes=4_096,
                    ),
                    ("child-workflow", "child-run"): _FakeHistory(
                        events=child_events,
                        size_bytes=8_192,
                    ),
                }
            ),
        )
        handoff = ActivityMetricsHandoff(
            run_id=run_id_fingerprint("run-1"),
            measurement_window_seconds=10.0,
            measurement_started_at="2026-01-01T00:00:00+00:00",
            measurement_finished_at="2026-01-01T00:00:10+00:00",
            workflow_execution_ids=["root-workflow"],
            workflow_execution_ids_complete=True,
        )
        return await collect_activity_history_metrics(
            client,
            handoff,
            workflow_task_queue="workflow-queue",
            executor_task_queue="executor-queue",
        )

    report = asyncio.run(exercise())

    assert report["workflow_histories_fetched"] == 2
    assert report["root_workflow_history_sizes"] == {
        "count": 1,
        "total_bytes": 4_096,
        "average_bytes": 4_096.0,
        "maximum_bytes": 4_096,
    }
    assert report["workflow_history_sizes"] == {
        "count": 2,
        "total_bytes": 12_288,
        "average_bytes": 6_144.0,
        "maximum_bytes": 8_192,
    }
    assert report["completed_tracecat_actions"] == 2
    assert report["completed_tracecat_actions_per_second"] == 0.2
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["action_name"] == "core.table.insert_row"
    assert group["activity_type"] == "execute_action_activity"
    assert group["queue_role"] == "executor"
    assert group["task_queue_fingerprint"] == deployment_value_fingerprint(
        "executor-queue"
    )
    assert group["scheduled"] == 3
    assert group["started"] == 3
    assert group["completed"] == 2
    assert group["open"] == 1
    assert group["attempts"] == 4
    assert group["retries"] == 1
    assert group["schedule_to_start"]["count"] == 1
    assert group["schedule_to_start"]["p95_seconds"] == 1.0
    assert group["schedule_to_start_excluded_retried"] == 1
    assert group["start_to_close"]["p95_seconds"] == 2.0
    assert group["schedule_to_close"]["p95_seconds"] == 5.0


def test_sdk_metric_delta_reports_retry_aware_histograms_without_raw_queues() -> None:
    labels = 'activity_type="execute_action_activity",namespace="default",task_queue="executor-queue"'
    baseline = parse_temporal_sdk_metrics(
        "\n".join(
            [
                f'temporal_activity_succeed_endtoend_latency_bucket{{{labels},le="10"}} 2',
                f'temporal_activity_succeed_endtoend_latency_bucket{{{labels},le="100"}} 2',
                f'temporal_activity_succeed_endtoend_latency_bucket{{{labels},le="+Inf"}} 2',
                f"temporal_activity_succeed_endtoend_latency_count{{{labels}}} 2",
                f"temporal_activity_succeed_endtoend_latency_sum{{{labels}}} 20",
                f"temporal_activity_execution_failed{{{labels}}} 1",
            ]
        )
    )
    final = parse_temporal_sdk_metrics(
        "\n".join(
            [
                f'temporal_activity_succeed_endtoend_latency_bucket{{{labels},le="10"}} 3',
                f'temporal_activity_succeed_endtoend_latency_bucket{{{labels},le="100"}} 6',
                f'temporal_activity_succeed_endtoend_latency_bucket{{{labels},le="+Inf"}} 6',
                f"temporal_activity_succeed_endtoend_latency_count{{{labels}}} 6",
                f"temporal_activity_succeed_endtoend_latency_sum{{{labels}}} 260",
                f"temporal_activity_execution_failed{{{labels}}} 3",
            ]
        )
    )

    report = build_temporal_sdk_metrics(
        {SdkMetricsEndpoint("executor", 1, "http://executor-1/metrics"): baseline},
        {SdkMetricsEndpoint("executor", 1, "http://executor-1/metrics"): final},
        measurement_window_seconds=10.0,
    )

    histogram = report["histograms"][0]
    assert histogram["count"] == 4
    assert histogram["rate_per_second"] == 0.4
    assert histogram["sum_milliseconds"] == 240
    assert histogram["p50_upper_bound_milliseconds"] == 100
    assert histogram["p95_upper_bound_milliseconds"] == 100
    assert report["counters"][0]["count"] == 2
    serialized = json.dumps(report)
    assert "executor-queue" not in serialized
    assert "default" not in serialized
    assert deployment_value_fingerprint("executor-queue") in serialized


def test_sdk_metric_deltas_subtract_before_summing_scaled_replicas() -> None:
    first_baseline = parse_temporal_sdk_metrics(
        'temporal_activity_execution_failed{activity_type="execute_action_activity"} 100'
    )
    first_final = parse_temporal_sdk_metrics(
        'temporal_activity_execution_failed{activity_type="execute_action_activity"} 20'
    )
    second_baseline = parse_temporal_sdk_metrics(
        'temporal_activity_execution_failed{activity_type="execute_action_activity"} 100'
    )
    second_final = parse_temporal_sdk_metrics(
        'temporal_activity_execution_failed{activity_type="execute_action_activity"} 190'
    )
    first_endpoint = SdkMetricsEndpoint("executor", 1, "http://executor-1/metrics")
    second_endpoint = SdkMetricsEndpoint("executor", 2, "http://executor-2/metrics")

    report = build_temporal_sdk_metrics(
        {
            first_endpoint: first_baseline,
            second_endpoint: second_baseline,
        },
        {
            first_endpoint: first_final,
            second_endpoint: second_final,
        },
        measurement_window_seconds=10.0,
    )

    assert len(report["counters"]) == 1
    assert report["counters"][0]["count"] == 110
    assert report["counters"][0]["counter_reset_detected"] is True


def test_sdk_metric_delta_rejects_series_lost_during_measurement() -> None:
    endpoint = SdkMetricsEndpoint("executor", 1, "http://executor-1/metrics")
    baseline = parse_temporal_sdk_metrics(
        'temporal_activity_execution_failed{activity_type="execute_action_activity"} 3'
    )
    final = parse_temporal_sdk_metrics("")

    with pytest.raises(ActivityMetricsCaptureError, match="series.*disappeared"):
        build_temporal_sdk_metrics(
            {endpoint: baseline},
            {endpoint: final},
            measurement_window_seconds=10.0,
        )


def test_sdk_metric_final_rejects_http_200_without_supported_series() -> None:
    endpoint = SdkMetricsEndpoint("executor", 1, "http://executor-1/metrics")
    capture = TemporalSdkMetricsCapture((endpoint,))

    with respx.mock:
        respx.get(endpoint.url).mock(
            return_value=httpx.Response(200, text="process_cpu_seconds_total 1")
        )
        with pytest.raises(
            ActivityMetricsCaptureError,
            match="returned no supported benchmark metric series",
        ):
            asyncio.run(capture.capture_final())


def test_measurement_boundary_waits_for_collector_acknowledgement(
    tmp_path: Path,
) -> None:
    async def exercise(
        artifact_dir: Path,
        synchronize: Callable[[Path, str, float], Coroutine[object, object, None]],
        request_filename: str,
        acknowledgement_filename: str,
    ) -> None:
        waiter = asyncio.create_task(synchronize(artifact_dir, "run-1", 1.0))
        request_path = artifact_dir / request_filename
        while not request_path.is_file():
            await asyncio.sleep(0)
        request = json.loads(request_path.read_text())
        (artifact_dir / acknowledgement_filename).write_text(
            json.dumps(
                {
                    "run_id": request["run_id"],
                    "status": "ready",
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                }
            )
        )
        await waiter

    for (
        synchronize,
        request_filename,
        acknowledgement_filename,
    ) in (
        (
            _synchronize_measurement_baseline,
            ".runner_measurement_ready.json",
            ".collector_measurement_ready.json",
        ),
        (
            _synchronize_measurement_complete,
            ".runner_measurement_complete.json",
            ".collector_measurement_complete.json",
        ),
    ):
        artifact_dir = tmp_path / request_filename
        artifact_dir.mkdir()
        asyncio.run(
            exercise(
                artifact_dir,
                synchronize,
                request_filename,
                acknowledgement_filename,
            )
        )
