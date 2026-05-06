from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import NoResultFound

from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired, ScopeDeniedError
from tracecat.pagination import CursorPaginationParams
from tracecat.tags.schemas import TagCreate, TagUpdate
from tracecat.tiers.enums import Entitlement


def _role_with_scopes(scopes: frozenset[str] | None) -> Role:
    return Role(
        type="user",
        user_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        service_id="tracecat-api",
        scopes=scopes,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invoker",
    [
        lambda service: service.list_tags(),
        lambda service: service.list_tags_paginated(CursorPaginationParams()),
        lambda service: service.get_tag(uuid4()),
    ],
)
async def test_agent_tag_read_methods_require_agent_read_scope(
    invoker: Callable[[AgentTagsService], Awaitable[object]],
) -> None:
    """Read methods should reject callers without agent:read before querying."""
    session = AsyncMock()
    service = AgentTagsService(session=session, role=_role_with_scopes(frozenset()))

    with pytest.raises(ScopeDeniedError) as exc_info:
        await invoker(service)

    assert exc_info.value.missing_scopes == ["agent:read"]
    session.execute.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("invoker", "role_scopes"),
    [
        (
            lambda service: service.list_tags_for_preset(uuid4()),
            frozenset({"agent:read"}),
        ),
        (
            lambda service: service.get_preset_tag(uuid4(), uuid4()),
            frozenset({"agent:update"}),
        ),
        (
            lambda service: service.add_preset_tag(uuid4(), uuid4()),
            frozenset({"agent:update"}),
        ),
        (
            lambda service: service.remove_preset_tag(object()),
            frozenset({"agent:update"}),
        ),
    ],
)
async def test_preset_tag_methods_require_agent_addons_entitlement(
    invoker: Callable[[AgentTagsService], Awaitable[object]],
    role_scopes: frozenset[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preset-tag association methods should preserve the AGENT_ADDONS gate."""
    session = AsyncMock()
    service = AgentTagsService(session=session, role=_role_with_scopes(role_scopes))
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await invoker(service)

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)
    session.execute.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("invoker", "role_scopes"),
    [
        (lambda service: service.list_tags(), frozenset({"agent:read"})),
        (
            lambda service: service.list_tags_paginated(CursorPaginationParams()),
            frozenset({"agent:read"}),
        ),
        (lambda service: service.get_tag(uuid4()), frozenset({"agent:read"})),
        (
            lambda service: service.create_tag(
                TagCreate(name="alpha", color="#000000")
            ),
            frozenset({"agent:create"}),
        ),
        (
            lambda service: service.update_tag(object(), TagUpdate(name="beta")),
            frozenset({"agent:update"}),
        ),
        (lambda service: service.delete_tag(object()), frozenset({"agent:delete"})),
        (
            lambda service: service.delete_tag_by_id(uuid4()),
            frozenset({"agent:delete"}),
        ),
    ],
)
async def test_agent_tag_definition_methods_require_agent_addons_entitlement(
    invoker: Callable[[AgentTagsService], Awaitable[object]],
    role_scopes: frozenset[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent tag definition CRUD should preserve the AGENT_ADDONS gate."""
    session = AsyncMock()
    service = AgentTagsService(session=session, role=_role_with_scopes(role_scopes))
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await invoker(service)

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)
    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_tag_by_id_allows_delete_scope_without_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting by ID should not require agent:read."""
    tag = object()
    result = MagicMock()
    result.scalar_one.return_value = tag
    session = AsyncMock()
    session.execute.return_value = result
    service = AgentTagsService(
        session=session,
        role=_role_with_scopes(frozenset({"agent:delete"})),
    )
    monkeypatch.setattr(service, "has_entitlement", AsyncMock(return_value=True))

    await service.delete_tag_by_id(uuid4())

    session.delete.assert_awaited_once_with(tag)
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_list_tags_for_preset_requires_existing_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preset tag listing should fail when the preset is missing."""
    session = AsyncMock()
    session.scalar.return_value = False
    service = AgentTagsService(
        session=session,
        role=_role_with_scopes(frozenset({"agent:read"})),
    )
    monkeypatch.setattr(service, "has_entitlement", AsyncMock(return_value=True))

    with pytest.raises(NoResultFound, match="Agent preset not found"):
        await service.list_tags_for_preset(uuid4())

    session.execute.assert_not_awaited()
