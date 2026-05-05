"""Unit tests for AgentFolderService."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.agent.folders.service import AgentFolderService
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset, Workspace
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def folder_service(session: AsyncSession, svc_role: Role) -> AgentFolderService:
    return AgentFolderService(session=session, role=svc_role)


@pytest.fixture
async def preset(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[AgentPreset, None]:
    p = AgentPreset(
        name="test preset",
        slug="test-preset",
        workspace_id=svc_workspace.id,
        model_name="claude-3-5-sonnet",
        model_provider="anthropic",
    )
    session.add(p)
    await session.commit()
    try:
        yield p
    finally:
        await session.delete(p)
        await session.commit()


@pytest.mark.anyio
class TestAgentFolderService:
    async def test_create_and_get(self, folder_service: AgentFolderService) -> None:
        folder = await folder_service.create_folder(name="alpha")
        assert folder.path == "/alpha/"

        by_id = await folder_service.get_folder(folder.id)
        assert by_id is not None and by_id.id == folder.id

        by_path = await folder_service.get_folder_by_path("/alpha/")
        assert by_path is not None and by_path.id == folder.id

    async def test_nested_create(self, folder_service: AgentFolderService) -> None:
        await folder_service.create_folder(name="parent")
        child = await folder_service.create_folder(name="child", parent_path="/parent/")
        assert child.path == "/parent/child/"

    async def test_blank_name_rejected(
        self, folder_service: AgentFolderService
    ) -> None:
        with pytest.raises(TracecatValidationError):
            await folder_service.create_folder(name="   ")

    async def test_slash_in_name_rejected(
        self, folder_service: AgentFolderService
    ) -> None:
        with pytest.raises(TracecatValidationError):
            await folder_service.create_folder(name="bad/name")

    async def test_duplicate_path_raises_conflict(
        self, folder_service: AgentFolderService
    ) -> None:
        await folder_service.create_folder(name="dup")
        with pytest.raises(TracecatConflictError):
            await folder_service.create_folder(name="dup")

    async def test_missing_parent_rejected(
        self, folder_service: AgentFolderService
    ) -> None:
        with pytest.raises(TracecatValidationError):
            await folder_service.create_folder(name="x", parent_path="/nope/")

    async def test_rename_updates_descendants(
        self, folder_service: AgentFolderService
    ) -> None:
        await folder_service.create_folder(name="parent")
        await folder_service.create_folder(name="child", parent_path="/parent/")
        await folder_service.create_folder(
            name="grandchild", parent_path="/parent/child/"
        )

        parent = await folder_service.get_folder_by_path("/parent/")
        assert parent is not None
        await folder_service.rename_folder(parent.id, "renamed")

        grandchild = await folder_service.get_folder_by_path(
            "/renamed/child/grandchild/"
        )
        assert grandchild is not None

    async def test_move_folder_prevents_cycles(
        self, folder_service: AgentFolderService
    ) -> None:
        a = await folder_service.create_folder(name="a")
        b = await folder_service.create_folder(name="b", parent_path="/a/")
        with pytest.raises(TracecatValidationError):
            await folder_service.move_folder(a.id, b.id)

    async def test_delete_non_empty_blocks_without_recursive(
        self, folder_service: AgentFolderService
    ) -> None:
        parent = await folder_service.create_folder(name="parent")
        await folder_service.create_folder(name="child", parent_path="/parent/")
        with pytest.raises(TracecatValidationError):
            await folder_service.delete_folder(parent.id, recursive=False)

    async def test_delete_recursive_detaches_presets(
        self,
        folder_service: AgentFolderService,
        preset: AgentPreset,
        session: AsyncSession,
    ) -> None:
        folder = await folder_service.create_folder(name="bucket")
        await folder_service.move_preset(preset.id, folder)

        await folder_service.delete_folder(folder.id, recursive=True)

        await session.refresh(preset)
        assert preset.folder_id is None

    async def test_move_preset_to_root(
        self,
        folder_service: AgentFolderService,
        preset: AgentPreset,
        session: AsyncSession,
    ) -> None:
        folder = await folder_service.create_folder(name="bucket")
        await folder_service.move_preset(preset.id, folder)
        await session.refresh(preset)
        assert preset.folder_id == folder.id

        await folder_service.move_preset(preset.id, None)
        await session.refresh(preset)
        assert preset.folder_id is None

    async def test_move_missing_preset_raises_not_found(
        self, folder_service: AgentFolderService
    ) -> None:
        import uuid

        with pytest.raises(TracecatNotFoundError):
            await folder_service.move_preset(uuid.uuid4(), None)

    async def test_get_directory_items_root(
        self,
        folder_service: AgentFolderService,
        preset: AgentPreset,
    ) -> None:
        await folder_service.create_folder(name="alpha")
        await folder_service.create_folder(name="beta")

        items = await folder_service.get_directory_items("/")
        types = {i.type for i in items}
        assert "folder" in types and "preset" in types
        names = {getattr(i, "name", None) for i in items}
        assert {"alpha", "beta"}.issubset(names)

    async def test_get_directory_items_unknown_path_raises(
        self, folder_service: AgentFolderService
    ) -> None:
        with pytest.raises(TracecatNotFoundError):
            await folder_service.get_directory_items("/does-not-exist/")
