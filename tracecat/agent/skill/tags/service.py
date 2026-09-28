"""Service for skill tag definitions and skill-tag linking."""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from slugify import slugify
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracecat.authz.controls import require_scope
from tracecat.db.models import Skill, SkillTag, SkillTagLink
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import SkillTagID
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tags.schemas import TagCreate, TagUpdate
from tracecat.tiers.enums import Entitlement


class SkillTagsService(BaseWorkspaceService):
    """Handles both skill tag definitions and skill-tag linking."""

    service_name = "skill_tags"

    # --- Tag definitions ---

    async def _commit_tag_definition_change(self, conflict_message: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatConflictError(conflict_message) from err

    async def _get_tag(self, tag_id: SkillTagID) -> SkillTag:
        statement = select(SkillTag).where(
            SkillTag.workspace_id == self.workspace_id,
            SkillTag.id == tag_id,
        )
        result = await self.session.execute(statement)
        if tag := result.scalar_one_or_none():
            return tag
        raise TracecatNotFoundError("Skill tag not found")

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags(self) -> Sequence[SkillTag]:
        """List all skill tags in the workspace."""
        statement = select(SkillTag).where(SkillTag.workspace_id == self.workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags_paginated(
        self, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[SkillTag]:
        """List all skill tags in the workspace with cursor pagination."""
        paginator = BaseCursorPaginator(self.session)
        statement = select(SkillTag).where(SkillTag.workspace_id == self.workspace_id)

        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError("Invalid cursor for skill tags") from err

            cursor_created_at = cursor_data.sort_value
            if not isinstance(cursor_created_at, datetime):
                raise TracecatValidationError("Invalid cursor for skill tags")

            predicate = sa.or_(
                SkillTag.created_at < cursor_created_at,
                sa.and_(
                    SkillTag.created_at == cursor_created_at,
                    SkillTag.id < cursor_id,
                ),
            )
            if params.reverse:
                predicate = sa.or_(
                    SkillTag.created_at > cursor_created_at,
                    sa.and_(
                        SkillTag.created_at == cursor_created_at,
                        SkillTag.id > cursor_id,
                    ),
                )
            statement = statement.where(predicate)

        if params.reverse:
            statement = statement.order_by(SkillTag.created_at.asc(), SkillTag.id.asc())
        else:
            statement = statement.order_by(
                SkillTag.created_at.desc(), SkillTag.id.desc()
            )
        statement = statement.limit(params.limit + 1)

        tags = (await self.session.execute(statement)).scalars().all()
        has_more = len(tags) > params.limit
        items = list(tags[: params.limit])

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

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_tag(self, tag_id: SkillTagID) -> SkillTag:
        """Get a skill tag by ID."""
        return await self._get_tag(tag_id)

    async def get_tag_by_ref(self, ref: str) -> SkillTag:
        """Get a skill tag by its ref."""
        statement = select(SkillTag).where(
            SkillTag.workspace_id == self.workspace_id,
            SkillTag.ref == ref,
        )
        result = await self.session.execute(statement)
        if tag := result.scalar_one_or_none():
            return tag
        raise TracecatNotFoundError("Skill tag not found")

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_tag(self, tag: TagCreate) -> SkillTag:
        """Create a new skill tag."""
        ref = slugify(tag.name)

        existing = await self.session.execute(
            select(SkillTag).where(
                SkillTag.ref == ref,
                SkillTag.workspace_id == self.workspace_id,
            )
        )
        if existing.one_or_none():
            raise TracecatConflictError(f"Skill tag with slug '{ref}' already exists")

        db_tag = SkillTag(
            name=tag.name,
            ref=ref,
            workspace_id=self.workspace_id,
            color=tag.color,
        )
        self.session.add(db_tag)
        await self._commit_tag_definition_change(
            f"Skill tag with slug '{ref}' already exists"
        )
        await self.session.refresh(db_tag)
        return db_tag

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_tag(self, tag: SkillTag, params: TagUpdate) -> SkillTag:
        """Update a skill tag and regenerate ref if name changed."""
        if params.name and params.name != tag.name:
            new_ref = slugify(params.name)
            if new_ref != tag.ref:
                existing = await self.session.execute(
                    select(SkillTag).where(
                        SkillTag.workspace_id == self.workspace_id,
                        SkillTag.ref == new_ref,
                        SkillTag.id != tag.id,
                    )
                )
                if existing.one_or_none():
                    raise TracecatConflictError(
                        f"Skill tag with slug '{new_ref}' already exists"
                    )
                tag.ref = new_ref

        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)

        await self._commit_tag_definition_change(
            f"Skill tag with slug '{tag.ref}' already exists"
        )
        await self.session.refresh(tag)
        return tag

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_tag(self, tag: SkillTag) -> None:
        """Delete a skill tag definition."""
        await self.session.delete(tag)
        await self.session.commit()

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_tag_by_id(self, tag_id: SkillTagID) -> None:
        """Delete a skill tag definition by ID."""
        tag = await self._get_tag(tag_id)
        await self.session.delete(tag)
        await self.session.commit()

    # --- Skill-tag linking ---

    async def _require_skill_and_tag_in_workspace(
        self, skill_id: uuid.UUID, tag_id: SkillTagID
    ) -> None:
        skill_exists = exists(
            select(Skill.id).where(
                Skill.id == skill_id,
                Skill.workspace_id == self.workspace_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
        )
        tag_exists = exists(
            select(SkillTag.id).where(
                SkillTag.id == tag_id,
                SkillTag.workspace_id == self.workspace_id,
            )
        )
        is_allowed = await self.session.scalar(select(skill_exists & tag_exists))
        if not is_allowed:
            raise TracecatNotFoundError("Skill or tag not found")

    async def _require_skill_in_workspace(self, skill_id: uuid.UUID) -> None:
        skill_exists = exists(
            select(Skill.id).where(
                Skill.id == skill_id,
                Skill.workspace_id == self.workspace_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
            )
        )
        is_allowed = await self.session.scalar(select(skill_exists))
        if not is_allowed:
            raise TracecatNotFoundError("Skill not found")

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags_for_skill(self, skill_id: uuid.UUID) -> Sequence[SkillTag]:
        """List all tags on a skill."""
        await self._require_skill_in_workspace(skill_id)
        stmt = (
            select(SkillTag)
            .join(SkillTagLink, SkillTag.id == SkillTagLink.tag_id)
            .join(Skill, Skill.id == SkillTagLink.skill_id)
            .where(
                SkillTagLink.skill_id == skill_id,
                Skill.workspace_id == self.workspace_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
                SkillTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags_for_skill_paginated(
        self,
        skill_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[SkillTag]:
        """List tags on a skill with cursor pagination."""
        await self._require_skill_in_workspace(skill_id)
        paginator = BaseCursorPaginator(self.session)
        statement = (
            select(SkillTag)
            .join(SkillTagLink, SkillTag.id == SkillTagLink.tag_id)
            .join(Skill, Skill.id == SkillTagLink.skill_id)
            .where(
                SkillTagLink.skill_id == skill_id,
                Skill.workspace_id == self.workspace_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
                SkillTag.workspace_id == self.workspace_id,
            )
        )

        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError("Invalid cursor for skill tags") from err

            cursor_created_at = cursor_data.sort_value
            if not isinstance(cursor_created_at, datetime):
                raise TracecatValidationError("Invalid cursor for skill tags")

            predicate = sa.or_(
                SkillTag.created_at < cursor_created_at,
                sa.and_(
                    SkillTag.created_at == cursor_created_at,
                    SkillTag.id < cursor_id,
                ),
            )
            if params.reverse:
                predicate = sa.or_(
                    SkillTag.created_at > cursor_created_at,
                    sa.and_(
                        SkillTag.created_at == cursor_created_at,
                        SkillTag.id > cursor_id,
                    ),
                )
            statement = statement.where(predicate)

        if params.reverse:
            statement = statement.order_by(SkillTag.created_at.asc(), SkillTag.id.asc())
        else:
            statement = statement.order_by(
                SkillTag.created_at.desc(), SkillTag.id.desc()
            )
        statement = statement.limit(params.limit + 1)

        tags = (await self.session.execute(statement)).scalars().all()
        has_more = len(tags) > params.limit
        items = list(tags[: params.limit])

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
    async def get_skill_tag(
        self, skill_id: uuid.UUID, tag_id: SkillTagID
    ) -> SkillTagLink:
        """Get a skill-tag association."""
        stmt = (
            select(SkillTagLink)
            .join(Skill, Skill.id == SkillTagLink.skill_id)
            .join(SkillTag, SkillTag.id == SkillTagLink.tag_id)
            .where(
                SkillTagLink.skill_id == skill_id,
                SkillTagLink.tag_id == tag_id,
                Skill.workspace_id == self.workspace_id,
                Skill.deleted_at.is_(None),
                Skill.archived_at.is_(None),
                SkillTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        if link := result.scalar_one_or_none():
            return link
        raise TracecatNotFoundError("Tag not found")

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def add_skill_tag(
        self, skill_id: uuid.UUID, tag_id: SkillTagID
    ) -> SkillTagLink:
        """Add a tag to a skill."""
        await self._require_skill_and_tag_in_workspace(skill_id, tag_id)
        stmt = (
            pg_insert(SkillTagLink)
            .values(skill_id=skill_id, tag_id=tag_id)
            .on_conflict_do_nothing(index_elements=["tag_id", "skill_id"])
            .returning(SkillTagLink)
        )
        result = await self.session.execute(stmt)
        link = result.scalar_one_or_none()
        if link is None:
            existing = await self.session.execute(
                select(SkillTagLink).where(
                    SkillTagLink.skill_id == skill_id,
                    SkillTagLink.tag_id == tag_id,
                )
            )
            link = existing.scalar_one_or_none()
            if link is None:
                raise TracecatNotFoundError("Skill or tag not found")
        await self.session.commit()
        return link

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def remove_skill_tag(self, link: SkillTagLink) -> None:
        """Remove a tag from a skill."""
        await self.session.delete(link)
        await self.session.commit()
