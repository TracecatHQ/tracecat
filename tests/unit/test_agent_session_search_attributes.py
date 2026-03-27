"""Unit tests for direct agent session Temporal search attributes."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.common import TypedSearchAttributes

from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.chat.schemas import BasicChatRequest, ChatRequest
from tracecat.db.models import AgentSession
from tracecat.workflow.executions.correlation import build_agent_session_correlation_id
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)


def _search_attr_map(attrs: TypedSearchAttributes) -> dict[str, str]:
    return {pair.key.name: pair.value for pair in attrs.search_attributes}


def _build_db_session() -> AsyncSession:
    return cast(
        AsyncSession,
        create_autospec(AsyncSession, instance=True, spec_set=True),
    )


@pytest.fixture
def role_with_user() -> Role:
    return Role(
        type="user",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset({"agent:execute", "secret:read"}),
    )


@pytest.fixture
def role_without_user() -> Role:
    return Role(
        type="service",
        service_id="tracecat-mcp",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=None,
    )


def _build_session(role: Role, *, session_id: uuid.UUID) -> AgentSession:
    session = AgentSession(
        workspace_id=role.workspace_id,
        title="Test session",
        entity_type=AgentSessionEntity.COPILOT.value,
        entity_id=uuid.uuid4(),
        tools=["core.http_request"],
    )
    session.id = session_id
    return session


@asynccontextmanager
async def _mock_agent_config_context(
    agent_session: AgentSession,
):
    _ = agent_session
    agent_config_ctor = cast(Any, AgentConfig)
    yield agent_config_ctor(
        model_name="gpt-4o-mini",
        model_provider="openai",
        actions=["core.http_request"],
    )


@pytest.mark.anyio
async def test_run_turn_stamps_tracecat_search_attributes(
    role_with_user: Role,
) -> None:
    service = AgentSessionService(_build_db_session(), role_with_user)
    session_id = uuid.uuid4()
    agent_session = _build_session(role_with_user, session_id=session_id)

    temporal_client = AsyncMock()

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch.object(service, "has_pending_approvals", AsyncMock(return_value=False)),
        patch.object(service, "auto_title_session_on_first_prompt", AsyncMock()),
        patch.object(service, "_build_agent_config", _mock_agent_config_context),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
    ):
        response = await service.run_turn(
            session_id=session_id,
            request=cast(ChatRequest, BasicChatRequest(message="hello world")),
        )

    assert response is not None
    temporal_client.start_workflow.assert_awaited_once()
    kwargs = temporal_client.start_workflow.await_args.kwargs
    search_attributes = kwargs["search_attributes"]
    assert isinstance(search_attributes, TypedSearchAttributes)
    pairs = _search_attr_map(search_attributes)
    assert pairs[TemporalSearchAttr.TRIGGER_TYPE.value] == TriggerType.MANUAL.value
    assert (
        pairs[TemporalSearchAttr.EXECUTION_TYPE.value] == ExecutionType.PUBLISHED.value
    )
    assert pairs[
        TemporalSearchAttr.CORRELATION_ID.value
    ] == build_agent_session_correlation_id(session_id)
    assert pairs[TemporalSearchAttr.WORKSPACE_ID.value] == str(
        role_with_user.workspace_id
    )
    assert pairs[TemporalSearchAttr.TRIGGERED_BY_USER_ID.value] == str(
        role_with_user.user_id
    )


@pytest.mark.anyio
async def test_run_turn_omits_triggered_by_when_role_has_no_user_id(
    role_without_user: Role,
) -> None:
    service = AgentSessionService(_build_db_session(), role_without_user)
    session_id = uuid.uuid4()
    agent_session = _build_session(role_without_user, session_id=session_id)

    temporal_client = AsyncMock()

    with (
        patch.object(service, "get_session", AsyncMock(return_value=agent_session)),
        patch.object(service, "has_pending_approvals", AsyncMock(return_value=False)),
        patch.object(service, "auto_title_session_on_first_prompt", AsyncMock()),
        patch.object(service, "_build_agent_config", _mock_agent_config_context),
        patch(
            "tracecat.agent.session.service.get_temporal_client",
            AsyncMock(return_value=temporal_client),
        ),
    ):
        _ = await service.run_turn(
            session_id=session_id,
            request=cast(ChatRequest, BasicChatRequest(message="hello world")),
        )

    kwargs = temporal_client.start_workflow.await_args.kwargs
    search_attributes = kwargs["search_attributes"]
    assert isinstance(search_attributes, TypedSearchAttributes)
    pairs = _search_attr_map(search_attributes)
    assert pairs[TemporalSearchAttr.TRIGGER_TYPE.value] == TriggerType.MANUAL.value
    assert (
        pairs[TemporalSearchAttr.EXECUTION_TYPE.value] == ExecutionType.PUBLISHED.value
    )
    assert pairs[
        TemporalSearchAttr.CORRELATION_ID.value
    ] == build_agent_session_correlation_id(session_id)
    assert pairs[TemporalSearchAttr.WORKSPACE_ID.value] == str(
        role_without_user.workspace_id
    )
    assert TemporalSearchAttr.TRIGGERED_BY_USER_ID.value not in pairs
