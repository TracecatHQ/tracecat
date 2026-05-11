from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.folders.schemas import AgentFolderDirectoryItem
from tracecat.agent.folders.service import (
    AGENT_FOLDER_PARENT_NOT_FOUND_CODE,
    AgentFolderService,
)
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.tiers.enums import Entitlement

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def folder_service(
    session: AsyncSession, svc_role: Role
) -> AsyncGenerator[AgentFolderService, None]:
    """Create an agent folder service instance for testing."""
    yield AgentFolderService(session=session, role=svc_role)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invoker",
    [
        lambda service: service.get_folder(uuid4()),
        lambda service: service.get_folder_by_path("/parent/"),
        lambda service: service.list_folders("/"),
        lambda service: service.list_folders_paginated("/", CursorPaginationParams()),
        lambda service: service.get_directory_items("/"),
        lambda service: service.get_folder_tree("/"),
    ],
)
async def test_folder_read_methods_require_agent_read_scope(
    invoker: Callable[[AgentFolderService], Awaitable[object]],
) -> None:
    """Folder read methods should reject callers without agent:read before querying."""
    session = AsyncMock()
    service = AgentFolderService(
        session=session,
        role=Role(
            type="user",
            user_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            service_id="tracecat-api",
            scopes=frozenset(),
        ),
    )

    with pytest.raises(ScopeDeniedError) as exc_info:
        await invoker(service)

    assert exc_info.value.missing_scopes == ["agent:read"]
    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_delete_folder_allows_delete_scope_without_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder deletes should not require agent:read for the internal lookup."""
    session = AsyncMock()
    service = AgentFolderService(
        session=session,
        role=Role(
            type="user",
            user_id=uuid4(),
            organization_id=uuid4(),
            workspace_id=uuid4(),
            service_id="tracecat-api",
            scopes=frozenset({"agent:delete"}),
        ),
    )
    folder = SimpleNamespace(id=uuid4(), path="/folder/")
    monkeypatch.setattr(service, "_get_folder", AsyncMock(return_value=folder))
    monkeypatch.setattr(service, "_has_children", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_has_presets", AsyncMock(return_value=False))

    await service.delete_folder(folder.id)

    session.delete.assert_awaited_once_with(folder)
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_list_folders_escapes_like_wildcards(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listing a subtree should treat percent signs in folder names literally."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    await folder_service.create_folder(name="foo%", parent_path="/")
    await folder_service.create_folder(name="child", parent_path="/foo%/")
    await folder_service.create_folder(name="fooz", parent_path="/")

    folders = await folder_service.list_folders("/foo%")

    assert {folder.path for folder in folders} == {"/foo%/", "/foo%/child/"}


@pytest.mark.anyio
async def test_list_folders_paginated_excludes_parent_and_escapes_like_wildcards(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paginated subtree listing should exclude the parent and escape LIKE wildcards."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    await folder_service.create_folder(name="foo%", parent_path="/")
    await folder_service.create_folder(name="child", parent_path="/foo%/")
    await folder_service.create_folder(name="fooz", parent_path="/")

    page = await folder_service.list_folders_paginated(
        "/foo%", CursorPaginationParams(limit=10)
    )

    assert [folder.path for folder in page.items] == ["/foo%/child/"]
    assert page.has_more is False


@pytest.mark.anyio
async def test_list_folders_paginated_uses_cursor_pages(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paginated folder listing should return stable cursor pages."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    created_paths = {
        (await folder_service.create_folder(name="alpha", parent_path="/")).path,
        (await folder_service.create_folder(name="beta", parent_path="/")).path,
        (await folder_service.create_folder(name="gamma", parent_path="/")).path,
    }

    first_page = await folder_service.list_folders_paginated(
        "/", CursorPaginationParams(limit=2)
    )

    assert len(first_page.items) == 2
    assert first_page.has_more is True
    assert first_page.has_previous is False
    assert first_page.next_cursor is not None

    second_page = await folder_service.list_folders_paginated(
        "/", CursorPaginationParams(limit=2, cursor=first_page.next_cursor)
    )

    assert len(second_page.items) == 1
    assert second_page.has_more is False
    assert second_page.has_previous is True
    assert {folder.path for folder in first_page.items + second_page.items} == (
        created_paths
    )


@pytest.mark.anyio
async def test_list_folders_paginated_invalid_cursor_raises_tracecat_validation(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid folder cursors should surface app-level validation errors."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))

    with pytest.raises(
        TracecatValidationError, match="Invalid cursor for agent folders"
    ):
        await folder_service.list_folders_paginated(
            "/", CursorPaginationParams(cursor="invalid")
        )


@pytest.mark.anyio
async def test_list_folders_rejects_missing_parent_path(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing parent paths should not look like empty folders."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))

    with pytest.raises(TracecatValidationError) as exc_info:
        await folder_service.list_folders("/missing/")

    assert exc_info.value.detail == {"code": AGENT_FOLDER_PARENT_NOT_FOUND_CODE}


@pytest.mark.anyio
async def test_get_directory_items_escapes_like_wildcards(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directory queries should not leak sibling folders via LIKE wildcards."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    await folder_service.create_folder(name="foo%", parent_path="/")
    await folder_service.create_folder(name="child", parent_path="/foo%/")
    await folder_service.create_folder(name="fooz", parent_path="/")
    await folder_service.create_folder(name="intruder", parent_path="/fooz/")

    directory_items = await folder_service.get_directory_items("/foo%")
    folder_paths = {
        item.path
        for item in directory_items
        if isinstance(item, AgentFolderDirectoryItem)
    }

    assert folder_paths == {"/foo%/child/"}


@pytest.mark.anyio
async def test_get_directory_items_requires_agent_addons_entitlement(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directory reads should preserve the AGENT_ADDONS entitlement gate."""
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(folder_service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await folder_service.get_directory_items("/")

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)


