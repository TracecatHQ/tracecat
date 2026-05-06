from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import NoResultFound

from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.types import Role
from tracecat.db.models import AgentTag
from tracecat.exceptions import EntitlementRequired, ScopeDeniedError
from tracecat.pagination import BaseCursorPaginator, CursorPaginationParams
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


def _agent_tag(name: str, created_at: datetime, workspace_id: object) -> AgentTag:
    return AgentTag(
        id=uuid4(),
        workspace_id=workspace_id,
        name=name,
        ref=name,
        color=None,
        created_at=created_at,
        updated_at=created_at,
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
async def test_list_tags_paginated_reverse_pages_use_canonical_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reverse tag pages should return newest-first items with swapped cursors."""
    workspace_id = uuid4()
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    raw_reverse_rows = [
        _agent_tag("older", base_time + timedelta(minutes=1), workspace_id),
        _agent_tag("middle", base_time + timedelta(minutes=2), workspace_id),
        _agent_tag("newer", base_time + timedelta(minutes=3), workspace_id),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = raw_reverse_rows
    session = AsyncMock()
    session.execute.return_value = result
    service = AgentTagsService(
        session=session,
        role=_role_with_scopes(frozenset({"agent:read"})),
    )
    monkeypatch.setattr(service, "has_entitlement", AsyncMock(return_value=True))
    cursor = BaseCursorPaginator.encode_cursor(
        uuid4(),
        sort_column="created_at",
        sort_value=base_time,
    )

    page = await service.list_tags_paginated(
        CursorPaginationParams(limit=2, cursor=cursor, reverse=True)
    )

    assert [tag.id for tag in page.items] == [
        raw_reverse_rows[1].id,
        raw_reverse_rows[0].id,
    ]
    assert page.next_cursor == BaseCursorPaginator.encode_cursor(
        raw_reverse_rows[0].id,
        sort_column="created_at",
        sort_value=raw_reverse_rows[0].created_at,
    )
    assert page.prev_cursor == BaseCursorPaginator.encode_cursor(
        raw_reverse_rows[1].id,
        sort_column="created_at",
        sort_value=raw_reverse_rows[1].created_at,
    )
    assert page.has_more is True
    assert page.has_previous is True


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


@pytest.mark.anyio
async def test_add_preset_tag_rejects_duplicate_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate preset-tag assignments should surface as conflicts."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.scalar.return_value = True
    session.execute.return_value = result
    service = AgentTagsService(
        session=session,
        role=_role_with_scopes(frozenset({"agent:update"})),
    )
    monkeypatch.setattr(service, "has_entitlement", AsyncMock(return_value=True))

    with pytest.raises(ValueError, match="Agent preset tag already exists"):
        await service.add_preset_tag(uuid4(), uuid4())

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
