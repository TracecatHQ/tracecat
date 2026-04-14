"""Service for managing agent preset folders using materialized path pattern."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.agent.folders.schemas import (
    AgentFolderDirectoryItem,
    AgentPresetDirectoryItem,
    DirectoryItem,
)
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentFolder, AgentPreset
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tags.schemas import TagRead
from tracecat.tiers.enums import Entitlement

AGENT_FOLDER_CONFLICT_CODE = "agent_folder_conflict"
AGENT_FOLDER_NOT_FOUND_CODE = "agent_folder_not_found"
AGENT_FOLDER_PARENT_NOT_FOUND_CODE = "agent_folder_parent_not_found"
AGENT_FOLDER_INVALID_CODE = "agent_folder_invalid"


class AgentFolderService(BaseWorkspaceService):
    """Service for managing agent preset folders using materialized path pattern."""

    service_name = "agent_folders"

    @staticmethod
    def _normalize_folder_path(path: str) -> str:
        """Normalize folder paths to materialized-path format."""
        if not path or path == "/":
            return "/"
        return path if path.endswith("/") else f"{path}/"

    @staticmethod
    def _folder_validation_error(
        message: str,
        *,
        code: str,
    ) -> TracecatValidationError:
        return TracecatValidationError(message, detail={"code": code})

    @classmethod
    def _get_parent_path(cls, path: str) -> str:
        """Return the immediate parent path for a normalized folder path."""
        path = cls._normalize_folder_path(path)
        if path == "/":
            return "/"

        parent_path, _, _ = path.rstrip("/").rpartition("/")
        return f"{parent_path}/" if parent_path else "/"

    async def _write_folder_change(
        self,
        *,
        conflict_path: str,
        commit: bool,
    ) -> None:
        """Persist a folder write and translate unique conflicts cleanly."""
        try:
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise self._folder_validation_error(
                f"Folder {conflict_path} already exists",
                code=AGENT_FOLDER_CONFLICT_CODE,
            ) from exc

    @require_scope("agent:create")
    async def create_folder(
        self, name: str, parent_path: str = "/", commit: bool = True
    ) -> AgentFolder:
        """Create a new agent folder."""
        if "/" in name:
            raise self._folder_validation_error(
                "Folder name cannot contain slashes",
                code=AGENT_FOLDER_INVALID_CODE,
            )

        parent_path = self._normalize_folder_path(parent_path)

        if parent_path != "/":
            parent_exists = await self._folder_path_exists(parent_path)
            if not parent_exists:
                raise self._folder_validation_error(
                    f"Parent path {parent_path} not found",
                    code=AGENT_FOLDER_PARENT_NOT_FOUND_CODE,
                )

        full_path = f"{parent_path}{name}/" if parent_path != "/" else f"/{name}/"

        path_exists = await self._folder_path_exists(full_path)
        if path_exists:
            raise self._folder_validation_error(
                f"Folder {full_path} already exists",
                code=AGENT_FOLDER_CONFLICT_CODE,
            )

        folder = AgentFolder(
            name=name,
            path=full_path,
            workspace_id=self.workspace_id,
        )
        self.session.add(folder)
        await self._write_folder_change(conflict_path=full_path, commit=commit)
        await self.session.refresh(folder)
        return folder

    async def get_folder(self, folder_id: uuid.UUID) -> AgentFolder | None:
        """Get a folder by ID."""
        statement = select(AgentFolder).where(
            AgentFolder.workspace_id == self.workspace_id,
            AgentFolder.id == folder_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_folder_by_path(self, path: str) -> AgentFolder | None:
        """Get a folder by its path."""
        path = self._normalize_folder_path(path)

        statement = select(AgentFolder).where(
            AgentFolder.workspace_id == self.workspace_id,
            AgentFolder.path == path,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_folders(self, parent_path: str = "/") -> Sequence[AgentFolder]:
        """List all folders within the specified parent path subtree."""
        parent_path = self._normalize_folder_path(parent_path)
        statement = select(AgentFolder).where(
            AgentFolder.workspace_id == self.workspace_id,
            AgentFolder.path.startswith(parent_path, autoescape=True),
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def move_preset(
        self, preset_id: uuid.UUID, folder: AgentFolder | None = None
    ) -> AgentPreset:
        """Move an agent preset to a different folder."""
        statement = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.execute(statement)
        preset = result.scalar_one_or_none()
        if not preset:
            raise TracecatNotFoundError(f"Agent preset {preset_id} not found")

        preset.folder_id = folder.id if folder else None
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    @require_scope("agent:update")
    async def rename_folder(self, folder_id: uuid.UUID, new_name: str) -> AgentFolder:
        """Rename a folder. Updates the folder name and path."""
        if "/" in new_name:
            raise self._folder_validation_error(
                "Folder name cannot contain slashes",
                code=AGENT_FOLDER_INVALID_CODE,
            )

        folder = await self.get_folder(folder_id)
        if not folder:
            raise self._folder_validation_error(
                f"Folder {folder_id} not found",
                code=AGENT_FOLDER_NOT_FOUND_CODE,
            )

        old_path = folder.path
        parent_path = self._get_parent_path(folder.path)
        new_path = (
            f"{parent_path}{new_name}/" if parent_path != "/" else f"/{new_name}/"
        )

        if new_path != old_path:
            path_exists = await self._folder_path_exists(new_path)
            if path_exists:
                raise self._folder_validation_error(
                    f"Folder {new_path} already exists",
                    code=AGENT_FOLDER_CONFLICT_CODE,
                )

        descendants = await self._get_descendants(old_path)

        folder.name = new_name
        folder.path = new_path
        self.session.add(folder)

        for descendant in descendants:
            descendant.path = descendant.path.replace(old_path, new_path, 1)
            self.session.add(descendant)

        await self._write_folder_change(conflict_path=new_path, commit=True)
        await self.session.refresh(folder)
        return folder

    @require_scope("agent:update")
    async def move_folder(
        self, folder_id: uuid.UUID, new_parent_id: uuid.UUID | None
    ) -> AgentFolder:
        """Move a folder to a different parent."""
        folder = await self.get_folder(folder_id)
        if not folder:
            raise self._folder_validation_error(
                f"Folder {folder_id} not found",
                code=AGENT_FOLDER_NOT_FOUND_CODE,
            )

        new_parent_path = "/"
        if new_parent_id is not None:
            new_parent = await self.get_folder(new_parent_id)
            if not new_parent:
                raise self._folder_validation_error(
                    f"Parent folder {new_parent_id} not found",
                    code=AGENT_FOLDER_PARENT_NOT_FOUND_CODE,
                )
            new_parent_path = new_parent.path

            if folder.path == new_parent_path:
                raise self._folder_validation_error(
                    "Cannot make a folder its own child",
                    code=AGENT_FOLDER_INVALID_CODE,
                )
            if new_parent.path.startswith(folder.path):
                raise self._folder_validation_error(
                    "Cannot create cyclic folder structure",
                    code=AGENT_FOLDER_INVALID_CODE,
                )

        old_path = folder.path
        old_name = folder.name
        new_path = (
            f"{new_parent_path}{old_name}/"
            if new_parent_path != "/"
            else f"/{old_name}/"
        )

        if new_path != old_path:
            path_exists = await self._folder_path_exists(new_path)
            if path_exists:
                raise self._folder_validation_error(
                    f"Folder {new_path} already exists",
                    code=AGENT_FOLDER_CONFLICT_CODE,
                )

        descendants = await self._get_descendants(old_path)

        folder.path = new_path
        self.session.add(folder)

        for descendant in descendants:
            descendant.path = descendant.path.replace(old_path, new_path, 1)
            self.session.add(descendant)

        await self._write_folder_change(conflict_path=new_path, commit=True)
        await self.session.refresh(folder)
        return folder

    @require_scope("agent:delete")
    async def delete_folder(
        self, folder_id: uuid.UUID, recursive: bool = False
    ) -> None:
        """Delete a folder."""
        folder = await self.get_folder(folder_id)
        if not folder:
            raise self._folder_validation_error(
                f"Folder {folder_id} not found",
                code=AGENT_FOLDER_NOT_FOUND_CODE,
            )

        if folder.path == "/":
            raise self._folder_validation_error(
                "Cannot delete root folder",
                code=AGENT_FOLDER_INVALID_CODE,
            )

        if not recursive:
            has_children = await self._has_children(folder.path)
            has_presets = await self._has_presets(folder_id)
            if has_children or has_presets:
                raise self._folder_validation_error(
                    "Folder is not empty. Please move or delete its contents first.",
                    code=AGENT_FOLDER_INVALID_CODE,
                )
        else:
            descendants = await self._get_descendants(folder.path)
            for descendant in descendants:
                statement = select(AgentPreset).where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.folder_id == descendant.id,
                )
                result = await self.session.execute(statement)
                presets = result.scalars().all()
                for preset in presets:
                    preset.folder_id = None
                    self.session.add(preset)
                await self.session.delete(descendant)

            statement = select(AgentPreset).where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.folder_id == folder.id,
            )
            result = await self.session.execute(statement)
            presets = result.scalars().all()
            for preset in presets:
                preset.folder_id = None
                self.session.add(preset)

        await self.session.delete(folder)
        await self.session.commit()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_directory_items(
        self, path: str = "/", *, order_by: Literal["asc", "desc"] = "desc"
    ) -> Sequence[DirectoryItem]:
        """Get all directory items (presets and folders) in the given path."""
        path = self._normalize_folder_path(path)

        if path != "/":
            folder = await self.get_folder_by_path(path)
            if not folder:
                raise TracecatNotFoundError(f"Folder {path} not found")
            folder_id = folder.id
        else:
            folder_id = None

        # Fetch presets in this folder
        preset_statement = (
            select(AgentPreset)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.folder_id == folder_id,
            )
            .order_by(
                AgentPreset.created_at.desc()
                if order_by == "desc"
                else AgentPreset.created_at.asc()
            )
            .options(selectinload(AgentPreset.tags))
        )
        preset_result = await self.session.execute(preset_statement)
        presets = preset_result.scalars().all()

        path_depth = path.count("/") + 1
        folder_statement = select(AgentFolder).where(
            AgentFolder.workspace_id == self.workspace_id,
            AgentFolder.path.startswith(path, autoescape=True),
            AgentFolder.path != path,
            func.length(AgentFolder.path)
            - func.length(func.replace(AgentFolder.path, "/", ""))
            == path_depth,
        )
        folder_result = await self.session.execute(folder_statement)
        folders = folder_result.scalars().all()

        directory_items: list[DirectoryItem] = []
        folder_ids = [folder.id for folder in folders]
        folder_paths = {folder.path for folder in folders}
        preset_counts_by_folder_id: dict[uuid.UUID, int] = {}
        child_folder_counts_by_path: dict[str, int] = {}

        if folder_ids:
            preset_folder_result = await self.session.execute(
                select(AgentPreset.folder_id, func.count(AgentPreset.id))
                .where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.folder_id.in_(folder_ids),
                )
                .group_by(AgentPreset.folder_id)
            )
            preset_counts_by_folder_id = {
                folder_id: preset_count
                for folder_id, preset_count in preset_folder_result.tuples().all()
                if folder_id is not None
            }

            descendant_path_result = await self.session.execute(
                select(AgentFolder.path).where(
                    AgentFolder.workspace_id == self.workspace_id,
                    AgentFolder.path.startswith(path, autoescape=True),
                    AgentFolder.path != path,
                )
            )
            for descendant_path in descendant_path_result.scalars().all():
                parent_path = self._get_parent_path(descendant_path)
                if parent_path in folder_paths:
                    child_folder_counts_by_path[parent_path] = (
                        child_folder_counts_by_path.get(parent_path, 0) + 1
                    )

        for f in folders:
            num_items = child_folder_counts_by_path.get(
                f.path, 0
            ) + preset_counts_by_folder_id.get(f.id, 0)
            directory_items.append(
                AgentFolderDirectoryItem(
                    type="folder",
                    num_items=num_items,
                    id=f.id,
                    name=f.name,
                    path=f.path,
                    workspace_id=f.workspace_id,
                    created_at=f.created_at,
                    updated_at=f.updated_at,
                )
            )

        for preset in presets:
            directory_items.append(
                AgentPresetDirectoryItem(
                    type="preset",
                    id=preset.id,
                    name=preset.name,
                    slug=preset.slug,
                    description=preset.description,
                    model_provider=preset.model_provider,
                    model_name=preset.model_name,
                    folder_id=preset.folder_id,
                    tags=[
                        TagRead.model_validate(tag, from_attributes=True)
                        for tag in preset.tags
                    ],
                    created_at=preset.created_at,
                    updated_at=preset.updated_at,
                )
            )

        return directory_items

    async def get_folder_tree(self, root_path: str = "/") -> Sequence[AgentFolder]:
        """Get the full folder tree starting from the given root path."""
        root_path = self._normalize_folder_path(root_path)

        statement = (
            select(AgentFolder)
            .where(
                AgentFolder.workspace_id == self.workspace_id,
                AgentFolder.path.startswith(root_path, autoescape=True),
            )
            .order_by(AgentFolder.path)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    # Private helpers

    async def _folder_path_exists(self, path: str) -> bool:
        path = self._normalize_folder_path(path)
        statement = (
            select(func.count())
            .select_from(AgentFolder)
            .where(
                AgentFolder.workspace_id == self.workspace_id,
                AgentFolder.path == path,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one() > 0

    async def _has_children(self, path: str) -> bool:
        path = self._normalize_folder_path(path)
        statement = (
            select(func.count())
            .select_from(AgentFolder)
            .where(
                AgentFolder.workspace_id == self.workspace_id,
                AgentFolder.path.startswith(path, autoescape=True),
                AgentFolder.path != path,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one() > 0

    async def _has_presets(self, folder_id: uuid.UUID) -> bool:
        statement = (
            select(func.count())
            .select_from(AgentPreset)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.folder_id == folder_id,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one() > 0

    async def _get_descendants(self, path: str) -> Sequence[AgentFolder]:
        path = self._normalize_folder_path(path)
        statement = select(AgentFolder).where(
            AgentFolder.workspace_id == self.workspace_id,
            AgentFolder.path.startswith(path, autoescape=True),
            AgentFolder.path != path,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
