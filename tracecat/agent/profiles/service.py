"""Service layer for managing agent profiles."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import cast

from slugify import slugify
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlmodel import desc, select

from tracecat.agent.profiles.schemas import AgentProfileCreate, AgentProfileUpdate
from tracecat.agent.types import AgentConfig, OutputType
from tracecat.db.models import AgentProfile
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.service import BaseWorkspaceService


class AgentProfilesService(BaseWorkspaceService):
    """CRUD operations and helpers for agent profiles."""

    service_name = "agent_profiles"

    async def list_profiles(self) -> Sequence[AgentProfile]:
        """Return all agent profiles for the current workspace ordered by recency."""

        stmt = (
            select(AgentProfile)
            .where(AgentProfile.owner_id == self.workspace_id)
            .order_by(desc(AgentProfile.created_at))
        )
        result = await self.session.exec(stmt)
        return result.all()

    async def get_profile(self, profile_id: uuid.UUID) -> AgentProfile:
        """Retrieve a single profile by its identifier."""

        return await self._get_profile_model(profile_id)

    async def get_profile_by_slug(self, slug: str) -> AgentProfile:
        """Retrieve a profile by its slug."""

        return await self._get_profile_model_by_slug(slug)

    async def create_profile(self, params: AgentProfileCreate) -> AgentProfile:
        """Create a new agent profile scoped to the current workspace."""

        slug = await self._normalize_and_validate_slug(
            proposed_slug=params.slug,
            fallback_name=params.name,
        )
        profile = AgentProfile(
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
    ) -> AgentProfile:
        """Update an existing profile."""
        profile = await self._get_profile_model(profile_id)
        set_fields = params.model_dump(exclude_unset=True)

        # Handle name first as it may be needed for slug fallback
        if "name" in set_fields:
            profile.name = set_fields.pop("name")

        # Handle slug with validation
        if "slug" in set_fields:
            profile.slug = await self._normalize_and_validate_slug(
                proposed_slug=set_fields.pop("slug"),
                fallback_name=profile.name,
                exclude_id=profile_id,
            )

        # Update remaining fields
        for field, value in set_fields.items():
            setattr(profile, field, value)

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

        stmt = select(AgentProfile).where(
            AgentProfile.owner_id == self.workspace_id,
            AgentProfile.slug == slug,
        )
        if exclude_id is not None:
            stmt = stmt.where(AgentProfile.id != exclude_id)

        result = await self.session.exec(stmt)
        if result.first() is not None:
            raise TracecatValidationError(
                f"Agent profile slug '{slug}' is already in use for this workspace",
            )
        return slug

    async def _get_profile_model(self, profile_id: uuid.UUID) -> AgentProfile:
        """Get an agent profile by ID with proper error handling."""
        stmt = select(AgentProfile).where(
            AgentProfile.owner_id == self.workspace_id,
            AgentProfile.id == profile_id,
        )
        result = await self.session.exec(stmt)
        try:
            return result.one()
        except MultipleResultsFound as e:
            self.logger.error(
                "Multiple agent profiles found",
                profile_id=profile_id,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                "Multiple agent profiles found when searching by ID"
            ) from e
        except NoResultFound as e:
            self.logger.error(
                "Agent profile not found",
                profile_id=profile_id,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                f"Agent profile {profile_id} not found in this workspace"
            ) from e

    async def _get_profile_model_by_slug(self, slug: str) -> AgentProfile:
        """Get an agent profile by slug with proper error handling."""
        stmt = select(AgentProfile).where(
            AgentProfile.owner_id == self.workspace_id,
            AgentProfile.slug == slug,
        )
        result = await self.session.exec(stmt)
        try:
            return result.one()
        except MultipleResultsFound as e:
            self.logger.error(
                "Multiple agent profiles found",
                slug=slug,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                f"Multiple agent profiles found with slug '{slug}'"
            ) from e
        except NoResultFound as e:
            self.logger.error(
                "Agent profile not found",
                slug=slug,
                owner_id=self.workspace_id,
            )
            raise TracecatNotFoundError(
                f"Agent profile '{slug}' not found in this workspace"
            ) from e

    def _profile_to_agent_config(self, profile: AgentProfile) -> AgentConfig:
        return AgentConfig(
            model_name=profile.model_name,
            model_provider=profile.model_provider,
            base_url=profile.base_url,
            instructions=profile.instructions,
            output_type=cast(OutputType | None, profile.output_type),
            actions=profile.actions,
            namespaces=profile.namespaces,
            fixed_arguments=profile.fixed_arguments,
            tool_approvals=profile.tool_approvals,
            mcp_server_url=profile.mcp_server_url,
            mcp_server_headers=profile.mcp_server_headers,
            model_settings=profile.model_settings,
            retries=profile.retries,
        )
