from __future__ import annotations

from uuid import UUID

AGENT_SESSION_CORRELATION_KIND = "agent-session"
CASE_TASK_CORRELATION_KIND = "case-task"


def build_tracecat_correlation_id(kind: str, value: UUID | str) -> str:
    """Build a namespaced Tracecat workflow correlation ID."""
    return f"{kind}:{value}"


def build_agent_session_correlation_id(session_id: UUID) -> str:
    """Build the shared correlation ID for workflows in one agent session."""
    return build_tracecat_correlation_id(AGENT_SESSION_CORRELATION_KIND, session_id)


def build_case_task_correlation_id(task_id: UUID) -> str:
    """Build the correlation ID for workflow runs started from a case task."""
    return build_tracecat_correlation_id(CASE_TASK_CORRELATION_KIND, task_id)
