"""Synthetic run context for agent execution paths outside the workflow engine."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from tracecat.dsl.schemas import RunContext
from tracecat.identifiers import WorkflowUUID
from tracecat.identifiers.workflow import ExecutionUUID


def build_agent_run_context(
    *,
    environment: str,
    workflow_id: UUID | None = None,
    run_id: UUID | None = None,
    execution_id: UUID | None = None,
    logical_time: datetime | None = None,
) -> RunContext:
    """Build a synthetic RunContext pinned to an agent run's environment."""
    wf_id = WorkflowUUID.from_uuid(workflow_id or uuid4())
    return RunContext(
        wf_id=wf_id,
        wf_run_id=run_id or uuid4(),
        wf_exec_id=f"{wf_id.short()}/{ExecutionUUID.from_uuid(execution_id or uuid4()).short()}",
        environment=environment,
        logical_time=logical_time or datetime.now(UTC),
    )
