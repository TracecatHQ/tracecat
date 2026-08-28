"""Durable workflow for the case-agent interaction backfill."""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from tracecat.cases.agent_sessions.types import CaseAgentSessionBackfillReport


@workflow.defn
class CaseAgentSessionBackfillWorkflow:
    """Run the restart-safe backfill outside the HTTP request lifecycle."""

    @workflow.run
    async def run(self) -> CaseAgentSessionBackfillReport:
        return await workflow.execute_activity(
            case_agent_session_backfill_activity,
            start_to_close_timeout=timedelta(days=1),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@activity.defn
async def case_agent_session_backfill_activity() -> CaseAgentSessionBackfillReport:
    """Run the backfill with an unrestricted system database session."""
    from tracecat.cases.agent_sessions.backfill import CaseAgentSessionBackfill
    from tracecat.db.engine import get_async_session_bypass_rls_context_manager

    def heartbeat() -> None:
        activity.heartbeat()

    async with get_async_session_bypass_rls_context_manager() as session:
        return await CaseAgentSessionBackfill(session).run(on_batch_complete=heartbeat)
