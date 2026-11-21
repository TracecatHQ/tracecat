import uuid

from temporalio.common import Priority

from tracecat.agent.workflows.tool_execution import AGENT_TOOL_PRIORITY
from tracecat.temporal.fairness import org_priority


def test_org_priority_sets_org_fairness_key() -> None:
    org_id = uuid.uuid4()

    priority = org_priority(org_id)

    assert priority.fairness_key == str(org_id)
    assert priority.priority_key is None
    assert priority.fairness_weight is None


def test_org_priority_keeps_base_priority_key() -> None:
    org_id = uuid.uuid4()

    priority = org_priority(org_id, AGENT_TOOL_PRIORITY)

    assert priority == Priority(priority_key=1, fairness_key=str(org_id))
    assert AGENT_TOOL_PRIORITY.fairness_key is None


def test_org_priority_without_org_returns_base() -> None:
    assert org_priority(None, AGENT_TOOL_PRIORITY) is AGENT_TOOL_PRIORITY
