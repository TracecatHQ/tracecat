from __future__ import annotations

import uuid

import pytest
from pydantic import TypeAdapter, ValidationError
from tracecat_ee.agent.types import AgentWorkflowID


def test_agent_workflow_id_json_schema_accepts_uuid_shape() -> None:
    session_id = uuid.UUID("00000000-0000-0000-0000-000000000003")

    value = TypeAdapter(AgentWorkflowID).validate_json(f'"agent/{session_id}"')

    assert value == f"agent/{session_id}"


def test_agent_workflow_id_json_schema_rejects_literal_quantifier_shape() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AgentWorkflowID).validate_json('"agent/a8-b4-c4-d4-e12"')
