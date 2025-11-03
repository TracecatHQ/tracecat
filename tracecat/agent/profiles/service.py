"""Service layer for managing agent profiles."""

from __future__ import annotations

import uuid

from slugify import slugify
from sqlmodel import select

from tracecat.agent.profiles.schemas import AgentProfileCreate, AgentProfileUpdate
from tracecat.agent.types import AgentConfig
from tracecat.db.models import AgentProfile as AgentProfileModel
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService


class AgentProfilesService(BaseWorkspaceService):
    """CRUD operations and helpers for agent profiles."""

    service_name = "agent_profiles"

    async def list_profiles(self) -> list[AgentProfileModel]:
        """Return all agent profiles for the current workspace ordered by recency."""

        stmt = (
            select(AgentProfileModel)
            .where(AgentProfileModel.owner_id == self.workspace_id)
            .order_by(AgentProfileModel.created_at.desc())
        )
        result = await self.session.exec(stmt)
        return result.all()

    async def get_profile(self, profile_id: uuid.UUID) -> AgentProfileModel:
        """Retrieve a single profile by its identifier."""

        return await self._get_profile_model(profile_id)

    async def get_profile_by_slug(self, slug: str) -> AgentProfileModel:
        """Retrieve a profile by its slug."""

        return await self._get_profile_model_by_slug(slug)

    async def create_profile(self, params: AgentProfileCreate) -> AgentProfileModel:
        """Create a new agent profile scoped to the current workspace."""

        slug = await self._normalize_and_validate_slug(
            proposed_slug=params.slug,
            fallback_name=params.name,
        )
        profile = AgentProfileModel(
            owner_id=self.workspace_id,
            name=params.name,
            slug=slug,
            description=params.description,
            instructions=params.instructions,
            model_name=params.model_name,
            model_provider=params.model_provider,
            base_url=params.base_url,
            output_type=params.output_type,
            actions=params.actions,
            namespaces=params.namespaces,
            fixed_arguments=params.fixed_arguments,
            tool_approvals=params.tool_approvals,
            mcp_server_url=params.mcp_server_url,
            mcp_server_headers=params.mcp_server_headers,
            model_settings=params.model_settings,
            retries=params.retries,
        )
        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def update_profile(
        self, profile_id: uuid.UUID, params: AgentProfileUpdate
    ) -> AgentProfileModel:
        """Update an existing profile."""

        profile = await self._get_profile_model(profile_id)
        payload = params.model_dump(exclude_unset=True)

        if "name" in payload:
            profile.name = payload.pop("name")
        if "slug" in payload:
            profile.slug = await self._normalize_and_validate_slug(
                proposed_slug=payload.pop("slug"),
                fallback_name=profile.name,
                exclude_id=profile_id,
            )

        for field in (
            "description",
            "instructions",
            "model_name",
            "model_provider",
            "base_url",
            "output_type",
            "actions",
            "namespaces",
            "fixed_arguments",
            "tool_approvals",
            "mcp_server_url",
            "mcp_server_headers",
            "model_settings",
            "retries",
        ):
            if field in payload:
                setattr(profile, field, payload[field])

        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def delete_profile(self, profile_id: uuid.UUID) -> None:
        """Delete a profile."""

        profile = await self._get_profile_model(profile_id)
        await self.session.delete(profile)
        await self.session.commit()

    async def get_agent_config(self, profile_id: uuid.UUID) -> AgentConfig:
        """Resolve the agent configuration for a profile by ID."""

        profile = await self._get_profile_model(profile_id)
        return self._profile_to_agent_config(profile)

    async def get_agent_config_by_slug(self, slug: str) -> AgentConfig:
        """Resolve the agent configuration for a profile by slug."""

        profile = await self._get_profile_model_by_slug(slug)
        return self._profile_to_agent_config(profile)

    async def _normalize_and_validate_slug(
        self,
        *,
        proposed_slug: str | None,
        fallback_name: str,
        exclude_id: uuid.UUID | None = None,
    ) -> str:
        base = proposed_slug or fallback_name
        slug = slugify(base, separator="-")
        if not slug:
            raise TracecatValidationError("Agent profile slug cannot be empty")

        stmt = select(AgentProfileModel).where(
            AgentProfileModel.owner_id == self.workspace_id,
            AgentProfileModel.slug == slug,
        )
        if exclude_id is not None:
            stmt = stmt.where(AgentProfileModel.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first() is not None:
            raise TracecatValidationError(
                f"Agent profile slug '{slug}' is already in use for this workspace",
            )
        return slug

    async def _get_profile_model(self, profile_id: uuid.UUID) -> AgentProfileModel:
        stmt = select(AgentProfileModel).where(
            AgentProfileModel.owner_id == self.workspace_id,
            AgentProfileModel.id == profile_id,
        )
        result = await self.session.exec(stmt)
        profile = result.first()
        if profile is None:
            raise TracecatNotFoundError(
                f"Agent profile {profile_id} not found in this workspace",
            )
        return profile

    async def _get_profile_model_by_slug(self, slug: str) -> AgentProfileModel:
        stmt = select(AgentProfileModel).where(
            AgentProfileModel.owner_id == self.workspace_id,
            AgentProfileModel.slug == slug,
        )
        result = await self.session.exec(stmt)
        profile = result.first()
        if profile is None:
            raise TracecatNotFoundError(
                f"Agent profile '{slug}' not found in this workspace",
            )
        return profile

    def _profile_to_agent_config(self, profile: AgentProfileModel) -> AgentConfig:
        return AgentConfig(
            model_name=profile.model_name,
            model_provider=profile.model_provider,
            base_url=profile.base_url,
            instructions=profile.instructions,
            output_type=profile.output_type,
            actions=profile.actions,
            namespaces=profile.namespaces,
            fixed_arguments=profile.fixed_arguments,
            tool_approvals=profile.tool_approvals,
            mcp_server_url=profile.mcp_server_url,
            mcp_server_headers=profile.mcp_server_headers,
            model_settings=profile.model_settings,
            retries=profile.retries,
        )
