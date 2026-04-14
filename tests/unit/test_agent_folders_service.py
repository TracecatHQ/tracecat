from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.folders.schemas import AgentFolderDirectoryItem
from tracecat.agent.folders.service import AgentFolderService
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.tiers.enums import Entitlement

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def folder_service(
    session: AsyncSession, svc_role: Role
) -> AsyncGenerator[AgentFolderService, None]:
    """Create an agent folder service instance for testing."""
    yield AgentFolderService(session=session, role=svc_role)


@pytest.mark.anyio
async def test_list_folders_escapes_like_wildcards(
    folder_service: AgentFolderService,
) -> None:
    """Listing a subtree should treat percent signs in folder names literally."""
    await folder_service.create_folder(name="foo%", parent_path="/")
    await folder_service.create_folder(name="child", parent_path="/foo%/")
    await folder_service.create_folder(name="fooz", parent_path="/")

    folders = await folder_service.list_folders("/foo%")

    assert {folder.path for folder in folders} == {"/foo%/", "/foo%/child/"}


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
) -> None:
    """Folder creation should reject empty or whitespace-only names."""
    with pytest.raises(TracecatValidationError, match="Folder name cannot be empty"):
        await folder_service.create_folder(name="   ", parent_path="/")


@pytest.mark.anyio
async def test_create_folder_trims_name_before_persisting(
    folder_service: AgentFolderService,
) -> None:
    """Folder creation should store trimmed names and normalized paths."""
    folder = await folder_service.create_folder(name="  parent  ", parent_path="/")

    assert folder.name == "parent"
    assert folder.path == "/parent/"


@pytest.mark.anyio
async def test_rename_folder_rejects_blank_name(
    folder_service: AgentFolderService,
) -> None:
    """Folder renames should reject empty or whitespace-only names."""
    folder = await folder_service.create_folder(name="parent", parent_path="/")

    with pytest.raises(TracecatValidationError, match="Folder name cannot be empty"):
        await folder_service.rename_folder(folder.id, "  ")
