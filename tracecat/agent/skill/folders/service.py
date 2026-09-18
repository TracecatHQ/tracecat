"""Service for managing skill folders using the materialized path pattern."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Literal

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from tracecat.agent.skill.folders.schemas import (
    DirectoryItem,
    SkillDirectoryItem,
    SkillFolderDirectoryItem,
)
from tracecat.authz.controls import require_scope
from tracecat.db.models import Skill, SkillFolder
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tags.schemas import TagRead
from tracecat.tiers.enums import Entitlement


class SkillFolderErrorCode(StrEnum):
    """Machine-readable skill folder validation error codes."""

    CONFLICT = "skill_folder_conflict"
    NOT_FOUND = "skill_folder_not_found"
    PARENT_NOT_FOUND = "skill_folder_parent_not_found"
    INVALID = "skill_folder_invalid"


class SkillFolderService(BaseWorkspaceService):
    """Service for managing skill folders using the materialized path pattern."""

    service_name = "skill_folders"

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
        code: SkillFolderErrorCode,
    ) -> TracecatValidationError:
        return TracecatValidationError(message, detail={"code": code.value})

    @staticmethod
    def _normalize_folder_name(name: str) -> str:
        """Trim folder names and reject blank values."""
        normalized_name = name.strip()
        if not normalized_name:
            raise SkillFolderService._folder_validation_error(
                "Folder name cannot be empty",
                code=SkillFolderErrorCode.INVALID,
            )
        if "/" in normalized_name:
            raise SkillFolderService._folder_validation_error(
                "Folder name cannot contain slashes",
                code=SkillFolderErrorCode.INVALID,
            )
        return normalized_name

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
                code=SkillFolderErrorCode.CONFLICT,
            ) from exc

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_folder(
        self, name: str, parent_path: str = "/", commit: bool = True
    ) -> SkillFolder:
        """Create a new skill folder."""
        normalized_name = self._normalize_folder_name(name)

        parent_path = self._normalize_folder_path(parent_path)

        if parent_path != "/":
            parent_exists = await self._folder_path_exists(parent_path)
            if not parent_exists:
                raise self._folder_validation_error(
                    f"Parent path {parent_path} not found",
                    code=SkillFolderErrorCode.PARENT_NOT_FOUND,
                )

        full_path = (
            f"{parent_path}{normalized_name}/"
            if parent_path != "/"
            else f"/{normalized_name}/"
        )

        path_exists = await self._folder_path_exists(full_path)
        if path_exists:
            raise self._folder_validation_error(
                f"Folder {full_path} already exists",
                code=SkillFolderErrorCode.CONFLICT,
            )

        folder = SkillFolder(
            name=normalized_name,
            path=full_path,
            workspace_id=self.workspace_id,
        )
        self.session.add(folder)
        await self._write_folder_change(conflict_path=full_path, commit=commit)
        await self.session.refresh(folder)
        return folder

    async def _get_folder(self, folder_id: uuid.UUID) -> SkillFolder | None:
        statement = select(SkillFolder).where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.id == folder_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def _get_folder_by_path(self, path: str) -> SkillFolder | None:
        path = self._normalize_folder_path(path)

        statement = select(SkillFolder).where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.path == path,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_folder(self, folder_id: uuid.UUID) -> SkillFolder | None:
        """Get a folder by ID."""
        return await self._get_folder(folder_id)

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_folder_by_path(self, path: str) -> SkillFolder | None:
        """Get a folder by its path."""
        return await self._get_folder_by_path(path)

    async def _require_existing_parent_path(self, parent_path: str) -> str:
        parent_path = self._normalize_folder_path(parent_path)
        if parent_path != "/":
            parent_exists = await self._folder_path_exists(parent_path)
            if not parent_exists:
                raise self._folder_validation_error(
                    f"Parent path {parent_path} not found",
                    code=SkillFolderErrorCode.PARENT_NOT_FOUND,
                )
        return parent_path

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_folders(self, parent_path: str = "/") -> Sequence[SkillFolder]:
        """List all folders within the specified parent path subtree."""
        parent_path = await self._require_existing_parent_path(parent_path)

        statement = select(SkillFolder).where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.path.startswith(parent_path, autoescape=True),
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_folders_paginated(
        self,
        parent_path: str = "/",
        params: CursorPaginationParams | None = None,
    ) -> CursorPaginatedResponse[SkillFolder]:
        """List folders within a parent path subtree with cursor pagination."""
        params = params or CursorPaginationParams()
        parent_path = await self._require_existing_parent_path(parent_path)
        paginator = BaseCursorPaginator(self.session)
        statement = select(SkillFolder).where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.path.startswith(parent_path, autoescape=True),
            SkillFolder.path != parent_path,
        )

        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError(
                    "Invalid cursor for skill folders"
                ) from err

            cursor_created_at = cursor_data.sort_value
            if not isinstance(cursor_created_at, datetime):
                raise TracecatValidationError("Invalid cursor for skill folders")

            predicate = sa.or_(
                SkillFolder.created_at < cursor_created_at,
                sa.and_(
                    SkillFolder.created_at == cursor_created_at,
                    SkillFolder.id < cursor_id,
                ),
            )
            if params.reverse:
                predicate = sa.or_(
                    SkillFolder.created_at > cursor_created_at,
                    sa.and_(
                        SkillFolder.created_at == cursor_created_at,
                        SkillFolder.id > cursor_id,
                    ),
                )
            statement = statement.where(predicate)

        if params.reverse:
            statement = statement.order_by(
                SkillFolder.created_at.asc(), SkillFolder.id.asc()
            )
        else:
            statement = statement.order_by(
                SkillFolder.created_at.desc(), SkillFolder.id.desc()
            )
        statement = statement.limit(params.limit + 1)

        folders = (await self.session.execute(statement)).scalars().all()
        has_more = len(folders) > params.limit
        items = list(folders[: params.limit])

        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = paginator.encode_cursor(
                last.id,
                sort_column="created_at",
                sort_value=last.created_at,
            )

        prev_cursor = None
        if params.cursor and items:
            first = items[0]
            prev_cursor = paginator.encode_cursor(
                first.id,
                sort_column="created_at",
                sort_value=first.created_at,
            )

        if params.reverse:
            items.reverse()
            next_cursor, prev_cursor = prev_cursor, next_cursor
            has_more, has_previous = params.cursor is not None, has_more
        else:
            has_previous = params.cursor is not None

        return CursorPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
        )

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def move_skill(
        self, skill_id: uuid.UUID, folder: SkillFolder | None = None
    ) -> Skill:
        """Move a skill to a different folder."""
        statement = select(Skill).where(
            Skill.workspace_id == self.workspace_id,
            Skill.id == skill_id,
            Skill.deleted_at.is_(None),
            Skill.archived_at.is_(None),
        )
        result = await self.session.execute(statement)
        skill = result.scalar_one_or_none()
        if not skill:
            raise TracecatNotFoundError(f"Skill {skill_id} not found")

        skill.folder_id = folder.id if folder else None
        self.session.add(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def rename_folder(self, folder_id: uuid.UUID, new_name: str) -> SkillFolder:
        """Rename a folder. Updates the folder name and path."""
        normalized_name = self._normalize_folder_name(new_name)

        folder = await self.get_folder(folder_id)
        if not folder:
            raise self._folder_validation_error(
                f"Folder {folder_id} not found",
                code=SkillFolderErrorCode.NOT_FOUND,
            )

        old_path = folder.path
        parent_path = self._get_parent_path(folder.path)
        new_path = (
            f"{parent_path}{normalized_name}/"
            if parent_path != "/"
            else f"/{normalized_name}/"
        )

        if new_path != old_path:
            path_exists = await self._folder_path_exists(new_path)
            if path_exists:
                raise self._folder_validation_error(
                    f"Folder {new_path} already exists",
                    code=SkillFolderErrorCode.CONFLICT,
                )

        folder.name = normalized_name
        folder.path = new_path
        self.session.add(folder)

        if new_path != old_path:
            await self._update_descendant_paths(old_path, new_path)

        await self._write_folder_change(conflict_path=new_path, commit=True)
        await self.session.refresh(folder)
        return folder

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def move_folder(
        self, folder_id: uuid.UUID, new_parent_id: uuid.UUID | None
    ) -> SkillFolder:
        """Move a folder to a different parent."""
        folder = await self.get_folder(folder_id)
        if not folder:
            raise self._folder_validation_error(
                f"Folder {folder_id} not found",
                code=SkillFolderErrorCode.NOT_FOUND,
            )

        new_parent_path = "/"
        if new_parent_id is not None:
            new_parent = await self.get_folder(new_parent_id)
            if not new_parent:
                raise self._folder_validation_error(
                    f"Parent folder {new_parent_id} not found",
                    code=SkillFolderErrorCode.PARENT_NOT_FOUND,
                )
            new_parent_path = new_parent.path

            if folder.path == new_parent_path:
                raise self._folder_validation_error(
                    "Cannot make a folder its own child",
                    code=SkillFolderErrorCode.INVALID,
                )
            if new_parent.path.startswith(folder.path):
                raise self._folder_validation_error(
                    "Cannot create cyclic folder structure",
                    code=SkillFolderErrorCode.INVALID,
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
                    code=SkillFolderErrorCode.CONFLICT,
                )

        folder.path = new_path
        self.session.add(folder)

        if new_path != old_path:
            await self._update_descendant_paths(old_path, new_path)

        await self._write_folder_change(conflict_path=new_path, commit=True)
        await self.session.refresh(folder)
        return folder

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_folder(
        self, folder_id: uuid.UUID, recursive: bool = False
    ) -> None:
        """Delete a folder."""
        folder = await self._get_folder(folder_id)
        if not folder:
            raise self._folder_validation_error(
                f"Folder {folder_id} not found",
                code=SkillFolderErrorCode.NOT_FOUND,
            )

        if folder.path == "/":
            raise self._folder_validation_error(
                "Cannot delete root folder",
                code=SkillFolderErrorCode.INVALID,
            )

        if not recursive:
            has_children = await self._has_children(folder.path)
            has_skills = await self._has_skills(folder_id)
            if has_children or has_skills:
                raise self._folder_validation_error(
                    "Folder is not empty. Please move or delete its contents first.",
                    code=SkillFolderErrorCode.INVALID,
                )
        else:
            folder_ids = select(SkillFolder.id).where(
                SkillFolder.workspace_id == self.workspace_id,
                SkillFolder.path.startswith(folder.path, autoescape=True),
            )
            await self.session.execute(
                sa.update(Skill)
                .where(
                    Skill.workspace_id == self.workspace_id,
                    Skill.folder_id.in_(folder_ids),
                )
                .values(folder_id=None)
                .execution_options(synchronize_session=False)
            )
            await self.session.execute(
                sa.delete(SkillFolder)
                .where(
                    SkillFolder.workspace_id == self.workspace_id,
                    SkillFolder.path.startswith(folder.path, autoescape=True),
                    SkillFolder.path != folder.path,
                )
                .execution_options(synchronize_session=False)
            )

        await self.session.delete(folder)
        await self.session.commit()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_directory_items(
        self, path: str = "/", *, order_by: Literal["asc", "desc"] = "desc"
    ) -> Sequence[DirectoryItem]:
        """Get all directory items (skills and folders) in the given path."""
        path, folder_id = await self._get_directory_context(path)

        skill_statement = (
            select(Skill)
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.folder_id == folder_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
            .order_by(
                Skill.created_at.desc()
                if order_by == "desc"
                else Skill.created_at.asc()
            )
            .options(selectinload(Skill.tags))
        )
        skill_result = await self.session.execute(skill_statement)
        skills = skill_result.scalars().all()

        folder_statement = self._directory_folders_statement(path)
        folder_result = await self.session.execute(folder_statement)
        folders = folder_result.scalars().all()

        return await self._build_directory_items(
            folders=folders, skills=skills, path=path
        )

    async def _get_directory_context(self, path: str) -> tuple[str, uuid.UUID | None]:
        path = self._normalize_folder_path(path)

        if path != "/":
            folder = await self._get_folder_by_path(path)
            if not folder:
                raise TracecatNotFoundError(f"Folder {path} not found")
            return path, folder.id

        return path, None

    def _directory_folders_statement(self, path: str) -> sa.Select[tuple[SkillFolder]]:
        path_depth = path.count("/") + 1
        return select(SkillFolder).where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.path.startswith(path, autoescape=True),
            SkillFolder.path != path,
            func.length(SkillFolder.path)
            - func.length(func.replace(SkillFolder.path, "/", ""))
            == path_depth,
        )

    async def _build_directory_items(
        self,
        *,
        folders: Sequence[SkillFolder],
        skills: Sequence[Skill],
        path: str,
        preserve_order: Sequence[SkillFolder | Skill] | None = None,
    ) -> list[DirectoryItem]:
        directory_items: list[DirectoryItem] = []
        path_depth = path.count("/") + 1
        folder_ids = [folder.id for folder in folders]
        folder_paths = {folder.path for folder in folders}
        skill_counts_by_folder_id: dict[uuid.UUID, int] = {}
        child_folder_counts_by_path: dict[str, int] = {}

        if folder_ids:
            skill_folder_result = await self.session.execute(
                select(Skill.folder_id, func.count(Skill.id))
                .where(
                    Skill.workspace_id == self.workspace_id,
                    Skill.folder_id.in_(folder_ids),
                    Skill.deleted_at.is_(None),
                    Skill.archived_at.is_(None),
                )
                .group_by(Skill.folder_id)
            )
            skill_counts_by_folder_id = {
                folder_id: skill_count
                for folder_id, skill_count in skill_folder_result.tuples().all()
                if folder_id is not None
            }

            child_depth = path_depth + 1
            child_path_result = await self.session.execute(
                select(SkillFolder.path).where(
                    SkillFolder.workspace_id == self.workspace_id,
                    SkillFolder.path.startswith(path, autoescape=True),
                    SkillFolder.path != path,
                    func.length(SkillFolder.path)
                    - func.length(func.replace(SkillFolder.path, "/", ""))
                    == child_depth,
                )
            )
            for child_path in child_path_result.scalars().all():
                parent_path = self._get_parent_path(child_path)
                if parent_path in folder_paths:
                    child_folder_counts_by_path[parent_path] = (
                        child_folder_counts_by_path.get(parent_path, 0) + 1
                    )

        items_by_id: dict[uuid.UUID, DirectoryItem] = {}
        for f in folders:
            num_items = child_folder_counts_by_path.get(
                f.path, 0
            ) + skill_counts_by_folder_id.get(f.id, 0)
            items_by_id[f.id] = SkillFolderDirectoryItem(
                type="folder",
                num_items=num_items,
                id=f.id,
                name=f.name,
                path=f.path,
                workspace_id=f.workspace_id,
                created_at=f.created_at,
                updated_at=f.updated_at,
            )

        for skill in skills:
            items_by_id[skill.id] = SkillDirectoryItem(
                type="skill",
                id=skill.id,
                name=skill.name,
                slug=skill.slug or skill.name,
                description=skill.description,
                current_version_id=skill.current_version_id,
                folder_id=skill.folder_id,
                tags=[
                    TagRead.model_validate(tag, from_attributes=True)
                    for tag in skill.tags
                ],
                created_at=skill.created_at,
                updated_at=skill.updated_at,
            )

        if preserve_order is not None:
            return [items_by_id[row.id] for row in preserve_order]

        for f in folders:
            directory_items.append(items_by_id[f.id])

        for skill in skills:
            directory_items.append(items_by_id[skill.id])

        return directory_items

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_folder_tree(self, root_path: str = "/") -> Sequence[SkillFolder]:
        """Get the full folder tree starting from the given root path."""
        root_path = self._normalize_folder_path(root_path)

        statement = (
            select(SkillFolder)
            .where(
                SkillFolder.workspace_id == self.workspace_id,
                SkillFolder.path.startswith(root_path, autoescape=True),
            )
            .order_by(SkillFolder.path)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    # Private helpers

    async def _folder_path_exists(self, path: str) -> bool:
        path = self._normalize_folder_path(path)
        exists_clause = sa.exists().where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.path == path,
        )
        return bool(await self.session.scalar(select(exists_clause)))

    async def _update_descendant_paths(self, old_path: str, new_path: str) -> None:
        old_path = self._normalize_folder_path(old_path)
        new_path = self._normalize_folder_path(new_path)
        await self.session.execute(
            sa.update(SkillFolder)
            .where(
                SkillFolder.workspace_id == self.workspace_id,
                SkillFolder.path.startswith(old_path, autoescape=True),
                SkillFolder.path != old_path,
            )
            .values(
                path=func.concat(
                    new_path,
                    func.substring(SkillFolder.path, len(old_path) + 1),
                )
            )
            .execution_options(synchronize_session=False)
        )

    async def _has_children(self, path: str) -> bool:
        path = self._normalize_folder_path(path)
        exists_clause = sa.exists().where(
            SkillFolder.workspace_id == self.workspace_id,
            SkillFolder.path.startswith(path, autoescape=True),
            SkillFolder.path != path,
        )
        return bool(await self.session.scalar(select(exists_clause)))

    async def _has_skills(self, folder_id: uuid.UUID) -> bool:
        exists_clause = sa.exists().where(
            Skill.workspace_id == self.workspace_id,
            Skill.folder_id == folder_id,
            Skill.deleted_at.is_(None),
            Skill.archived_at.is_(None),
        )
        return bool(await self.session.scalar(select(exists_clause)))
