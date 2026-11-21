"""Read Temporal histories for a burst replay run and print per-run timings.

Runs inside a Tracecat DSL worker pod, fed on stdin, so it uses the pod's own
Temporal client and namespace and no credential leaves the cluster:

    kubectl exec -i deploy/tracecat-worker -- env BURST_WORKSPACES=... \
        BURST_SINCE=... BURST_UNTIL=... python - < pod_collect.py

Read-only: lists workflow executions by workspace and start time and fetches
their histories. Payloads are never decoded. Prints one ``RUN {json}`` line per
execution with server timestamps (epoch seconds) from the history:

- ``started``: WorkflowExecutionStarted (the API's accepted start).
- ``pacing``: the start's first-workflow-task backoff (start pacing delay).
- ``first_wft``: the first WorkflowTaskStarted.
- ``acts``: executor-queue activities as
  ``[activity_id, scheduled, started, closed, attempt, outcome]``.

Environment:
    BURST_WORKSPACES: ``label=workspace_id`` pairs, comma separated.
    BURST_SINCE, BURST_UNTIL: RFC 3339 UTC bounds on the start time.
    BURST_TYPES: workflow types (default DSLWorkflow,ExecuteRegistryToolWorkflow).
    BURST_CONCURRENCY: histories fetched at once (default 8).
    BURST_MAX_RUNS: stop after this many executions per workspace (default 5000).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from tracecat import config
from tracecat.dsl.client import get_temporal_client

CLOSE_EVENTS = {
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED: "COMPLETED",
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED: "FAILED",
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT: "TIMED_OUT",
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED: "CANCELED",
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED: "TERMINATED",
    EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW: "CONTINUED_AS_NEW",
}
ACTIVITY_CLOSE_EVENTS = {
    EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED: "completed",
    EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED: "failed",
    EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT: "timed_out",
    EventType.EVENT_TYPE_ACTIVITY_TASK_CANCELED: "canceled",
}


def _ts(event: HistoryEvent) -> float:
    return event.event_time.ToMicroseconds() / 1e6


def summarize(events: list[HistoryEvent], executor_queue: str) -> dict[str, object]:
    """Reduce one history to the timings the report needs."""
    started = pacing = first_wft = closed = None
    status = "RUNNING"
    failure = None
    signals = timers = 0
    scheduled: dict[int, list[object]] = {}
    for event in events:
        etype = event.event_type
        if etype == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
            started = _ts(event)
            attrs = event.workflow_execution_started_event_attributes
            pacing = attrs.first_workflow_task_backoff.ToMicroseconds() / 1e6
        elif etype == EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED and first_wft is None:
            first_wft = _ts(event)
        elif etype == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED:
            signals += 1
        elif etype == EventType.EVENT_TYPE_TIMER_STARTED:
            timers += 1
        elif etype == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            attrs = event.activity_task_scheduled_event_attributes
            if attrs.task_queue.name == executor_queue:
                scheduled[event.event_id] = [
                    attrs.activity_id,
                    _ts(event),
                    None,
                    None,
                    0,
                    None,
                ]
        elif etype == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
            attrs = event.activity_task_started_event_attributes
            if (act := scheduled.get(attrs.scheduled_event_id)) is not None:
                act[2] = _ts(event)
                act[4] = attrs.attempt
        elif (outcome := ACTIVITY_CLOSE_EVENTS.get(etype)) is not None:
            match outcome:
                case "completed":
                    sched_id = event.activity_task_completed_event_attributes.scheduled_event_id
                case "failed":
                    sched_id = (
                        event.activity_task_failed_event_attributes.scheduled_event_id
                    )
                case "timed_out":
                    sched_id = event.activity_task_timed_out_event_attributes.scheduled_event_id
                case _:
                    sched_id = (
                        event.activity_task_canceled_event_attributes.scheduled_event_id
                    )
            if (act := scheduled.get(sched_id)) is not None:
                act[3] = _ts(event)
                act[5] = outcome
        elif (close := CLOSE_EVENTS.get(etype)) is not None:
            status = close
            closed = _ts(event)
            if etype == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                failure = (
                    event.workflow_execution_failed_event_attributes.failure.message[
                        :300
                    ]
                )
    return {
        "status": status,
        "failure": failure,
        "events": len(events),
        "started": started,
        "pacing": pacing,
        "first_wft": first_wft,
        "closed": closed,
        "signals": signals,
        "timers": timers,
        "acts": list(scheduled.values()),
    }


async def main() -> None:
    workspaces = dict(
        pair.split("=", 1) for pair in os.environ["BURST_WORKSPACES"].split(",") if pair
    )
    since = os.environ["BURST_SINCE"]
    until = os.environ["BURST_UNTIL"]
    types = os.environ.get("BURST_TYPES") or "DSLWorkflow,ExecuteRegistryToolWorkflow"
    concurrency = int(os.environ.get("BURST_CONCURRENCY") or 8)
    max_runs = int(os.environ.get("BURST_MAX_RUNS") or 5000)
    type_list = ", ".join(f"'{t}'" for t in types.split(","))
    executor_queue = config.TRACECAT__EXECUTOR_QUEUE

    client = await get_temporal_client()
    sem = asyncio.Semaphore(concurrency)

    async def one(label: str, wf_type: str, workflow_id: str, run_id: str) -> None:
        async with sem:
            try:
                handle = client.get_workflow_handle(workflow_id, run_id=run_id)
                history = await handle.fetch_history()
                row = summarize(list(history.events), executor_queue)
            except Exception as e:  # report and keep going
                row = {"error": f"{type(e).__name__}: {e}"[:300]}
            row |= {
                "org": label,
                "type": wf_type,
                "workflow_id": workflow_id,
                "run_id": run_id,
            }
            print("RUN " + json.dumps(row), flush=True)

    for label, workspace_id in workspaces.items():
        query = (
            f"TracecatWorkspaceId = '{workspace_id}' AND WorkflowType IN ({type_list})"
            f" AND StartTime >= '{since}' AND StartTime <= '{until}'"
        )
        tasks: list[asyncio.Task[None]] = []
        async for execution in client.list_workflows(query):
            if len(tasks) >= max_runs:
                print(
                    f"WARN {label}: stopped at {max_runs} executions", file=sys.stderr
                )
                break
            tasks.append(
                asyncio.create_task(
                    one(label, execution.workflow_type, execution.id, execution.run_id)
                )
            )
        await asyncio.gather(*tasks)
        print(f"DONE {label} {len(tasks)}", flush=True)


asyncio.run(main())
