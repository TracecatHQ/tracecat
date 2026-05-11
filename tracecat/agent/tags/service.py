"""Service for agent tag definitions and preset-tag linking."""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from slugify import slugify
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentPreset, AgentTag, AgentTagLink
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import AgentTagID
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tags.schemas import TagCreate, TagUpdate
from tracecat.tiers.enums import Entitlement


class AgentTagsService(BaseWorkspaceService):
    """Handles both agent tag definitions and preset-tag linking."""

    service_name = "agent_tags"

    # --- Tag definitions ---

    async def _commit_tag_definition_change(self, conflict_message: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as err:
            await self.session.rollback()
            raise TracecatConflictError(conflict_message) from err

    async def _get_tag(self, tag_id: AgentTagID) -> AgentTag:
        statement = select(AgentTag).where(
            AgentTag.workspace_id == self.workspace_id,
            AgentTag.id == tag_id,
        )
        result = await self.session.execute(statement)
        if tag := result.scalar_one_or_none():
            return tag
        raise TracecatNotFoundError("Agent tag not found")

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags(self) -> Sequence[AgentTag]:
        """List all agent tags in the workspace."""
        statement = select(AgentTag).where(AgentTag.workspace_id == self.workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags_paginated(
        self, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[AgentTag]:
        """List all agent tags in the workspace with cursor pagination."""
        paginator = BaseCursorPaginator(self.session)
        statement = select(AgentTag).where(AgentTag.workspace_id == self.workspace_id)

        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError("Invalid cursor for agent tags") from err

            cursor_created_at = cursor_data.sort_value
            if not isinstance(cursor_created_at, datetime):
                raise TracecatValidationError("Invalid cursor for agent tags")

            predicate = sa.or_(
                AgentTag.created_at < cursor_created_at,
                sa.and_(
                    AgentTag.created_at == cursor_created_at,
                    AgentTag.id < cursor_id,
                ),
            )
            if params.reverse:
                predicate = sa.or_(
                    AgentTag.created_at > cursor_created_at,
                    sa.and_(
                        AgentTag.created_at == cursor_created_at,
                        AgentTag.id > cursor_id,
                    ),
                )
            statement = statement.where(predicate)

        if params.reverse:
            statement = statement.order_by(AgentTag.created_at.asc(), AgentTag.id.asc())
        else:
            statement = statement.order_by(
                AgentTag.created_at.desc(), AgentTag.id.desc()
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
    async def get_tag(self, tag_id: AgentTagID) -> AgentTag:
        """Get an agent tag by ID."""
        return await self._get_tag(tag_id)

    async def get_tag_by_ref(self, ref: str) -> AgentTag:
        """Get an agent tag by its ref."""
        statement = select(AgentTag).where(
            AgentTag.workspace_id == self.workspace_id,
            AgentTag.ref == ref,
        )
        result = await self.session.execute(statement)
        if tag := result.scalar_one_or_none():
            return tag
        raise TracecatNotFoundError("Agent tag not found")

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_tag(self, tag: TagCreate) -> AgentTag:
        """Create a new agent tag."""
        ref = slugify(tag.name)

        existing = await self.session.execute(
            select(AgentTag).where(
                AgentTag.ref == ref,
                AgentTag.workspace_id == self.workspace_id,
            )
        )
        if existing.one_or_none():
            raise TracecatConflictError(f"Agent tag with slug '{ref}' already exists")

        db_tag = AgentTag(
            name=tag.name,
            ref=ref,
            workspace_id=self.workspace_id,
            color=tag.color,
        )
        self.session.add(db_tag)
        await self._commit_tag_definition_change(
            f"Agent tag with slug '{ref}' already exists"
        )
        await self.session.refresh(db_tag)
        return db_tag

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_tag(self, tag: AgentTag, params: TagUpdate) -> AgentTag:
        """Update an agent tag and regenerate ref if name changed."""
        if params.name and params.name != tag.name:
            new_ref = slugify(params.name)
            if new_ref != tag.ref:
                existing = await self.session.execute(
                    select(AgentTag).where(
                        AgentTag.workspace_id == self.workspace_id,
                        AgentTag.ref == new_ref,
                        AgentTag.id != tag.id,
                    )
                )
                if existing.one_or_none():
                    raise TracecatConflictError(
                        f"Agent tag with slug '{new_ref}' already exists"
                    )
                tag.ref = new_ref

        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)

        await self._commit_tag_definition_change(
            f"Agent tag with slug '{tag.ref}' already exists"
        )
        await self.session.refresh(tag)
        return tag

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_tag(self, tag: AgentTag) -> None:
        """Delete an agent tag definition."""
        await self.session.delete(tag)
        await self.session.commit()

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_tag_by_id(self, tag_id: AgentTagID) -> None:
        """Delete an agent tag definition by ID."""
        tag = await self._get_tag(tag_id)
        await self.session.delete(tag)
        await self.session.commit()

    # --- Preset-tag linking ---

    async def _require_preset_and_tag_in_workspace(
        self, preset_id: uuid.UUID, tag_id: AgentTagID
    ) -> None:
        preset_exists = exists(
            select(AgentPreset.id).where(
                AgentPreset.id == preset_id,
                AgentPreset.workspace_id == self.workspace_id,
            )
        )
        tag_exists = exists(
            select(AgentTag.id).where(
                AgentTag.id == tag_id,
                AgentTag.workspace_id == self.workspace_id,
            )
        )
        is_allowed = await self.session.scalar(select(preset_exists & tag_exists))
        if not is_allowed:
            raise TracecatNotFoundError("Agent preset or tag not found")

    async def _require_preset_in_workspace(self, preset_id: uuid.UUID) -> None:
        preset_exists = exists(
            select(AgentPreset.id).where(
                AgentPreset.id == preset_id,
                AgentPreset.workspace_id == self.workspace_id,
            )
        )
        is_allowed = await self.session.scalar(select(preset_exists))
        if not is_allowed:
            raise TracecatNotFoundError("Agent preset not found")

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags_for_preset(self, preset_id: uuid.UUID) -> Sequence[AgentTag]:
        """List all tags on a preset."""
        await self._require_preset_in_workspace(preset_id)
        stmt = (
            select(AgentTag)
            .join(AgentTagLink, AgentTag.id == AgentTagLink.tag_id)
            .join(AgentPreset, AgentPreset.id == AgentTagLink.preset_id)
            .where(
                AgentTagLink.preset_id == preset_id,
                AgentPreset.workspace_id == self.workspace_id,
                AgentTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset_tag(
        self, preset_id: uuid.UUID, tag_id: AgentTagID
    ) -> AgentTagLink:
        """Get a preset-tag association."""
        stmt = (
            select(AgentTagLink)
            .join(AgentPreset, AgentPreset.id == AgentTagLink.preset_id)
            .join(AgentTag, AgentTag.id == AgentTagLink.tag_id)
            .where(
                AgentTagLink.preset_id == preset_id,
                AgentTagLink.tag_id == tag_id,
                AgentPreset.workspace_id == self.workspace_id,
                AgentTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        if link := result.scalar_one_or_none():
            return link
        raise TracecatNotFoundError("Tag not found")

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def add_preset_tag(
        self, preset_id: uuid.UUID, tag_id: AgentTagID
    ) -> AgentTagLink:
        """Add a tag to an agent preset."""
        await self._require_preset_and_tag_in_workspace(preset_id, tag_id)
        stmt = (
            pg_insert(AgentTagLink)
            .values(preset_id=preset_id, tag_id=tag_id)
            .on_conflict_do_nothing(index_elements=["tag_id", "preset_id"])
            .returning(AgentTagLink)
        )
        result = await self.session.execute(stmt)
        link = result.scalar_one_or_none()
        if link is None:
            existing = await self.session.execute(
                select(AgentTagLink).where(
                    AgentTagLink.preset_id == preset_id,
                    AgentTagLink.tag_id == tag_id,
                )
            )
            link = existing.scalar_one_or_none()
            if link is None:
                raise TracecatNotFoundError("Agent preset or tag not found")
        await self.session.commit()
        return link

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def remove_preset_tag(self, link: AgentTagLink) -> None:
        """Remove a tag from an agent preset."""
        await self.session.delete(link)
        await self.session.commit()
