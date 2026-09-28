from __future__ import annotations

import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.agent.session.service import AGENT_SESSION_EXECUTION_SCOPES
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.cases.agent_invocations.activities import (
    prepare_comment_agent_invocation_activity,
)
from tracecat.cases.agent_invocations.schemas import (
    PrepareCommentAgentInvocationInput,
)
from tracecat.cases.agent_invocations.types import PreparedCommentAgentSession


@pytest.mark.anyio
async def test_prepare_comment_invocation_uses_session_execution_role() -> None:
    invocation_id = uuid.uuid4()
    session_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    actor_role = Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"case:update", "agent:execute"}),
    )
    execution_role = actor_role.model_copy(
        update={
            "scopes": (actor_role.scopes or frozenset())
            | AGENT_SESSION_EXECUTION_SCOPES
        }
    )
    prepared_session = PreparedCommentAgentSession(
        invocation_id=invocation_id,
        session_id=session_id,
        prompt="Investigate this thread",
        display_messages=("@Agent investigate",),
    )
    prepared_turn = SimpleNamespace(
        prompt=prepared_session.prompt,
        session_id=session_id,
        run_id=uuid.uuid4(),
        active_stream_id=uuid.uuid4(),
        config=AgentConfig(
            model_name="test-model",
            model_provider="openai",
            actions=["core.cases.get_case"],
        ),
        title="Case agent",
        entity_type=AgentSessionEntity.CASE,
        entity_id=uuid.uuid4(),
        tools=["core.cases.get_case"],
        agent_preset_id=uuid.uuid4(),
        agent_preset_version_id=uuid.uuid4(),
    )
    dispatcher = SimpleNamespace(
        session=object(),
        create_or_get_agent_session=AsyncMock(return_value=prepared_session),
    )
    session_service = SimpleNamespace(
        execution_role=execution_role,
        prepare_new_turn=AsyncMock(return_value=prepared_turn),
    )

    @contextlib.asynccontextmanager
    async def dispatcher_context():
        yield dispatcher

    with (
        patch(
            "tracecat.cases.agent_invocations.activities."
            "CaseCommentAgentInvocationDispatcher.with_session",
            return_value=dispatcher_context(),
        ),
        patch(
            "tracecat.cases.agent_invocations.activities.AgentSessionService",
            return_value=session_service,
        ),
    ):
        result = await prepare_comment_agent_invocation_activity(
            PrepareCommentAgentInvocationInput(
                role=actor_role,
                invocation_id=invocation_id,
            )
        )

    assert result.workflow_args is not None
    assert result.workflow_args.role == execution_role
    assert result.workflow_args.role.scopes == (
        (actor_role.scopes or frozenset()) | AGENT_SESSION_EXECUTION_SCOPES
    )
    assert actor_role.scopes == frozenset({"case:update", "agent:execute"})
