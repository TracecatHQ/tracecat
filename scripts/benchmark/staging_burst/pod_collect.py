"""Read Temporal histories for a burst replay run and print per-run timings.

Runs inside a Tracecat DSL worker pod, fed on stdin, so it uses the pod's own
Temporal client and namespace and no credential leaves the cluster. Run it
through ``burst.py collect``, which ships the copy at a reviewed git ref.

Read-only: lists workflow executions by workspace and start time and fetches
their histories. To tell a "not admitted" bounce from a real run, it decodes
(and so decrypts) each executor action's result in the pod with the server's
own helper; only the outcome label leaves the pod. Failure messages are passed
through the server's redaction (``EventFailure.sanitize_error_text``) before
they're printed. Prints one ``RUN {json}`` line per execution with server
timestamps (epoch seconds) from the history:

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
import uuid
from datetime import UTC, datetime

from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from tracecat import config
from tracecat.dsl.client import get_temporal_client
from tracecat.workflow.executions.common import (
    get_not_admitted,
    schedules_executor_action,
)
from tracecat.workflow.executions.schemas import EventFailure

WORKFLOW_TYPES = {"DSLWorkflow", "ExecuteRegistryToolWorkflow"}

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


async def summarize(
    events: list[HistoryEvent], executor_queue: str
) -> dict[str, object]:
    """Reduce one history to the timings the report needs.

    An executor action that answered "not admitted" (the org was at its
    limit and nothing ran) gets the outcome ``not_admitted``.
    """
    started = pacing = first_wft = closed = None
    status = "RUNNING"
    failure = None
    signals = timers = 0
    scheduled: dict[int, list[object]] = {}
    actions: set[int] = set()
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
                if schedules_executor_action(event):
                    actions.add(event.event_id)
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
                if sched_id in actions and await get_not_admitted(event) is not None:
                    act[5] = "not_admitted"
        elif (close := CLOSE_EVENTS.get(etype)) is not None:
            status = close
            closed = _ts(event)
            if etype == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                # Redact the whole message first: a cut could split a secret.
                redacted = EventFailure.sanitize_error_text(
                    event.workflow_execution_failed_event_attributes.failure.message
                )
                failure = redacted[:300] if redacted else None
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


def _label(value: str) -> str:
    """A workspace label: printed on protocol lines, so letters, digits, - and _ only."""
    if not value or not all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
        sys.exit(f"invalid workspace label: {value!r}")
    return value


def _rfc3339(value: str) -> str:
    """Parse a timestamp and re-render it as RFC 3339 UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def main() -> None:
    # Every value below is spliced into a visibility query, so parse each one
    # into its type first: a quote in any of them can't widen the query.
    workspaces = {
        _label(label): str(uuid.UUID(workspace_id))
        for label, workspace_id in (
            pair.split("=", 1)
            for pair in os.environ["BURST_WORKSPACES"].split(",")
            if pair
        )
    }
    since = _rfc3339(os.environ["BURST_SINCE"])
    until = _rfc3339(os.environ["BURST_UNTIL"])
    types = (os.environ.get("BURST_TYPES") or ",".join(sorted(WORKFLOW_TYPES))).split(
        ","
    )
    if unknown := set(types) - WORKFLOW_TYPES:
        sys.exit(f"unknown BURST_TYPES: {sorted(unknown)}")
    concurrency = int(os.environ.get("BURST_CONCURRENCY") or 8)
    max_runs = int(os.environ.get("BURST_MAX_RUNS") or 5000)
    type_list = ", ".join(f"'{t}'" for t in types)
    executor_queue = config.TRACECAT__EXECUTOR_QUEUE

    client = await get_temporal_client()
    sem = asyncio.Semaphore(concurrency)

    async def one(label: str, wf_type: str, workflow_id: str, run_id: str) -> None:
        async with sem:
            try:
                handle = client.get_workflow_handle(workflow_id, run_id=run_id)
                history = await handle.fetch_history()
                row = await summarize(list(history.events), executor_queue)
            except Exception as e:  # report and keep going
                message = EventFailure.sanitize_error_text(f"{type(e).__name__}: {e}")
                row = {"error": (message or "")[:300]}
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
                # The caller treats this as a failed collection: raise
                # BURST_MAX_RUNS rather than report on a partial window.
                print(f"TRUNCATED {label} {max_runs}", flush=True)
                break
            tasks.append(
                asyncio.create_task(
                    one(label, execution.workflow_type, execution.id, execution.run_id)
                )
            )
        await asyncio.gather(*tasks)
        print(f"DONE {label} {len(tasks)}", flush=True)


asyncio.run(main())
