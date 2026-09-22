from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.skill.folders.schemas import SkillFolderDirectoryItem
from tracecat.agent.skill.folders.service import (
    SkillFolderErrorCode,
    SkillFolderService,
)
from tracecat.auth.types import Role
from tracecat.db.models import Skill, SkillFolder
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.tiers.enums import Entitlement

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def folder_service(
    session: AsyncSession, svc_role: Role
) -> AsyncGenerator[SkillFolderService, None]:
    """Create a skill folder service instance for testing."""
    yield SkillFolderService(session=session, role=svc_role)


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
    invoker: Callable[[SkillFolderService], Awaitable[object]],
) -> None:
    """Folder read methods should reject callers without agent:read before querying."""
    session = AsyncMock()
    service = SkillFolderService(
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
    service = SkillFolderService(
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
    monkeypatch.setattr(service, "has_entitlement", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_get_folder", AsyncMock(return_value=folder))
    monkeypatch.setattr(service, "_has_children", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_has_skills", AsyncMock(return_value=False))

    await service.delete_folder(folder.id)

    session.delete.assert_awaited_once_with(folder)
    session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_list_folders_escapes_like_wildcards(
    folder_service: SkillFolderService,
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
    folder_service: SkillFolderService,
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
    folder_service: SkillFolderService,
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
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid folder cursors should surface app-level validation errors."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))

    with pytest.raises(
        TracecatValidationError, match="Invalid cursor for skill folders"
    ):
        await folder_service.list_folders_paginated(
            "/", CursorPaginationParams(cursor="invalid")
        )


@pytest.mark.anyio
async def test_list_folders_rejects_missing_parent_path(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing parent paths should not look like empty folders."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))

    with pytest.raises(TracecatValidationError) as exc_info:
        await folder_service.list_folders("/missing/")

    assert exc_info.value.detail == {
        "code": SkillFolderErrorCode.PARENT_NOT_FOUND.value
    }


@pytest.mark.anyio
async def test_get_directory_items_escapes_like_wildcards(
    folder_service: SkillFolderService,
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
        if isinstance(item, SkillFolderDirectoryItem)
    }

    assert folder_paths == {"/foo%/child/"}


@pytest.mark.anyio
async def test_get_directory_items_requires_agent_addons_entitlement(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directory reads should preserve the AGENT_ADDONS entitlement gate."""
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(folder_service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await folder_service.get_directory_items("/")

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)


@pytest.mark.anyio
async def test_move_skill_requires_agent_addons_entitlement(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skill folder moves should preserve the AGENT_ADDONS entitlement gate."""
    mock_has_entitlement = AsyncMock(return_value=False)
    monkeypatch.setattr(folder_service, "has_entitlement", mock_has_entitlement)

    with pytest.raises(EntitlementRequired, match=Entitlement.AGENT_ADDONS.value):
        await folder_service.move_skill(uuid4(), None)

    mock_has_entitlement.assert_awaited_once_with(Entitlement.AGENT_ADDONS)


@pytest.mark.anyio
async def test_move_skill_rejects_soft_deleted_skill(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-deleted skills should not be movable through folder management."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    target = await folder_service.create_folder(name="target", parent_path="/")
    skill = Skill(
        workspace_id=folder_service.workspace_id,
        name="Soft-deleted skill",
        slug="soft-deleted-skill",
        deleted_at=datetime.now(UTC),
    )
    folder_service.session.add(skill)
    await folder_service.session.commit()

    with pytest.raises(TracecatNotFoundError):
        await folder_service.move_skill(skill.id, target)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invoker",
    [
        lambda service: service.get_folder(uuid4()),
        lambda service: service.get_folder_by_path("/parent/"),
        lambda service: service.list_folders("/"),
        lambda service: service.list_folders_paginated("/", CursorPaginationParams()),
        lambda service: service.get_directory_items("/"),
        lambda service: service.create_folder(name="parent", parent_path="/"),
        lambda service: service.get_folder_tree("/"),
        lambda service: service.rename_folder(uuid4(), "renamed"),
        lambda service: service.move_folder(uuid4(), None),
        lambda service: service.delete_folder(uuid4()),
    ],
)
async def test_folder_management_methods_require_agent_addons_entitlement(
    folder_service: SkillFolderService,
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
    folder_service: SkillFolderService,
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
            Skill(
                workspace_id=folder_service.workspace_id,
                name="alpha",
                slug="alpha",
                folder_id=parent.id,
            ),
            Skill(
                workspace_id=folder_service.workspace_id,
                name="beta",
                slug="beta",
                folder_id=parent.id,
            ),
            Skill(
                workspace_id=folder_service.workspace_id,
                name="soft deleted",
                slug="soft-deleted",
                folder_id=parent.id,
                deleted_at=datetime.now(UTC),
            ),
        ]
    )
    await folder_service.session.commit()

    directory_items = await folder_service.get_directory_items("/")
    parent_item = next(
        item
        for item in directory_items
        if isinstance(item, SkillFolderDirectoryItem) and item.id == parent.id
    )

    assert parent_item.num_items == 4


@pytest.mark.anyio
async def test_rename_folder_updates_descendant_paths(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder renames should rewrite descendant materialized paths."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    parent = await folder_service.create_folder(name="parent", parent_path="/")
    await folder_service.create_folder(name="child", parent_path="/parent/")
    await folder_service.create_folder(name="grandchild", parent_path="/parent/child/")

    renamed = await folder_service.rename_folder(parent.id, "renamed")
    result = await folder_service.session.execute(
        select(SkillFolder.path).where(
            SkillFolder.workspace_id == folder_service.workspace_id,
            SkillFolder.path.startswith("/renamed/", autoescape=True),
        )
    )

    assert renamed.path == "/renamed/"
    assert set(result.scalars().all()) == {
        "/renamed/",
        "/renamed/child/",
        "/renamed/child/grandchild/",
    }


@pytest.mark.anyio
async def test_move_folder_updates_descendant_paths(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder moves should rewrite descendant materialized paths."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    source = await folder_service.create_folder(name="source", parent_path="/")
    target = await folder_service.create_folder(name="target", parent_path="/")
    child = await folder_service.create_folder(name="child", parent_path="/source/")
    await folder_service.create_folder(name="grandchild", parent_path="/source/child/")

    moved = await folder_service.move_folder(child.id, target.id)
    result = await folder_service.session.execute(
        select(SkillFolder.path).where(
            SkillFolder.workspace_id == folder_service.workspace_id,
            SkillFolder.path.startswith("/target/child/", autoescape=True),
        )
    )

    assert moved.path == "/target/child/"
    assert set(result.scalars().all()) == {
        "/target/child/",
        "/target/child/grandchild/",
    }
    assert await folder_service.get_folder_by_path(source.path) is not None


@pytest.mark.anyio
async def test_delete_folder_recursive_clears_skills_and_deletes_descendants(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recursive folder deletes should batch-clear skill links and remove descendants."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    parent = await folder_service.create_folder(name="parent", parent_path="/")
    child = await folder_service.create_folder(name="child", parent_path="/parent/")
    folder_service.session.add_all(
        [
            Skill(
                workspace_id=folder_service.workspace_id,
                name="Parent skill",
                slug="parent-skill",
                folder_id=parent.id,
            ),
            Skill(
                workspace_id=folder_service.workspace_id,
                name="Child skill",
                slug="child-skill",
                folder_id=child.id,
            ),
        ]
    )
    await folder_service.session.commit()

    await folder_service.delete_folder(parent.id, recursive=True)

    folder_result = await folder_service.session.execute(
        select(SkillFolder.path).where(
            SkillFolder.workspace_id == folder_service.workspace_id,
            SkillFolder.path.startswith("/parent/", autoescape=True),
        )
    )
    skill_result = await folder_service.session.execute(
        select(Skill.slug, Skill.folder_id).where(
            Skill.workspace_id == folder_service.workspace_id,
            Skill.slug.in_(["parent-skill", "child-skill"]),
        )
    )

    assert folder_result.scalars().all() == []
    assert dict(skill_result.tuples().all()) == {
        "parent-skill": None,
        "child-skill": None,
    }


@pytest.mark.anyio
async def test_create_folder_rejects_blank_name(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder creation should reject empty or whitespace-only names."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    with pytest.raises(TracecatValidationError, match="Folder name cannot be empty"):
        await folder_service.create_folder(name="   ", parent_path="/")


@pytest.mark.anyio
async def test_create_folder_trims_name_before_persisting(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder creation should store trimmed names and normalized paths."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    folder = await folder_service.create_folder(name="  parent  ", parent_path="/")

    assert folder.name == "parent"
    assert folder.path == "/parent/"


@pytest.mark.anyio
async def test_rename_folder_rejects_blank_name(
    folder_service: SkillFolderService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folder renames should reject empty or whitespace-only names."""
    monkeypatch.setattr(folder_service, "has_entitlement", AsyncMock(return_value=True))
    folder = await folder_service.create_folder(name="parent", parent_path="/")

    with pytest.raises(TracecatValidationError, match="Folder name cannot be empty"):
        await folder_service.rename_folder(folder.id, "  ")
