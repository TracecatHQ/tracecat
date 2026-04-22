"""Service for agent tag definitions and preset-tag linking."""

import uuid
from collections.abc import Sequence

from slugify import slugify
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import NoResultFound

from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentPreset, AgentTag, AgentTagLink
from tracecat.identifiers import AgentTagID
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tags.schemas import TagCreate, TagUpdate
from tracecat.tiers.enums import Entitlement


class AgentTagsService(BaseWorkspaceService):
    """Handles both agent tag definitions and preset-tag linking."""

    service_name = "agent_tags"

    # --- Tag definitions ---

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags(self) -> Sequence[AgentTag]:
        """List all agent tags in the workspace."""
        statement = select(AgentTag).where(AgentTag.workspace_id == self.workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_tag(self, tag_id: AgentTagID) -> AgentTag:
        """Get an agent tag by ID."""
        statement = select(AgentTag).where(
            AgentTag.workspace_id == self.workspace_id,
            AgentTag.id == tag_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_tag_by_ref(self, ref: str) -> AgentTag:
        """Get an agent tag by its ref."""
        statement = select(AgentTag).where(
            AgentTag.workspace_id == self.workspace_id,
            AgentTag.ref == ref,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

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
            raise ValueError(f"Agent tag with slug '{ref}' already exists")

        db_tag = AgentTag(
            name=tag.name,
            ref=ref,
            workspace_id=self.workspace_id,
            color=tag.color,
        )
        self.session.add(db_tag)
        await self.session.commit()
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
                    raise ValueError(f"Agent tag with slug '{new_ref}' already exists")
                tag.ref = new_ref

        for key, value in params.model_dump(exclude_unset=True).items():
            setattr(tag, key, value)

        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    @require_scope("agent:delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_tag(self, tag: AgentTag) -> None:
        """Delete an agent tag definition."""
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
            raise NoResultFound("Agent preset or tag not found")

    @require_scope("agent:read")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_tags_for_preset(self, preset_id: uuid.UUID) -> Sequence[AgentTag]:
        """List all tags on a preset."""
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
        return result.scalar_one()

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
            link = await self.get_preset_tag(preset_id, tag_id)
        await self.session.commit()
        return link

    @require_scope("agent:update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def remove_preset_tag(self, link: AgentTagLink) -> None:
        """Remove a tag from an agent preset."""
        await self.session.delete(link)
        await self.session.commit()
