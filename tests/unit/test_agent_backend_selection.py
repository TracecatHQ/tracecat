"""New sessions take the configured backend only on surfaces that can run it."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.backends.default import DefaultBackend
from tracecat.agent.backends.selection import select_backend_id
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role

IS_ORG_ENTITLED = "tracecat.agent.backends.access.is_org_entitled"
CONFIGURED_BACKEND = (
    "tracecat.agent.backends.selection.config.TRACECAT__AGENT_CHAT_BACKEND"
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("entity_type", "entitled", "expected"),
    [
        # Chat-shaped surfaces take the configured backend.
        (AgentSessionEntity.WORKSPACE_CHAT, True, "external"),
        (AgentSessionEntity.AGENT_PRESET, True, "external"),
        (AgentSessionEntity.CASE, True, "external"),
        (AgentSessionEntity.EXTERNAL_CHANNEL, True, "external"),
        # Orgs without the entitlement keep the built-in backend.
        (AgentSessionEntity.WORKSPACE_CHAT, False, "oss"),
        (AgentSessionEntity.CASE, False, "oss"),
        (AgentSessionEntity.EXTERNAL_CHANNEL, False, "oss"),
        # Builder tools and workflow agent actions need the built-in workflow.
        (AgentSessionEntity.AGENT_PRESET_BUILDER, True, "oss"),
        (AgentSessionEntity.WORKFLOW, True, "oss"),
    ],
)
async def test_select_backend_id_follows_surface_and_entitlement(
    entity_type: AgentSessionEntity, entitled: bool, expected: str
) -> None:
    with (
        patch(CONFIGURED_BACKEND, "external"),
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=entitled)),
    ):
        selected = await select_backend_id(AsyncMock(), uuid.uuid4(), entity_type)
    assert selected == expected


@pytest.mark.anyio
async def test_select_backend_id_skips_the_entitlement_for_the_builtin_default() -> (
    None
):
    with (
        patch(CONFIGURED_BACKEND, "oss"),
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)) as entitled,
    ):
        selected = await select_backend_id(
            AsyncMock(), uuid.uuid4(), AgentSessionEntity.EXTERNAL_CHANNEL
        )
    assert selected == "oss"
    entitled.assert_not_awaited()


def _service() -> tuple[AgentSessionService, AsyncMock, Role]:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    db = AsyncMock(add=Mock())
    return AgentSessionService(db, role), db, role


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("pinned", "expected"),
    [
        # Slack, case comments and MCP preset runs create sessions unpinned too.
        (None, "external"),
        # A caller's explicit choice is never replaced.
        ("oss", "oss"),
    ],
)
async def test_create_session_selects_a_backend_only_when_unpinned(
    pinned: str | None, expected: str
) -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    # Workspace chat stores no default tools, so creation needs no tool lookup.
    args = AgentSessionCreate(
        entity_type=AgentSessionEntity.WORKSPACE_CHAT, entity_id=role.workspace_id
    )
    if pinned is not None:
        args = AgentSessionCreate(
            entity_type=AgentSessionEntity.WORKSPACE_CHAT,
            entity_id=role.workspace_id,
            backend_id=pinned,
        )
    with (
        patch(CONFIGURED_BACKEND, "external"),
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=True)),
        # Any installed backend will do; only the stored identifier matters.
        patch(
            "tracecat.agent.session.service.get_agent_backend",
            return_value=DefaultBackend(),
        ) as get_backend,
    ):
        created = await service.create_session(args)
    assert created.backend_id == expected
    get_backend.assert_called_once_with(expected, harness_type=None)
    db.commit.assert_awaited_once()
