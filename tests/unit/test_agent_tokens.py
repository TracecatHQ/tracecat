from __future__ import annotations

import uuid

from tracecat import config
from tracecat.agent.tokens import mint_mcp_token, verify_mcp_token
from tracecat.registry.lock.types import RegistryLock


def test_mcp_token_round_trips_parent_agent_workflow_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        allowed_actions=["core.http_request"],
        session_id=session_id,
        parent_agent_workflow_id=f"agent/{session_id}",
        parent_agent_run_id="run-123",
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={"core.http_request": "tracecat_registry"},
        ),
    )

    claims = verify_mcp_token(token)
    assert claims.workspace_id == workspace_id
    assert claims.organization_id == organization_id
    assert claims.session_id == session_id
    assert claims.user_id == user_id
    assert claims.parent_agent_workflow_id == f"agent/{session_id}"
    assert claims.parent_agent_run_id == "run-123"
    assert claims.registry_lock == RegistryLock(
        origins={"tracecat_registry": "test-version"},
        actions={"core.http_request": "tracecat_registry"},
    )
