"""New sessions on non-default agent backends require ``agent_runtime_preview``."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.backends.access import can_use_backend
from tracecat.agent.session.schemas import AgentSessionCreate
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.db.models import AgentSession
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.tiers.enums import Entitlement

IS_ORG_ENTITLED = "tracecat.agent.backends.access.is_org_entitled"


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
@pytest.mark.parametrize("backend_id", [None, "oss"])
async def test_default_backend_never_checks_entitlement(
    backend_id: str | None,
) -> None:
    with patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)) as entitled:
        assert await can_use_backend(AsyncMock(), uuid.uuid4(), backend_id)
    entitled.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("is_entitled", [False, True])
async def test_non_default_backend_follows_entitlement(is_entitled: bool) -> None:
    db = AsyncMock()
    org_id = uuid.uuid4()
    with patch(IS_ORG_ENTITLED, AsyncMock(return_value=is_entitled)) as entitled:
        assert await can_use_backend(db, org_id, "external") is is_entitled
    entitled.assert_awaited_once_with(db, org_id, Entitlement.AGENT_RUNTIME_PREVIEW)


@pytest.mark.anyio
async def test_create_rejects_non_default_backend_without_entitlement() -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)),
        pytest.raises(EntitlementRequired),
    ):
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
                backend_id="external",
            )
        )
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_create_allows_default_backend_without_entitlement() -> None:
    service, db, role = _service()
    assert role.workspace_id is not None
    with patch(IS_ORG_ENTITLED, AsyncMock(return_value=False)):
        created = await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
            )
        )
    assert created.backend_id == "oss"
    db.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_create_passes_gate_with_entitlement() -> None:
    """Entitled orgs reach backend resolution, which rejects uninstalled ones."""
    service, db, role = _service()
    assert role.workspace_id is not None
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=True)),
        pytest.raises(TracecatValidationError, match="unavailable"),
    ):
        await service.create_session(
            AgentSessionCreate(
                entity_type=AgentSessionEntity.WORKSPACE_CHAT,
                entity_id=role.workspace_id,
                backend_id="external",
            )
        )
    db.add.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("is_entitled", "expected"),
    [(False, EntitlementRequired), (True, TracecatValidationError)],
)
async def test_fork_gates_non_default_source_backend(
    is_entitled: bool, expected: type[Exception]
) -> None:
    service, db, role = _service()
    source = AgentSession(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        title="Source",
        entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
        entity_id=role.workspace_id,
        backend_id="external",
        harness_type="claude_code",
    )
    result = Mock()
    result.tuples.return_value.one_or_none.return_value = (source, None)
    db.execute.return_value = result
    with (
        patch(IS_ORG_ENTITLED, AsyncMock(return_value=is_entitled)),
        pytest.raises(expected),
    ):
        await service.fork_session(source.id)
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
