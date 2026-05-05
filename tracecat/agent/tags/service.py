"""Service for agent tag definitions and preset-tag links."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from tracecat.agent.tags.schemas import AgentTagCreate, AgentTagUpdate
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentPreset, AgentTag, AgentTagLink
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.identifiers import AgentTagID
from tracecat.service import BaseWorkspaceService


class AgentTagsService(BaseWorkspaceService):
    """Manages both agent tag definitions and preset-tag link rows."""

    service_name = "agent_tags"

    # --- Tag definitions ---

    async def list_tags(self) -> Sequence[AgentTag]:
        statement = select(AgentTag).where(AgentTag.workspace_id == self.workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_tag(self, tag_id: AgentTagID) -> AgentTag:
        statement = select(AgentTag).where(
            AgentTag.workspace_id == self.workspace_id,
            AgentTag.id == tag_id,
        )
        result = await self.session.execute(statement)
        tag = result.scalar_one_or_none()
        if tag is None:
            raise TracecatNotFoundError(f"Agent tag {tag_id} not found")
        return tag

    async def get_tag_by_ref(self, ref: str) -> AgentTag:
        statement = select(AgentTag).where(
            AgentTag.workspace_id == self.workspace_id,
            AgentTag.ref == ref,
        )
        result = await self.session.execute(statement)
        tag = result.scalar_one_or_none()
        if tag is None:
            raise TracecatNotFoundError(f"Agent tag with ref '{ref}' not found")
        return tag

    @require_scope("agent:create")
    async def create_tag(self, params: AgentTagCreate) -> AgentTag:
        ref = slugify(params.name)
        if not ref:
            raise TracecatConflictError(
                "Tag name must contain at least one alphanumeric character"
            )

        existing = await self.session.execute(
            select(AgentTag).where(
                AgentTag.ref == ref,
                AgentTag.workspace_id == self.workspace_id,
            )
        )
        if existing.scalar_one_or_none():
            raise TracecatConflictError(f"Agent tag with slug '{ref}' already exists")

        tag = AgentTag(
            name=params.name,
            ref=ref,
            workspace_id=self.workspace_id,
            color=params.color,
        )
        self.session.add(tag)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise TracecatConflictError(
                f"Agent tag '{params.name}' already exists"
            ) from e
        await self.session.refresh(tag)
        return tag

    @require_scope("agent:update")
    async def update_tag(self, tag: AgentTag, params: AgentTagUpdate) -> AgentTag:
        if params.name and params.name != tag.name:
            new_ref = slugify(params.name)
            if not new_ref:
                raise TracecatConflictError(
                    "Tag name must contain at least one alphanumeric character"
                )
            if new_ref != tag.ref:
                existing = await self.session.execute(
                    select(AgentTag).where(
                        AgentTag.workspace_id == self.workspace_id,
                        AgentTag.ref == new_ref,
                        AgentTag.id != tag.id,
                    )
                )
                if existing.scalar_one_or_none():
                    raise TracecatConflictError(
                        f"Agent tag with slug '{new_ref}' already exists"
                    )
                tag.ref = new_ref

        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise TracecatConflictError("Agent tag conflict on update") from e
        await self.session.refresh(tag)
        return tag

    @require_scope("agent:delete")
    async def delete_tag(self, tag: AgentTag) -> None:
        await self.session.delete(tag)
        await self.session.commit()

    # --- Preset-tag links ---

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

    async def list_tags_for_preset(self, preset_id: uuid.UUID) -> Sequence[AgentTag]:
        statement = (
            select(AgentTag)
            .join(AgentTagLink, AgentTag.id == AgentTagLink.tag_id)
            .join(AgentPreset, AgentPreset.id == AgentTagLink.preset_id)
            .where(
                AgentTagLink.preset_id == preset_id,
                AgentPreset.workspace_id == self.workspace_id,
                AgentTag.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_preset_tag_link(
        self, preset_id: uuid.UUID, tag_id: AgentTagID
    ) -> AgentTagLink:
        statement = (
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
        result = await self.session.execute(statement)
        link = result.scalar_one_or_none()
        if link is None:
            raise TracecatNotFoundError("Tag is not attached to this preset")
        return link

    @require_scope("agent:update")
    async def add_preset_tag(
        self, preset_id: uuid.UUID, tag_id: AgentTagID
    ) -> AgentTagLink:
        """Attach a tag to a preset. Idempotent — re-adding is a no-op."""
        await self._require_preset_and_tag_in_workspace(preset_id, tag_id)
        statement = (
            pg_insert(AgentTagLink)
            .values(preset_id=preset_id, tag_id=tag_id)
            .on_conflict_do_nothing(index_elements=["tag_id", "preset_id"])
            .returning(AgentTagLink)
        )
        result = await self.session.execute(statement)
        link = result.scalar_one_or_none()
        await self.session.commit()
        if link is None:
            link = await self.get_preset_tag_link(preset_id, tag_id)
        return link

    @require_scope("agent:update")
    async def remove_preset_tag(self, link: AgentTagLink) -> None:
        await self.session.delete(link)
        await self.session.commit()
