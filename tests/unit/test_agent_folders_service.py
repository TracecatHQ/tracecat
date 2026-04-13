from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.folders.schemas import AgentFolderDirectoryItem
from tracecat.agent.folders.service import AgentFolderService
from tracecat.auth.types import Role

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
) -> None:
    """Directory queries should not leak sibling folders via LIKE wildcards."""
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
