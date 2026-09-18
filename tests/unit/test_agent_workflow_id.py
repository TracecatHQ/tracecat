"""Core identity remains compatible with durable workflow IDs and serialization."""

from uuid import UUID

from pydantic import TypeAdapter
from tracecat_ee.agent.types import AgentWorkflowID

from tracecat.agent.workflow_id import agent_workflow_id


def test_shared_identity_preserves_existing_durable_contract():
    run_id = UUID("00000000-0000-4000-8000-000000000001")
    expected = "agent/00000000-0000-4000-8000-000000000001"
    assert agent_workflow_id(run_id) == expected
    assert AgentWorkflowID(run_id) == expected
    assert AgentWorkflowID.from_workflow_id(expected).session_id == run_id
    assert AgentWorkflowID.extract_id(expected) == run_id


def test_durable_identity_preserves_python_validation_and_json_serialization():
    run_id = UUID("00000000-0000-4000-8000-000000000001")
    adapter = TypeAdapter(AgentWorkflowID)
    identity = adapter.validate_python(agent_workflow_id(run_id))
    assert identity.session_id == run_id
    assert (
        adapter.dump_json(identity) == b'"agent/00000000-0000-4000-8000-000000000001"'
    )
