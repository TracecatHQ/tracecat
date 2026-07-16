from temporalio.common import Priority

from tracecat.agent.priority import (
    INTERACTIVE_AGENT_WORKFLOW_PRIORITY,
    resolve_interactive_agent_workflow_priority,
)


def test_interactive_agent_workflow_priority_requires_cluster_support() -> None:
    assert resolve_interactive_agent_workflow_priority(enabled=False) == Priority()


def test_interactive_agent_workflow_priority_enabled() -> None:
    assert (
        resolve_interactive_agent_workflow_priority(enabled=True)
        == INTERACTIVE_AGENT_WORKFLOW_PRIORITY
    )
