from __future__ import annotations

from datetime import timedelta

from temporalio import workflow


@workflow.defn(name="audit-persist-workflow")
class AuditPersistWorkflow:
    """Workflow that persists a batch of audit events."""

    @workflow.run
    async def run(self, events: list[dict]) -> None:
        await workflow.execute_activity(
            "audit_persist_activity",
            events,
            start_to_close_timeout=timedelta(minutes=5),
        )
