"""Service layer for managing agent presets."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.preset.schemas import AgentPresetCreate, AgentPresetUpdate
from tracecat.agent.preset.scopes import ensure_preset_scopes, preset_scope_name
from tracecat.agent.types import AgentConfig, MCPServerConfig, OutputType
from tracecat.audit.logger import audit_log
from tracecat.authz.controls import has_scope, require_scope
from tracecat.cases.prompts import CASE_COPILOT_BASE_INSTRUCTIONS
from tracecat.db.models import (
    AgentPreset,
    OAuthIntegration,
    RoleScope,
    Scope,
    Workspace,
)
from tracecat.db.models import (
    Role as DBRole,
)
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.encryption import decrypt_value
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat.workspaces.prompts import WORKSPACE_COPILOT_BASE_INSTRUCTIONS


@dataclass(frozen=True, slots=True)
class SystemPresetDefinition:
    name: str
    slug: str
    description: str
    instructions: str
    model_name: str = "gpt-4o-mini"
    model_provider: str = "openai"
    actions: list[str] | None = None


SYSTEM_PRESET_SLUG_WORKSPACE_COPILOT = "system-workspace-copilot"
SYSTEM_PRESET_SLUG_CASE_COPILOT = "system-case-copilot"
WORKSPACE_COPILOT_DEFAULT_TOOLS = [
    "core.table.list_tables",
    "core.table.get_table_metadata",
    "core.table.lookup",
    "core.table.search_rows",
    "core.cases.list_cases",
    "core.cases.get_case",
    "core.cases.search_cases",
]
CASE_COPILOT_DEFAULT_TOOLS = [
    "core.cases.get_case",
    "core.cases.list_cases",
    "core.cases.update_case",
    "core.cases.create_comment",
    "core.cases.list_comments",
]


SYSTEM_PRESET_DEFINITIONS: tuple[SystemPresetDefinition, ...] = (
    SystemPresetDefinition(
        name="General assistant",
        slug="system-general-assistant",
        description="General-purpose assistant for workspace operations.",
        instructions=(
            "You are a concise assistant for security and IT operations. "
            "Use available tools when needed and explain outcomes clearly."
        ),
    ),
    SystemPresetDefinition(
        name="Workspace copilot",
        slug=SYSTEM_PRESET_SLUG_WORKSPACE_COPILOT,
        description="Default workspace assistant with copilot prompt and tools.",
        instructions=WORKSPACE_COPILOT_BASE_INSTRUCTIONS,
        actions=WORKSPACE_COPILOT_DEFAULT_TOOLS,
    ),
    SystemPresetDefinition(
        name="Case copilot",
        slug=SYSTEM_PRESET_SLUG_CASE_COPILOT,
        description="Default case assistant with case copilot guidance and tools.",
        instructions=CASE_COPILOT_BASE_INSTRUCTIONS,
        actions=CASE_COPILOT_DEFAULT_TOOLS,
    ),
    SystemPresetDefinition(
        name="Incident triage",
        slug="system-incident-triage",
        description="Triage incidents and recommend next actions.",
        instructions=(
            "You are an incident triage assistant. Prioritize fast triage, "
            "risk assessment, and actionable remediation steps."
        ),
    ),
    SystemPresetDefinition(
        name="Automation advisor",
        slug="system-automation-advisor",
        description="Help design and improve workflow automation.",
        instructions=(
            "You are an automation advisor. Focus on practical playbook design, "
            "safe rollout strategy, and measurable operational impact."
        ),
    ),
)

type PresetScopeAction = Literal["read", "execute", "update", "delete"]


async def seed_system_presets_for_workspace(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> None:
    """Ensure default system presets exist for a workspace."""
    # Consider all existing slugs in the workspace so we don't attempt inserts
    # that would violate the workspace+slug uniqueness constraint.
    stmt = select(AgentPreset.slug).where(AgentPreset.workspace_id == workspace_id)
    existing = set((await session.execute(stmt)).scalars().all())

    inserts: list[AgentPreset] = []
    for definition in SYSTEM_PRESET_DEFINITIONS:
        if definition.slug in existing:
            continue
        inserts.append(
            AgentPreset(
                workspace_id=workspace_id,
                name=definition.name,
                slug=definition.slug,
                description=definition.description,
                instructions=definition.instructions,
                model_name=definition.model_name,
                model_provider=definition.model_provider,
                is_system=True,
                actions=definition.actions,
                namespaces=None,
                tool_approvals=None,
                mcp_integrations=None,
                retries=3,
                enable_internet_access=False,
            )
        )
    if inserts:
        session.add_all(inserts)
    await ensure_preset_scopes(
        session,
        [definition.slug for definition in SYSTEM_PRESET_DEFINITIONS],
    )


async def seed_system_presets_for_all_workspaces(session: AsyncSession) -> int:
    """Ensure default system presets exist for all workspaces."""
    stmt = select(Workspace.id)
    workspace_ids = list((await session.execute(stmt)).scalars().all())
    for workspace_id in workspace_ids:
        await seed_system_presets_for_workspace(
            session=session,
            workspace_id=workspace_id,
        )
    return len(workspace_ids)


class AgentPresetService(BaseWorkspaceService):
    """CRUD operations and helpers for agent presets."""

    service_name = "agent_preset"

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == self.workspace_id)
            .order_by(AgentPreset.created_at.desc())
        )
        result = await self.session.execute(stmt)
        presets = result.scalars().all()
        return [preset for preset in presets if self.can_access_preset(preset, "read")]

    def can_access_preset(
        self,
        preset: AgentPreset,
        action: PresetScopeAction,
    ) -> bool:
        """Check whether the current role can access a preset for a specific action."""
        if self.role.is_platform_superuser:
            return True
        if self.role.scopes is None:
            return False
        required_scope = preset_scope_name(preset.slug, action)
        return has_scope(self.role.scopes, required_scope)

    def require_preset_scope(
        self,
        preset: AgentPreset,
        action: PresetScopeAction,
    ) -> None:
        """Require a preset-specific scope."""
        required_scope = preset_scope_name(preset.slug, action)
        scopes = self.role.scopes or frozenset()
        if self.role.is_platform_superuser or has_scope(scopes, required_scope):
            return
        raise ScopeDeniedError(
            required_scopes=[required_scope],
            missing_scopes=[required_scope],
        )

    @require_scope("agent:create")
    @audit_log(resource_type="agent_preset", action="create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def create_preset(self, params: AgentPresetCreate) -> AgentPreset:
        """Create a new agent preset scoped to the current workspace."""

        slug = await self._normalize_and_validate_slug(
            proposed_slug=params.slug,
            fallback_name=params.name,
        )
        if params.actions:
            await self._validate_actions(params.actions)
        if params.mcp_integrations:
            await self._validate_mcp_integrations(params.mcp_integrations)
        assigned_role_id = await self._validate_assigned_role_id(
            params.assigned_role_id
        )
        await ensure_preset_scopes(self.session, [slug])
        preset = AgentPreset(
            workspace_id=self.workspace_id,
            slug=slug,
            name=params.name,
            description=params.description,
            instructions=params.instructions,
            model_name=params.model_name,
            model_provider=params.model_provider,
            base_url=params.base_url,
            output_type=params.output_type,
            actions=params.actions,
            namespaces=params.namespaces,
            tool_approvals=params.tool_approvals,
            mcp_integrations=params.mcp_integrations,
            enable_internet_access=params.enable_internet_access,
            retries=params.retries,
            assigned_role_id=assigned_role_id,
        )
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    async def _validate_actions(self, actions: list[str]) -> None:
        """Validate that all actions are in the registry index."""
        actions_set = set(actions)
        registry_service = RegistryActionsService(self.session, role=self.role)
        index_entries = await registry_service.list_actions_from_index(
            include_keys=actions_set
        )
        available_identifiers = {
            f"{entry.namespace}.{entry.name}" for entry, _ in index_entries
        }
        if missing_actions := actions_set - available_identifiers:
            raise TracecatValidationError(
                f"{len(missing_actions)} actions were not found in the registry: {sorted(missing_actions)}"
            )

    async def _validate_assigned_role_id(
        self,
        assigned_role_id: uuid.UUID | None,
    ) -> uuid.UUID | None:
        """Validate preset assigned-role selection."""
        if assigned_role_id is None:
            return None

        # Custom assigned roles are only available with RBAC addons.
        await check_entitlement(self.session, self.role, Entitlement.RBAC_ADDONS)

        stmt = select(DBRole.id).where(
            DBRole.id == assigned_role_id,
            DBRole.organization_id == self.organization_id,
        )
        role_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if role_id is None:
            raise TracecatValidationError(
                "Assigned role must belong to the current organization"
            )
        return assigned_role_id

    async def _resolve_assigned_role_scopes(
        self,
        assigned_role_id: uuid.UUID | None,
    ) -> list[str] | None:
        """Resolve granted scopes for a preset assigned role."""
        if assigned_role_id is None:
            return None
        stmt = (
            select(Scope.name)
            .join(RoleScope, RoleScope.scope_id == Scope.id)
            .where(RoleScope.role_id == assigned_role_id)
        )
        scope_names = sorted((await self.session.execute(stmt)).scalars().all())
        return scope_names

    @require_scope("agent:preset:*:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_preset(
        self, preset: AgentPreset, params: AgentPresetUpdate
    ) -> AgentPreset:
        """Update an existing preset."""
        self.require_preset_scope(preset, "update")
        set_fields = params.model_dump(exclude_unset=True)

        # Handle name first as it may be needed for slug fallback
        if "name" in set_fields:
            preset.name = set_fields.pop("name")

        # Handle slug with validation
        if "slug" in set_fields:
            proposed_slug = set_fields.pop("slug")
            if preset.is_system:
                normalized = slugify(proposed_slug or "", separator="-")
                if normalized != preset.slug:
                    raise TracecatAuthorizationError(
                        "Cannot modify slug for system presets"
                    )
            else:
                preset.slug = await self._normalize_and_validate_slug(
                    proposed_slug=proposed_slug,
                    fallback_name=preset.name,
                    exclude_id=preset.id,
                )
                await ensure_preset_scopes(self.session, [preset.slug])
        if "assigned_role_id" in set_fields:
            assigned_role_id = set_fields.pop("assigned_role_id")
            preset.assigned_role_id = await self._validate_assigned_role_id(
                assigned_role_id
            )

        # Validate actions if provided
        if "actions" in set_fields:
            # Select in RegistryAction actions that are in the list of actions
            if actions := set_fields.pop("actions"):
                await self._validate_actions(actions)
            # If we reach this point, all actions are valid or was empty
            preset.actions = actions

        if "mcp_integrations" in set_fields:
            if mcp_integrations := set_fields.pop("mcp_integrations"):
                await self._validate_mcp_integrations(mcp_integrations)
            preset.mcp_integrations = mcp_integrations

        # Update remaining fields
        for field, value in set_fields.items():
            setattr(preset, field, value)

        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    @require_scope("agent:preset:*:delete")
    @audit_log(resource_type="agent_preset", action="delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_preset(self, preset: AgentPreset) -> None:
        """Delete a preset."""
        self.require_preset_scope(preset, "delete")
        if preset.is_system:
            raise TracecatAuthorizationError("Cannot delete system presets")
        await self.session.delete(preset)
        await self.session.commit()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_agent_config_by_slug(self, slug: str) -> AgentConfig:
        """Get the agent configuration for a preset by slug."""
        if preset := await self.get_preset_by_slug(slug, required_action="execute"):
            return await self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with slug '{slug}' not found")

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_agent_config(self, preset_id: uuid.UUID) -> AgentConfig:
        """Get the agent configuration for a preset by ID."""
        if preset := await self.get_preset(preset_id, required_action="execute"):
            return await self._preset_to_agent_config(preset)
        raise TracecatNotFoundError(f"Agent preset with ID '{preset_id}' not found")

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
    ) -> AgentConfig:
        """Get an agent configuration from a preset by ID or slug with MCP integrations resolved."""
        if preset_id is None and slug is None:
            raise ValueError("Either preset_id or slug must be provided")

        if preset_id is not None:
            return await self.get_agent_config(preset_id)
        return await self.get_agent_config_by_slug(slug or "")

    async def _validate_mcp_integrations(self, mcp_integrations: list[str]) -> None:
        """Validate MCP integration IDs for the workspace."""
        if not mcp_integrations:
            return

        # Convert string IDs to UUIDs for validation
        mcp_integration_ids = set()
        for mcp_id in mcp_integrations:
            try:
                mcp_integration_ids.add(uuid.UUID(mcp_id))
            except ValueError as err:
                raise TracecatValidationError(
                    f"Invalid MCP integration ID format: {mcp_id}"
                ) from err

        integrations_service = IntegrationService(self.session, role=self.role)
        available_mcp_integrations = await integrations_service.list_mcp_integrations()
        available_mcp_integration_ids = {
            mcp_integration.id for mcp_integration in available_mcp_integrations
        }

        # Check if all requested IDs exist
        if missing_ids := mcp_integration_ids - available_mcp_integration_ids:
            missing_str = sorted(str(id) for id in missing_ids)
            raise TracecatValidationError(
                f"{len(missing_ids)} MCP integrations were not found in this workspace: {missing_str}"
            )

    async def _resolve_mcp_integrations(
        self, mcp_integrations: list[str] | None
    ) -> list[MCPServerConfig] | None:
        """Resolve MCP provider URLs and authorization headers from selected integrations."""
        if not mcp_integrations:
            return None

        integrations_service = IntegrationService(self.session, role=self.role)
        available_mcp_integrations = await integrations_service.list_mcp_integrations()
        by_id = {
            mcp_integration.id: mcp_integration
            for mcp_integration in available_mcp_integrations
        }

        # Get encryption key for decrypting custom credentials
        encryption_key = config.TRACECAT__DB_ENCRYPTION_KEY
        if not encryption_key:
            raise TracecatValidationError(
                "TRACECAT__DB_ENCRYPTION_KEY is not set, cannot resolve MCP integrations"
            )

        # Collect all matching integrations in preset order
        mcp_servers = []
        for mcp_id_str in mcp_integrations:
            try:
                mcp_integration_id = uuid.UUID(mcp_id_str)
            except ValueError:
                logger.warning(
                    "Invalid MCP integration ID format, skipping: %r",
                    mcp_id_str,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_id": mcp_id_str,
                    },
                )
                continue

            if mcp_integration_id not in by_id:
                logger.warning(
                    "MCP integration not found, skipping: %r",
                    mcp_integration_id,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration_id),
                    },
                )
                continue

            mcp_integration = by_id[mcp_integration_id]
            headers: dict[str, str] = {}

            # Resolve headers based on auth type
            if mcp_integration.auth_type == MCPAuthType.OAUTH2:
                # OAuth2: Get access token from linked OAuth integration
                if not mcp_integration.oauth_integration_id:
                    logger.warning(
                        "MCP integration %r has OAUTH2 auth type but no oauth_integration_id",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

                # Get OAuth integration by ID
                stmt = select(OAuthIntegration).where(
                    OAuthIntegration.id == mcp_integration.oauth_integration_id,
                    OAuthIntegration.workspace_id == self.workspace_id,
                )
                result = await self.session.execute(stmt)
                oauth_integration = result.scalars().first()
                if not oauth_integration:
                    logger.warning(
                        "OAuth integration not found for MCP integration %r",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                            "oauth_integration_id": str(
                                mcp_integration.oauth_integration_id
                            ),
                        },
                    )
                    continue

                await integrations_service.refresh_token_if_needed(oauth_integration)
                access_token = await integrations_service.get_access_token(
                    oauth_integration
                )
                if not access_token:
                    logger.warning(
                        "No access token for MCP integration %r (likely disconnected)",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                            "integration_status": oauth_integration.status,
                        },
                    )
                    continue

                token_type = oauth_integration.token_type or "Bearer"
                headers["Authorization"] = (
                    f"{token_type} {access_token.get_secret_value()}"
                )

            elif mcp_integration.auth_type == MCPAuthType.CUSTOM:
                # CUSTOM: Decrypt and parse custom credentials (JSON string with headers)
                if not mcp_integration.encrypted_headers:
                    logger.warning(
                        "MCP integration %r has CUSTOM auth type but no encrypted_headers",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

                try:
                    decrypted_bytes = decrypt_value(
                        mcp_integration.encrypted_headers, key=encryption_key
                    )
                    custom_credentials_str = decrypted_bytes.decode("utf-8")
                    custom_headers = json.loads(custom_credentials_str)

                    if not isinstance(custom_headers, dict):
                        logger.warning(
                            "Custom credentials for MCP integration %r must be a JSON object",
                            mcp_integration.name,
                            extra={
                                "workspace_id": str(self.workspace_id),
                                "mcp_integration_id": str(mcp_integration.id),
                            },
                        )
                        continue

                    # Merge custom headers (e.g., {"Authorization": "Bearer ...", "X-API-Key": "..."})
                    headers.update(custom_headers)

                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(
                        "Failed to parse custom credentials for MCP integration %r: %s",
                        mcp_integration.name,
                        str(e),
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

            elif mcp_integration.auth_type == MCPAuthType.NONE:
                pass

            else:
                logger.warning(
                    "Unknown auth type for MCP integration %r: %s",
                    mcp_integration.name,
                    mcp_integration.auth_type,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration.id),
                        "auth_type": str(mcp_integration.auth_type),
                    },
                )
                continue

            # Build MCP server config
            mcp_servers.append(
                {
                    "name": mcp_integration.name,
                    "url": mcp_integration.server_uri,
                    "headers": headers,
                }
            )

        if not mcp_servers:
            raise TracecatValidationError(
                "No matching MCP integrations found for this preset in the workspace"
            )

        return mcp_servers

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
            raise TracecatValidationError("Agent preset slug cannot be empty")

        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        if exclude_id is not None:
            stmt = stmt.where(AgentPreset.id != exclude_id)

        result = await self.session.execute(stmt)
        if result.scalars().first() is not None:
            raise TracecatValidationError(
                f"Agent preset slug '{slug}' is already in use for this workspace",
            )
        return slug

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset(
        self,
        preset_id: uuid.UUID,
        *,
        required_action: PresetScopeAction = "read",
    ) -> AgentPreset | None:
        """Get an agent preset by ID with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.execute(stmt)
        preset = result.scalars().first()
        if preset is not None:
            self.require_preset_scope(preset, required_action)
        return preset

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset_by_slug(
        self,
        slug: str,
        *,
        required_action: PresetScopeAction = "read",
    ) -> AgentPreset | None:
        """Get an agent preset by slug with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        result = await self.session.execute(stmt)
        preset = result.scalars().first()
        if preset is not None:
            self.require_preset_scope(preset, required_action)
        return preset

    async def _preset_to_agent_config(self, preset: AgentPreset) -> AgentConfig:
        mcp_servers = await self._resolve_mcp_integrations(preset.mcp_integrations)
        assigned_role_scopes = await self._resolve_assigned_role_scopes(
            preset.assigned_role_id
        )
        # Only disable parallel tool calls if tools will be present
        model_settings = {}
        if preset.actions or mcp_servers:
            model_settings["parallel_tool_calls"] = False
        return AgentConfig(
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            base_url=preset.base_url,
            instructions=preset.instructions,
            output_type=cast(OutputType | None, preset.output_type),
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_servers=mcp_servers,
            retries=preset.retries,
            model_settings=model_settings,
            enable_internet_access=preset.enable_internet_access,
            tool_execution_scopes=assigned_role_scopes,
        )
