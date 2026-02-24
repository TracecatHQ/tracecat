"""Reproduce Temporal workflow deadlock caused by blocking process logger calls.

This script runs two workflows in the Temporal local test environment:
1) Legacy path: workflow code calls the process Loguru logger directly.
2) Safe path: workflow code calls tracecat.dsl.workflow_logging.workflow_logger.

A blocking Loguru sink is installed to emulate logger lock contention. The
legacy path should fail with TMPRL1101 (workflow didn't yield), while the safe
path should still complete.

Run:
    uv run python scripts/temporal/repro_workflow_logging_deadlock.py
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from enum import StrEnum

from temporalio import workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from tracecat.dsl.worker import new_sandbox_runner
    from tracecat.dsl.workflow_logging import workflow_logger
    from tracecat.logger import logger as process_logger


class Mode(StrEnum):
    LEGACY = "legacy"
    SAFE = "safe"
    BOTH = "both"


DEADLOCK_MARKERS = ("TMPRL1101", "Potential deadlock", "didn't yield")


@workflow.defn
class LegacyProcessLoggerWorkflow:
    @workflow.run
    async def run(self) -> str:
        process_logger.info("legacy process logger call from workflow")
        await asyncio.sleep(0)
        return "legacy-ok"


@workflow.defn
class SafeWorkflowLoggerWorkflow:
    @workflow.run
    async def run(self) -> str:
        workflow_logger.info("workflow-safe logger call from workflow")
        await asyncio.sleep(0)
        return "safe-ok"


class _BlockFirstSink:
    def __init__(self, *, block_seconds: float, max_blocks: int = 1) -> None:
        self._block_seconds = block_seconds
        self._remaining_blocks = max_blocks

    def __call__(self, _message: object) -> None:
        if self._remaining_blocks <= 0:
            return
        self._remaining_blocks -= 1
        time.sleep(self._block_seconds)


async def _execute(
    env: WorkflowEnvironment,
    *,
    task_queue: str,
    workflow_cls: type[LegacyProcessLoggerWorkflow] | type[SafeWorkflowLoggerWorkflow],
) -> tuple[str, bool]:
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[workflow_cls],
        workflow_runner=new_sandbox_runner(),
    ):
        handle = await env.client.start_workflow(
            workflow_cls.run,
            id=f"deadlock-repro-{workflow_cls.__name__}-{uuid.uuid4()}",
            task_queue=task_queue,
        )
        result = await handle.result()
        has_deadlock = False
        async for event in handle.fetch_history_events():
            if event.event_type != EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED:
                continue
            failure = event.workflow_task_failed_event_attributes.failure
            msg = getattr(failure, "message", "")
            stack = getattr(failure, "stack_trace", "")
            failure_text = f"{msg}\n{stack}"
            if any(marker in failure_text for marker in DEADLOCK_MARKERS):
                has_deadlock = True
                break
        return result, has_deadlock


async def run_repro(mode: Mode, block_seconds: float) -> int:
    # Install a blocking sink to emulate logger contention in workflow threads.
    blocking_sink = _BlockFirstSink(block_seconds=block_seconds, max_blocks=1)
    sink_id = process_logger.add(
        blocking_sink,
        level="INFO",
        format="{message}",
        colorize=False,
    )
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            if mode in (Mode.LEGACY, Mode.BOTH):
                try:
                    result, had_deadlock = await _execute(
                        env,
                        task_queue="deadlock-repro-legacy",
                        workflow_cls=LegacyProcessLoggerWorkflow,
                    )
                except WorkflowFailureError as exc:
                    print("LEGACY: workflow failed unexpectedly")
                    print(str(exc))
                    return 1
                if not had_deadlock:
                    print("LEGACY: workflow completed, but no TMPRL1101 was found")
                    return 1
                print(
                    "LEGACY: completed and reproduced TMPRL1101 in task history",
                    f"(result={result!r})",
                )

            if mode in (Mode.SAFE, Mode.BOTH):
                try:
                    result, had_deadlock = await _execute(
                        env,
                        task_queue="deadlock-repro-safe",
                        workflow_cls=SafeWorkflowLoggerWorkflow,
                    )
                except WorkflowFailureError as exc:
                    print("SAFE: workflow failed unexpectedly")
                    print(str(exc))
                    return 1
                if had_deadlock:
                    print("SAFE: workflow completed, but TMPRL1101 was still observed")
                    return 1
                print("SAFE: completed successfully with no TMPRL1101", f"(result={result!r})")
    finally:
        process_logger.remove(sink_id)

    print("Repro complete.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reproduce Temporal workflow logger deadlock behavior locally."
    )
    parser.add_argument(
        "--mode",
        type=Mode,
        default=Mode.BOTH,
        choices=list(Mode),
        help="Which path to run: legacy, safe, or both (default).",
    )
    parser.add_argument(
        "--block-seconds",
        type=float,
        default=3.0,
        help="How long to block each process logger emission (default: 3.0).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(run_repro(mode=args.mode, block_seconds=args.block_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
