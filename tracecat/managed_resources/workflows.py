"""Durable execution of rollout phases on the background Temporal queue.

Each phase of each rollout record runs as its own workflow, so a slow or
failing target never holds up the others, and a worker restart resumes the
phase instead of losing it. The activity is idempotent: the engine skips a
record that is no longer in the state the phase expects.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Protocol

from temporalio import activity, workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from tracecat import config
    from tracecat.db.engine import get_async_session_bypass_rls_context_manager
    from tracecat.dsl.client import get_temporal_client
    from tracecat.managed_resources.rollout import ManagedRolloutEngine

type RolloutPhase = Literal["preview", "apply"]


@dataclass(frozen=True, slots=True)
class RolloutRef:
    """One phase of one rollout record; contents never enter workflow history."""

    rollout_id: uuid.UUID
    phase: RolloutPhase


class RolloutRunner(Protocol):
    """Starts rollout phases. Tests substitute an inline runner."""

    async def start(self, rollout_id: uuid.UUID, phase: RolloutPhase) -> None:
        """Start ``phase`` for one rollout record."""
        ...


class TemporalRolloutRunner:
    """Start each rollout phase as a background Temporal workflow."""

    async def start(self, rollout_id: uuid.UUID, phase: RolloutPhase) -> None:
        client = await get_temporal_client()
        try:
            await client.start_workflow(
                ManagedRolloutWorkflow.run,
                RolloutRef(rollout_id, phase),
                id=f"managed-rollout/{rollout_id}/{phase}",
                task_queue=config.TRACECAT__BACKGROUND_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=timedelta(seconds=5),
            )
        except WorkflowAlreadyStartedError:
            # A retried request after a start whose reply was lost: the phase
            # is already running, which is what the caller asked for.
            return


@activity.defn
async def managed_rollout_run(ref: RolloutRef) -> None:
    """Run one rollout phase against an RLS-bypass session."""
    async with get_async_session_bypass_rls_context_manager() as session:
        engine = ManagedRolloutEngine(session)
        if ref.phase == "preview":
            await engine.preview(ref.rollout_id)
        else:
            await engine.apply(ref.rollout_id)


@activity.defn
async def managed_rollout_fail(ref: RolloutRef) -> None:
    """Record a phase that exhausted its retries as failed."""
    async with get_async_session_bypass_rls_context_manager() as session:
        await ManagedRolloutEngine(session).mark_failed(
            ref.rollout_id, "The rollout could not finish; preview again"
        )


@workflow.defn
class ManagedRolloutWorkflow:
    """Run one rollout phase with retries, then record a final failure."""

    @workflow.run
    async def run(self, ref: RolloutRef) -> None:
        try:
            await workflow.execute_activity(
                managed_rollout_run,
                ref,
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    maximum_interval=timedelta(minutes=1),
                    maximum_attempts=3,
                ),
            )
        except ActivityError:
            await workflow.execute_activity(
                managed_rollout_fail,
                ref,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