@pytest.mark.anyio
async def test_move_preset_requires_agent_addons_entitlement(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preset folder moves should preserve the AGENT_ADDONS entitlement gate."""
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(folder_service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await folder_service.move_preset(uuid4(), None)

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invoker",
    [
        lambda service: service.get_folder(uuid4()),
        lambda service: service.get_folder_by_path("/parent/"),
        lambda service: service.list_folders("/"),
        lambda service: service.list_folders_paginated("/", CursorPaginationParams()),
        lambda service: service.create_folder(name="parent", parent_path="/"),
        lambda service: service.get_folder_tree("/"),
        lambda service: service.rename_folder(uuid4(), "renamed"),
        lambda service: service.move_folder(uuid4(), None),
        lambda service: service.delete_folder(uuid4()),
    ],
)
async def test_folder_management_methods_require_agent_addons_entitlement(
    folder_service: AgentFolderService,
    invoker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder management methods should preserve the AGENT_ADDONS gate."""
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(folder_service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await invoker(folder_service)

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)


@pytest.mark.anyio
async def test_get_directory_items_returns_real_direct_item_counts(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder rows should report direct child counts, not just boolean presence."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))

    parent = await folder_service.create_folder(name="parent", parent_path="/")
    await folder_service.create_folder(name="child-a", parent_path="/parent/")
    await folder_service.create_folder(name="child-b", parent_path="/parent/")
    await folder_service.create_folder(
        name="grandchild", parent_path="/parent/child-a/"
    )

    folder_service.session.add_all(
        [
            AgentPreset(
                workspace_id=folder_service.workspace_id,
                name="alpha",
                slug="alpha",
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
                enable_internet_access=False,
                folder_id=parent.id,
            ),
            AgentPreset(
                workspace_id=folder_service.workspace_id,
                name="beta",
                slug="beta",
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
                enable_internet_access=False,
                folder_id=parent.id,
            ),
        ]
    )
    await folder_service.session.commit()

    directory_items = await folder_service.get_directory_items("/")
    parent_item = next(
        item
        for item in directory_items
        if isinstance(item, AgentFolderDirectoryItem) and item.id == parent.id
    )

    assert parent_item.num_items == 4


@pytest.mark.anyio
async def test_create_folder_rejects_blank_name(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder creation should reject empty or whitespace-only names."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    with pytest.raises(TracecatValidationError, match="Folder name cannot be empty"):
        await folder_service.create_folder(name="   ", parent_path="/")


@pytest.mark.anyio
async def test_create_folder_trims_name_before_persisting(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder creation should store trimmed names and normalized paths."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    folder = await folder_service.create_folder(name="  parent  ", parent_path="/")

    assert folder.name == "parent"
    assert folder.path == "/parent/"


@pytest.mark.anyio
async def test_rename_folder_rejects_blank_name(
    folder_service: AgentFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder renames should reject empty or whitespace-only names."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    folder = await folder_service.create_folder(name="parent", parent_path="/")

    with pytest.raises(TracecatValidationError, match="Folder name cannot be empty"):
        await folder_service.rename_folder(folder.id, "  ")
