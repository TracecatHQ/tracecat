"""Service for agent preset folders using the materialized-path pattern."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.agent.folders.schemas import (
    AgentFolderDirectoryItem,
    AgentPresetDirectoryItem,
    DirectoryItem,
)
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentFolder, AgentPreset
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import AgentFolderID
from tracecat.service import BaseWorkspaceService
from tracecat.tags.schemas import TagRead


class AgentFolderService(BaseWorkspaceService):
    """Manages agent preset folders using the materialized path pattern.

    Path format: ``/parent/child/`` with each segment being a folder name.
    Root folders have path ``/foldername/``.
    """

    service_name = "agent_folders"

    @staticmethod
    def _normalize_folder_path(path: str) -> str:
        """Normalize a folder path to materialized-path format."""
        if path == "/":
            return path
        return path if path.endswith("/") else f"{path}/"

    @require_scope("agent:create")
    async def create_folder(
        self, name: str, parent_path: str = "/", *, commit: bool = True
    ) -> AgentFolder:
        """Create a new agent folder.

        Concurrent creates of the same path are caught by the unique
        ``(path, workspace_id)`` constraint and surfaced as a validation
        error rather than an unhandled IntegrityError.
        """
        name = name.strip()
        if not name:
            raise TracecatValidationError("Folder name cannot be blank")
        if "/" in name:
            raise TracecatValidationError("Folder name cannot contain slashes")

        parent_path = self._normalize_folder_path(parent_path)
        if parent_path != "/" and not await self._folder_path_exists(parent_path):
            raise TracecatValidationError(f"Parent path {parent_path} not found")

        full_path = f"{parent_path}{name}/" if parent_path != "/" else f"/{name}/"
        if await self._folder_path_exists(full_path):
            raise TracecatConflictError(f"Folder {full_path} already exists")

        folder = AgentFolder(
            name=name,
            path=full_path,
            workspace_id=self.workspace_id,
        )
        self.session.add(folder)
        try:
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
        except IntegrityError as e:
            await self.session.rollback()
            raise TracecatConflictError(f"Folder {full_path} already exists") from e
        await self.session.refresh(folder)
        return folder

    async def get_folder(self, folder_id: AgentFolderID) -> AgentFolder | None:
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
        """List all folders in the subtree under ``parent_path``."""
        statement = select(AgentFolder).where(
            AgentFolder.workspace_id == self.workspace_id,
            AgentFolder.path.like(f"{parent_path}%"),
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_presets_in_folder(
        self, folder_id: AgentFolderID | None = None
    ) -> Sequence[AgentPreset]:
        """Get presets in the specified folder, or root-level presets if ``None``."""
        statement = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.folder_id == folder_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("agent:update")
    async def move_preset(
        self, preset_id: uuid.UUID, folder: AgentFolder | None = None
    ) -> AgentPreset:
        """Move a preset to a different folder (or to root when ``folder`` is None)."""
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
    async def rename_folder(
        self, folder_id: AgentFolderID, new_name: str
    ) -> AgentFolder:
        """Rename a folder, updating its path and all descendant paths."""
        new_name = new_name.strip()
        if not new_name:
            raise TracecatValidationError("Folder name cannot be blank")
        if "/" in new_name:
            raise TracecatValidationError("Folder name cannot contain slashes")

        folder = await self.get_folder(folder_id)
        if not folder:
            raise TracecatNotFoundError(f"Folder {folder_id} not found")

        old_path = folder.path
        parent_path = folder.parent_path
        new_path = (
            f"{parent_path}{new_name}/" if parent_path != "/" else f"/{new_name}/"
        )

        if new_path != old_path and await self._folder_path_exists(new_path):
            raise TracecatConflictError(f"Folder {new_path} already exists")

        descendants = await self._get_descendants(old_path)

        folder.name = new_name
        folder.path = new_path
        self.session.add(folder)

        for descendant in descendants:
            descendant.path = descendant.path.replace(old_path, new_path, 1)
            self.session.add(descendant)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise TracecatConflictError(f"Folder {new_path} already exists") from e
        await self.session.refresh(folder)
        return folder

    @require_scope("agent:update")
    async def move_folder(
        self,
        folder_id: AgentFolderID,
        new_parent_id: AgentFolderID | None,
    ) -> AgentFolder:
        """Move a folder to a different parent (or to root when ``new_parent_id`` is None)."""
        folder = await self.get_folder(folder_id)
        if not folder:
            raise TracecatNotFoundError(f"Folder {folder_id} not found")

        new_parent_path = "/"
        if new_parent_id is not None:
            new_parent = await self.get_folder(new_parent_id)
            if not new_parent:
                raise TracecatNotFoundError(f"Parent folder {new_parent_id} not found")
            new_parent_path = new_parent.path

            if folder.path == new_parent_path:
                raise TracecatValidationError("Cannot make a folder its own child")
            if new_parent.path.startswith(folder.path):
                raise TracecatValidationError("Cannot create cyclic folder structure")

        old_path = folder.path
        old_name = folder.name
        new_path = (
            f"{new_parent_path}{old_name}/"
            if new_parent_path != "/"
            else f"/{old_name}/"
        )

        if new_path != old_path and await self._folder_path_exists(new_path):
            raise TracecatConflictError(f"Folder {new_path} already exists")

        descendants = await self._get_descendants(old_path)

        folder.path = new_path
        self.session.add(folder)

        for descendant in descendants:
            descendant.path = descendant.path.replace(old_path, new_path, 1)
            self.session.add(descendant)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise TracecatConflictError(f"Folder {new_path} already exists") from e
        await self.session.refresh(folder)
        return folder

    @require_scope("agent:delete")
    async def delete_folder(
        self, folder_id: AgentFolderID, *, recursive: bool = False
    ) -> None:
        """Delete a folder.

        With ``recursive=False`` the folder must be empty (no subfolders, no
        presets). With ``recursive=True``, descendant folders are deleted and
        contained presets are detached (folder_id set to NULL via FK ON
        DELETE SET NULL — handled here explicitly so the operation is atomic
        and predictable).
        """
        folder = await self.get_folder(folder_id)
        if not folder:
            raise TracecatNotFoundError(f"Folder {folder_id} not found")

        if folder.path == "/":
            raise TracecatValidationError("Cannot delete root folder")

        if not recursive:
            has_children = await self._has_children(folder.path)
            has_presets = await self._has_presets(folder_id)
            if has_children or has_presets:
                raise TracecatValidationError(
                    "Folder is not empty. Move or delete its contents first."
                )
        else:
            descendants = await self._get_descendants(folder.path)
            descendant_ids = [d.id for d in descendants] + [folder.id]
            await self._detach_presets_from_folders(descendant_ids)
            for descendant in descendants:
                await self.session.delete(descendant)

        await self.session.delete(folder)
        await self.session.commit()

    async def get_directory_items(
        self,
        path: str = "/",
        *,
        order_by: Literal["asc", "desc"] = "desc",
    ) -> Sequence[DirectoryItem]:
        """Return folders and presets at the given path.

        Folder counts are computed in a single grouped query rather than
        per-folder ``has_children`` / ``has_presets`` round-trips, fixing the
        N+1 issue from the first attempt at this feature.
        """
        path = self._normalize_folder_path(path)

        if path != "/":
            folder = await self.get_folder_by_path(path)
            if not folder:
                raise TracecatNotFoundError(f"Folder {path} not found")
            folder_id = folder.id
        else:
            folder_id = None

        # Presets at this level
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

        # Direct child folders at this level
        if path == "/":
            folder_statement = select(AgentFolder).where(
                AgentFolder.workspace_id == self.workspace_id,
                func.length(AgentFolder.path)
                - func.length(func.replace(AgentFolder.path, "/", ""))
                == 2,
            )
        else:
            folder_statement = select(AgentFolder).where(
                AgentFolder.workspace_id == self.workspace_id,
                AgentFolder.path.startswith(path),
                AgentFolder.path != path,
                ~AgentFolder.path.like(f"{path}%/%/"),
            )
        folder_result = await self.session.execute(folder_statement)
        folders = folder_result.scalars().all()

        # Per-folder counts in a single grouped query (fixes N+1)
        child_counts = await self._child_folder_counts([f.path for f in folders])
        preset_counts = await self._preset_counts([f.id for f in folders])

        directory_items: list[DirectoryItem] = []

        for f in folders:
            num_items = (1 if child_counts.get(f.path, 0) > 0 else 0) + (
                1 if preset_counts.get(f.id, 0) > 0 else 0
            )
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
        """Return the full folder subtree starting from ``root_path``."""
        root_path = self._normalize_folder_path(root_path)
        statement = (
            select(AgentFolder)
            .where(
                AgentFolder.workspace_id == self.workspace_id,
                or_(
                    AgentFolder.path.startswith(root_path),
                    AgentFolder.path == root_path,
                ),
            )
            .order_by(AgentFolder.path)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    # --- Private helpers ---

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
                AgentFolder.path.startswith(path),
                AgentFolder.path != path,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one() > 0

    async def _has_presets(self, folder_id: AgentFolderID) -> bool:
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
            AgentFolder.path.startswith(path),
            AgentFolder.path != path,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def _child_folder_counts(self, paths: Sequence[str]) -> dict[str, int]:
        """Count direct child folders for each input path in one query."""
        if not paths:
            return {}

        # For each parent_path, count folders whose path starts with parent_path,
        # is not equal to parent_path, and contains no further nested separator.
        # We use a CASE expression keyed by each parent path.
        cases = []
        for p in paths:
            cases.append(
                (
                    AgentFolder.path.startswith(p)
                    & (AgentFolder.path != p)
                    & ~AgentFolder.path.like(f"{p}%/%/"),
                    p,
                )
            )
        parent_label = case(*cases, else_=None).label("parent_path")
        statement = (
            select(parent_label, func.count())
            .where(AgentFolder.workspace_id == self.workspace_id)
            .group_by(parent_label)
        )
        result = await self.session.execute(statement)
        return {
            row.parent_path: row[1] for row in result if row.parent_path is not None
        }

    async def _preset_counts(
        self, folder_ids: Sequence[AgentFolderID]
    ) -> dict[uuid.UUID, int]:
        """Count presets in each folder in one grouped query."""
        if not folder_ids:
            return {}
        statement = (
            select(AgentPreset.folder_id, func.count())
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.folder_id.in_(folder_ids),
            )
            .group_by(AgentPreset.folder_id)
        )
        result = await self.session.execute(statement)
        return {row[0]: row[1] for row in result if row[0] is not None}

    async def _detach_presets_from_folders(
        self, folder_ids: Sequence[AgentFolderID]
    ) -> None:
        """Set folder_id to NULL for all presets in the given folders."""
        if not folder_ids:
            return
        statement = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.folder_id.in_(folder_ids),
        )
        result = await self.session.execute(statement)
        for preset in result.scalars().all():
            preset.folder_id = None
            self.session.add(preset)
