from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession, Workspace


@pytest.mark.anyio
@pytest.mark.integration
async def test_workflow_fork_persists_independent_child_session(
    session: AsyncSession,
    svc_workspace: Workspace,
) -> None:
    workflow_role = Role(
        type="service",
        service_id="tracecat-runner",
        organization_id=svc_workspace.organization_id,
        workspace_id=svc_workspace.id,
        scopes=frozenset({"agent:execute"}),
    )
    parent = AgentSession(
        id=uuid.uuid4(),
        workspace_id=svc_workspace.id,
        title="Parent session",
        created_by=None,
        entity_type=AgentSessionEntity.WORKFLOW.value,
        entity_id=uuid.uuid4(),
        tools=["core.transform.reshape"],
        harness_type="claude_code",
        work_dir_snapshot={"bucket": "agent-workspaces", "key": "parent.tar.gz"},
    )
    session.add(parent)
    await session.commit()

    child_id = uuid.uuid4()
    service = AgentSessionService(session, workflow_role)
    child, created = await service.get_or_create_session(
        AgentSessionCreate(
            id=child_id,
            title="Workflow fork",
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            tools=["core.http_request"],
        ),
        parent_session_id=parent.id,
    )

    assert created is True
    assert child.id == child_id
    assert child.id != parent.id
    assert child.parent_session_id == parent.id
    assert child.harness_type == parent.harness_type
    assert child.work_dir_snapshot == parent.work_dir_snapshot
    assert child.tools == ["core.http_request"]
    # The child owns a copy: mutating it must not leak into the parent, whose
    # in-memory row is still live because the session does not expire on commit.
    assert child.work_dir_snapshot is not None
    child.work_dir_snapshot["key"] = "child.tar.gz"
    assert parent.work_dir_snapshot == {
        "bucket": "agent-workspaces",
        "key": "parent.tar.gz",
    }

    persisted_child = await service.get_session(child_id)
    assert persisted_child is not None
    assert persisted_child.parent_session_id == parent.id
    assert await service.get_session(parent.id) is not None
