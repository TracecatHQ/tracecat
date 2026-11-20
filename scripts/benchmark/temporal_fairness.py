"""Measure Temporal task queue fairness between orgs on a local dev server.

Spike for ENG-2605. One activity worker with 16 slots stands in for one
Tracecat executor replica. Each synthetic org runs driver workflows started
with ``Priority(fairness_key=<org>)``, the same shape as
``tracecat.temporal.fairness.org_priority``; the activities they schedule
inherit the key and set only ``priority_key`` (1 for tool calls, like
``AGENT_TOOL_PRIORITY``; 3, the default, for workflow actions).

Usage (server started separately with ``temporal server start-dev``):

    python scripts/benchmark/temporal_fairness.py --address localhost:17633 \
        --label off --out results-off.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import Priority
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

WORKFLOW_QUEUE = "fairness-spike-wf"
ACTIVITY_QUEUE = "fairness-spike-act"
EXECUTOR_SLOTS = 16
ACTION_PRIORITY = 3
TOOL_PRIORITY = 1


@dataclass(frozen=True, slots=True)
class HoldInput:
    scenario: str
    org: str
    kind: str
    hold_seconds: float


@dataclass(frozen=True, slots=True)
class DriverInput:
    scenario: str
    org: str
    kind: str
    count: int
    interval_seconds: float
    hold_seconds: float
    priority_key: int


@dataclass(frozen=True, slots=True)
class Sample:
    scenario: str
    org: str
    kind: str
    scheduled_at: float
    schedule_to_start: float


@dataclass(slots=True)
class Collector:
    samples: list[Sample] = field(default_factory=list)


COLLECTOR = Collector()


@activity.defn
async def hold(input: HoldInput) -> None:
    info = activity.info()
    scheduled = info.current_attempt_scheduled_time
    COLLECTOR.samples.append(
        Sample(
            scenario=input.scenario,
            org=input.org,
            kind=input.kind,
            scheduled_at=scheduled.timestamp(),
            schedule_to_start=(info.started_time - scheduled).total_seconds(),
        )
    )
    await asyncio.sleep(input.hold_seconds)


@workflow.defn
class OrgDriver:
    """Schedule ``count`` activities, ``interval_seconds`` apart (0 = all at once)."""

    @workflow.run
    async def run(self, input: DriverInput) -> None:
        pending: list[asyncio.Future[None]] = []
        for i in range(input.count):
            if i and input.interval_seconds:
                await workflow.sleep(input.interval_seconds)
            pending.append(
                workflow.start_activity(
                    hold,
                    HoldInput(
                        scenario=input.scenario,
                        org=input.org,
                        kind=input.kind,
                        hold_seconds=input.hold_seconds,
                    ),
                    task_queue=ACTIVITY_QUEUE,
                    start_to_close_timeout=timedelta(minutes=5),
                    # Explicit priority key only; the fairness key is inherited.
                    priority=Priority(priority_key=input.priority_key),
                )
            )
        await asyncio.gather(*pending)


def scenarios() -> dict[str, list[tuple[float, DriverInput]]]:
    """Return each scenario as (start delay in seconds, driver input) pairs."""

    def drv(
        scenario: str,
        org: str,
        kind: str,
        count: int,
        interval: float,
        hold_s: float,
        priority_key: int,
    ) -> DriverInput:
        return DriverInput(scenario, org, kind, count, interval, hold_s, priority_key)

    s1, s2, s3 = "s1-cross-org", "s2-tools-under-action-flood", "s3-tool-flood"
    return {
        s1: [
            (0.0, drv(s1, "noisy", "action", 600, 0, 2.0, ACTION_PRIORITY)),
            (2.0, drv(s1, "quiet-a", "action", 30, 2.0, 2.0, ACTION_PRIORITY)),
            (2.0, drv(s1, "quiet-b", "action", 30, 2.0, 2.0, ACTION_PRIORITY)),
        ],
        s2: [
            (0.0, drv(s2, "noisy", "action", 600, 0, 2.0, ACTION_PRIORITY)),
            (2.0, drv(s2, "quiet-a", "tool", 30, 2.0, 1.0, TOOL_PRIORITY)),
        ],
        s3: [
            (0.0, drv(s3, "noisy", "tool", 300, 0, 1.0, TOOL_PRIORITY)),
            (2.0, drv(s3, "quiet-a", "tool", 10, 1.0, 1.0, TOOL_PRIORITY)),
            (2.0, drv(s3, "quiet-a", "action", 10, 1.0, 2.0, ACTION_PRIORITY)),
        ],
    }


async def run_scenario(
    client: Client, name: str, drivers: list[tuple[float, DriverInput]]
) -> None:
    async def start(delay: float, input: DriverInput) -> None:
        await asyncio.sleep(delay)
        await client.execute_workflow(
            OrgDriver.run,
            input,
            id=f"fairness-spike/{name}/{input.org}/{input.kind}/{uuid.uuid4()}",
            task_queue=WORKFLOW_QUEUE,
            # Same shape as tracecat.temporal.fairness.org_priority(org_id).
            priority=Priority(fairness_key=input.org),
        )

    await asyncio.gather(*(start(delay, input) for delay, input in drivers))


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarize(samples: list[Sample]) -> list[dict[str, str | int | float]]:
    groups: dict[tuple[str, str, str], list[float]] = {}
    for s in samples:
        groups.setdefault((s.scenario, s.org, s.kind), []).append(s.schedule_to_start)
    rows: list[dict[str, str | int | float]] = []
    for (scenario, org, kind), waits in sorted(groups.items()):
        rows.append(
            {
                "scenario": scenario,
                "org": org,
                "kind": kind,
                "n": len(waits),
                "p50_s": round(statistics.median(waits), 3),
                "p95_s": round(percentile(waits, 95), 3),
                "max_s": round(max(waits), 3),
            }
        )
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", help="Run one scenario by name")
    args = parser.parse_args()

    client = await Client.connect(args.address)
    selected = {
        name: drivers
        for name, drivers in scenarios().items()
        if args.only is None or name == args.only
    }
    async with (
        Worker(
            client,
            task_queue=WORKFLOW_QUEUE,
            workflows=[OrgDriver],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(
            client,
            task_queue=ACTIVITY_QUEUE,
            activities=[hold],
            max_concurrent_activities=EXECUTOR_SLOTS,
        ),
    ):
        for name, drivers in selected.items():
            print(f"[{args.label}] running {name}", flush=True)
            await run_scenario(client, name, drivers)

    rows = summarize(COLLECTOR.samples)
    args.out.write_text(
        json.dumps(
            {
                "label": args.label,
                "summary": rows,
                "samples": [asdict(s) for s in COLLECTOR.samples],
            },
            indent=2,
        )
    )
    for row in rows:
        print(json.dumps({"label": args.label, **row}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
