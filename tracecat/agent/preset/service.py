"""Service layer for managing agent presets."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any, cast

import sqlalchemy as sa
from slugify import slugify
from sqlalchemy import select

from tracecat.agent.common.types import MCPHttpServerConfig
from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetUpdate,
    AgentPresetVersionDiff,
    ScalarFieldChange,
    StringListFieldChange,
    ToolApprovalFieldChange,
)
from tracecat.agent.types import (
    AgentConfig,
    MCPServerConfig,
    OutputType,
)
from tracecat.audit.logger import audit_log
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    OAuthIntegration,
)
from tracecat.dsl.common import create_default_execution_context
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.executor.service import get_workspace_variables
from tracecat.expressions.eval import collect_expressions, eval_templated_object
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_validation import (
    MCPValidationError,
    validate_mcp_command_config,
)
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets import secrets_manager
from tracecat.secrets.encryption import decrypt_value
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


class AgentPresetService(BaseWorkspaceService):
    """CRUD operations and helpers for agent presets."""

    service_name = "agent_preset"
    EXECUTION_FIELDS = {
        "instructions",
        "model_name",
        "model_provider",
        "base_url",
        "output_type",
        "actions",
        "namespaces",
        "tool_approvals",
        "mcp_integrations",
        "retries",
        "enable_internet_access",
    }

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_presets(self) -> Sequence[AgentPreset]:
        """Return all agent presets for the current workspace ordered by recency."""

        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == self.workspace_id)
            .order_by(AgentPreset.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

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
        )
        self.session.add(preset)
        await self.session.flush()
        version = AgentPresetVersion(
            workspace_id=self.workspace_id,
            preset_id=preset.id,
            version=1,
            instructions=preset.instructions,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            base_url=preset.base_url,
            output_type=preset.output_type,
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_integrations=preset.mcp_integrations,
            retries=preset.retries,
            enable_internet_access=preset.enable_internet_access,
        )
        self.session.add(version)
        await self.session.flush()
        preset.current_version_id = version.id
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

    @require_scope("agent:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def update_preset(
        self, preset: AgentPreset, params: AgentPresetUpdate
    ) -> AgentPreset:
        """Update an existing preset."""
        set_fields = params.model_dump(exclude_unset=True)
        execution_changed = False

        # Handle name first as it may be needed for slug fallback
        if "name" in set_fields:
            preset.name = set_fields.pop("name")

        # Handle slug with validation
        if "slug" in set_fields:
            preset.slug = await self._normalize_and_validate_slug(
                proposed_slug=set_fields.pop("slug"),
                fallback_name=preset.name,
                exclude_id=preset.id,
            )

        # Validate actions if provided
        if "actions" in set_fields:
            # Select in RegistryAction actions that are in the list of actions
            if actions := set_fields.pop("actions"):
                await self._validate_actions(actions)
            # If we reach this point, all actions are valid or was empty
            if preset.actions != actions:
                preset.actions = actions
                execution_changed = True

        if "mcp_integrations" in set_fields:
            if mcp_integrations := set_fields.pop("mcp_integrations"):
                await self._validate_mcp_integrations(mcp_integrations)
            if preset.mcp_integrations != mcp_integrations:
                preset.mcp_integrations = mcp_integrations
                execution_changed = True

        # Update remaining fields
        for field, value in set_fields.items():
            if getattr(preset, field) != value:
                if field in self.EXECUTION_FIELDS:
                    execution_changed = True
                setattr(preset, field, value)

        self.session.add(preset)
        if execution_changed:
            version = await self._create_version_from_preset(preset)
            preset.current_version_id = version.id
            self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    @require_scope("agent:delete")
    @audit_log(resource_type="agent_preset", action="delete")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def delete_preset(self, preset: AgentPreset) -> None:
        """Delete a preset."""
        # Break the mutable-head pointer before deleting version rows to avoid an ORM
        # dependency cycle between AgentPreset.current_version_id and its versions.
        preset.current_version_id = None
        self.session.add(preset)
        await self.session.flush()
        await self.session.delete(preset)
        await self.session.commit()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_agent_config_by_slug(
        self, slug: str, *, preset_version: int | None = None
    ) -> AgentConfig:
        """Get the agent configuration for a preset by slug."""
        version = await self.resolve_agent_preset_version(
            slug=slug,
            preset_version=preset_version,
        )
        return await self._version_to_agent_config(version)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_agent_config(
        self,
        preset_id: uuid.UUID,
        *,
        preset_version_id: uuid.UUID | None = None,
    ) -> AgentConfig:
        """Get the agent configuration for a preset by ID."""
        version = await self.resolve_agent_preset_version(
            preset_id=preset_id,
            preset_version_id=preset_version_id,
        )
        return await self._version_to_agent_config(version)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def resolve_agent_preset_config(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> AgentConfig:
        """Get an agent configuration from a preset by ID or slug with MCP integrations resolved."""
        version = await self.resolve_agent_preset_version(
            preset_id=preset_id,
            slug=slug,
            preset_version_id=preset_version_id,
            preset_version=preset_version,
        )
        return await self._version_to_agent_config(version)

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def resolve_agent_preset_version(
        self,
        *,
        preset_id: uuid.UUID | None = None,
        slug: str | None = None,
        preset_version_id: uuid.UUID | None = None,
        preset_version: int | None = None,
    ) -> AgentPresetVersion:
        """Resolve a preset version from logical preset identity and optional pin."""
        if preset_id is None and slug is None and preset_version_id is None:
            raise ValueError(
                "Either preset_id, slug, or preset_version_id must be provided"
            )
        if preset_version is not None and slug is None and preset_id is None:
            raise ValueError("'preset_version' requires a preset_id or slug")

        preset: AgentPreset | None = None
        if preset_id is not None:
            preset = await self.get_preset(preset_id)
        elif slug is not None:
            preset = await self.get_preset_by_slug(slug)

        if preset is None and preset_version_id is None:
            detail = slug if slug is not None else str(preset_id)
            raise TracecatNotFoundError(f"Agent preset '{detail}' not found")

        if preset_version_id is not None:
            version = await self.get_version(preset_version_id)
            if version is None:
                raise TracecatNotFoundError(
                    f"Agent preset version with ID '{preset_version_id}' not found"
                )
            if preset is not None and version.preset_id != preset.id:
                raise TracecatValidationError(
                    "Preset version does not belong to the selected preset"
                )
            return version

        if preset is None:
            raise TracecatNotFoundError("Agent preset not found")

        if preset_version is not None:
            version = await self.get_version_by_number(
                preset_id=preset.id,
                version=preset_version,
            )
            if version is None:
                raise TracecatNotFoundError(
                    f"Agent preset version {preset_version} not found"
                )
            return version

        return await self.get_current_version_for_preset(preset)

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

    def _decrypt_mcp_headers(
        self,
        *,
        encrypted_headers: bytes,
        encryption_key: str,
        mcp_integration_id: uuid.UUID,
        mcp_integration_name: str,
    ) -> dict[str, str] | None:
        """Decrypt and validate MCP custom headers from encrypted storage."""
        try:
            decrypted_bytes = decrypt_value(encrypted_headers, key=encryption_key)
            custom_credentials_str = decrypted_bytes.decode("utf-8")
            parsed_headers = json.loads(custom_credentials_str)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
            logger.warning(
                "Failed to parse custom credentials for MCP integration %r: %s",
                mcp_integration_name,
                str(err),
                extra={
                    "workspace_id": str(self.workspace_id),
                    "mcp_integration_id": str(mcp_integration_id),
                },
            )
            return None

        if not isinstance(parsed_headers, dict):
            logger.warning(
                "Custom credentials for MCP integration %r must be a JSON object",
                mcp_integration_name,
                extra={
                    "workspace_id": str(self.workspace_id),
                    "mcp_integration_id": str(mcp_integration_id),
                },
            )
            return None

        custom_headers: dict[str, str] = {}
        for key, value in parsed_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                logger.warning(
                    "Custom credentials for MCP integration %r must contain string header values",
                    mcp_integration_name,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration_id),
                    },
                )
                return None
            custom_headers[key] = value

        return custom_headers

    async def _resolve_mcp_integrations(
        self, mcp_integrations: list[str] | None
    ) -> list[MCPServerConfig] | None:
        """Resolve MCP integrations into MCP server configs."""
        if not mcp_integrations:
            return None

        integrations_service = IntegrationService(self.session, role=self.role)
        available_mcp_integrations = await integrations_service.list_mcp_integrations()
        by_id = {
            mcp_integration.id: mcp_integration
            for mcp_integration in available_mcp_integrations
        }

        # Get encryption key for decrypting custom credentials
        encryption_key = get_db_encryption_key()

        mcp_servers: list[MCPServerConfig] = []

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

            # Handle stdio-type servers
            if mcp_integration.server_type == "stdio":
                if not mcp_integration.stdio_command:
                    logger.warning(
                        "Stdio-type MCP integration %r has no stdio_command specified",
                        mcp_integration.name,
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

                # Decrypt stdio_env if present
                stdio_env = integrations_service.decrypt_stdio_env(mcp_integration)
                if stdio_env:
                    try:
                        stdio_env = await self._resolve_stdio_env(
                            stdio_env=stdio_env,
                            mcp_integration_id=mcp_integration.id,
                            mcp_integration_slug=mcp_integration.slug,
                        )
                    except Exception as e:
                        logger.warning(
                            "Stdio env resolution failed for MCP integration %r: %s",
                            mcp_integration.name,
                            str(e),
                            extra={
                                "workspace_id": str(self.workspace_id),
                                "mcp_integration_id": str(mcp_integration.id),
                                "mcp_integration_slug": mcp_integration.slug,
                                "env_keys": sorted(stdio_env.keys()),
                            },
                        )
                        continue

                # Re-validate command config at resolution time
                try:
                    validate_mcp_command_config(
                        command=mcp_integration.stdio_command,
                        args=mcp_integration.stdio_args,
                        env=stdio_env,
                        name=mcp_integration.slug,
                    )
                except MCPValidationError as e:
                    logger.warning(
                        "Stdio-type MCP integration %r failed validation: %s",
                        mcp_integration.name,
                        str(e),
                        extra={
                            "workspace_id": str(self.workspace_id),
                            "mcp_integration_id": str(mcp_integration.id),
                        },
                    )
                    continue

                command_config: MCPServerConfig = {
                    "type": "stdio",
                    "name": mcp_integration.slug,
                    "command": mcp_integration.stdio_command,
                }
                if mcp_integration.stdio_args:
                    command_config["args"] = mcp_integration.stdio_args
                if stdio_env:
                    command_config["env"] = stdio_env
                if mcp_integration.timeout:
                    command_config["timeout"] = mcp_integration.timeout

                mcp_servers.append(command_config)
                continue

            # Handle HTTP-type servers (default)
            if not mcp_integration.server_uri:
                logger.warning(
                    "HTTP-type MCP integration %r has no server_uri specified",
                    mcp_integration.name,
                    extra={
                        "workspace_id": str(self.workspace_id),
                        "mcp_integration_id": str(mcp_integration.id),
                    },
                )
                continue

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

                if mcp_integration.encrypted_headers:
                    custom_headers = self._decrypt_mcp_headers(
                        encrypted_headers=mcp_integration.encrypted_headers,
                        encryption_key=encryption_key,
                        mcp_integration_id=mcp_integration.id,
                        mcp_integration_name=mcp_integration.name,
                    )
                    if custom_headers is None:
                        # OAuth2 additional headers are optional; malformed values should
                        # not disable the integration when an access token is available.
                        custom_headers = {}

                    auth_header_keys = [
                        key
                        for key in custom_headers
                        if key.strip().casefold() == "authorization"
                    ]
                    if auth_header_keys:
                        logger.warning(
                            "Ignoring custom Authorization header variants for OAUTH2 MCP integration %r",
                            mcp_integration.name,
                            extra={
                                "workspace_id": str(self.workspace_id),
                                "mcp_integration_id": str(mcp_integration.id),
                                "dropped_header_keys": auth_header_keys,
                            },
                        )
                        for auth_header_key in auth_header_keys:
                            custom_headers.pop(auth_header_key, None)
                    headers.update(custom_headers)

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

                custom_headers = self._decrypt_mcp_headers(
                    encrypted_headers=mcp_integration.encrypted_headers,
                    encryption_key=encryption_key,
                    mcp_integration_id=mcp_integration.id,
                    mcp_integration_name=mcp_integration.name,
                )
                if custom_headers is None:
                    continue
                # Merge custom headers (e.g., {"Authorization": "Bearer ...", "X-API-Key": "..."})
                headers.update(custom_headers)

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
            http_config: MCPHttpServerConfig = {
                "type": "http",
                "name": mcp_integration.name,
                "url": mcp_integration.server_uri,
                "headers": headers,
            }
            if mcp_integration.timeout is not None:
                http_config["timeout"] = mcp_integration.timeout
            mcp_servers.append(http_config)

        if not mcp_servers:
            raise TracecatValidationError(
                "No matching MCP integrations found for this preset in the workspace"
            )

        return mcp_servers

    async def _resolve_stdio_env(
        self,
        *,
        stdio_env: dict[str, str],
        mcp_integration_id: uuid.UUID,
        mcp_integration_slug: str,
    ) -> dict[str, str]:
        """Resolve template expressions in stdio_env using workspace secrets/vars."""
        collected = collect_expressions(stdio_env)
        if not collected.secrets and not collected.variables:
            return stdio_env

        secrets = await secrets_manager.get_action_secrets(
            secret_exprs=collected.secrets,
            action_secrets=set(),
        )
        vars_map = await get_workspace_variables(
            variable_exprs=collected.variables,
            role=self.role,
        )

        context = create_default_execution_context()
        context["SECRETS"] = secrets
        context["VARS"] = vars_map

        resolved = eval_templated_object(stdio_env, operand=context)
        if not isinstance(resolved, dict):
            raise TracecatValidationError(
                "Resolved stdio_env must be a JSON object with string values"
            )

        non_string_keys = [
            key for key, value in resolved.items() if not isinstance(value, str)
        ]
        if non_string_keys:
            raise TracecatValidationError(
                "Resolved stdio_env values must be strings "
                f"(invalid keys: {sorted(non_string_keys)})"
            )

        logger.info(
            "Resolved stdio_env template expressions",
            workspace_id=str(self.workspace_id),
            mcp_integration_id=str(mcp_integration_id),
            mcp_integration_slug=mcp_integration_slug,
            env_key_count=len(resolved),
            secret_ref_count=len(collected.secrets),
            var_ref_count=len(collected.variables),
        )

        return cast(dict[str, str], resolved)

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
    async def get_preset(self, preset_id: uuid.UUID) -> AgentPreset | None:
        """Get an agent preset by ID with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.id == preset_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_preset_by_slug(self, slug: str) -> AgentPreset | None:
        """Get an agent preset by slug with proper error handling."""
        stmt = select(AgentPreset).where(
            AgentPreset.workspace_id == self.workspace_id,
            AgentPreset.slug == slug,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def list_versions(
        self,
        preset_id: uuid.UUID,
        params: CursorPaginationParams,
    ) -> CursorPaginatedResponse[AgentPresetVersion]:
        """List immutable versions for a preset ordered newest first."""
        paginator = BaseCursorPaginator(self.session)
        stmt = select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == self.workspace_id,
            AgentPresetVersion.preset_id == preset_id,
        )
        if params.cursor:
            try:
                cursor_data = paginator.decode_cursor(params.cursor)
                cursor_id = uuid.UUID(cursor_data.id)
            except ValueError as err:
                raise TracecatValidationError(
                    "Invalid cursor for agent preset versions"
                ) from err
            cursor_version = cursor_data.sort_value
            if not isinstance(cursor_version, int):
                raise TracecatValidationError(
                    "Invalid cursor for agent preset versions"
                )
            cursor_predicate = sa.or_(
                AgentPresetVersion.version > cursor_version,
                sa.and_(
                    AgentPresetVersion.version == cursor_version,
                    AgentPresetVersion.id > cursor_id,
                ),
            )
            if not params.reverse:
                cursor_predicate = sa.or_(
                    AgentPresetVersion.version < cursor_version,
                    sa.and_(
                        AgentPresetVersion.version == cursor_version,
                        AgentPresetVersion.id < cursor_id,
                    ),
                )
            stmt = stmt.where(cursor_predicate)

        if params.reverse:
            stmt = stmt.order_by(
                AgentPresetVersion.version.asc(),
                AgentPresetVersion.id.asc(),
            )
        else:
            stmt = stmt.order_by(
                AgentPresetVersion.version.desc(),
                AgentPresetVersion.id.desc(),
            )
        stmt = stmt.limit(params.limit + 1)
        result = await self.session.execute(stmt)
        versions = result.scalars().all()
        has_more = len(versions) > params.limit
        items = versions[: params.limit]

        next_cursor = None
        if has_more and items:
            last_version = items[-1]
            next_cursor = paginator.encode_cursor(
                last_version.id,
                sort_column="version",
                sort_value=last_version.version,
            )

        prev_cursor = None
        if params.cursor and items:
            first_version = items[0]
            prev_cursor = paginator.encode_cursor(
                first_version.id,
                sort_column="version",
                sort_value=first_version.version,
            )

        return CursorPaginatedResponse(
            items=list(items),
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=params.cursor is not None,
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version(self, version_id: uuid.UUID) -> AgentPresetVersion | None:
        """Get a preset version by ID."""
        stmt = select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == self.workspace_id,
            AgentPresetVersion.id == version_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def get_version_by_number(
        self, *, preset_id: uuid.UUID, version: int
    ) -> AgentPresetVersion | None:
        """Get a preset version by logical preset and version number."""
        stmt = select(AgentPresetVersion).where(
            AgentPresetVersion.workspace_id == self.workspace_id,
            AgentPresetVersion.preset_id == preset_id,
            AgentPresetVersion.version == version,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def _lock_preset_for_versioning(self, preset_id: uuid.UUID) -> None:
        """Serialize version creation for one preset using a row-level lock."""
        stmt = (
            select(AgentPreset.id)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id == preset_id,
            )
            .with_for_update()
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            raise TracecatNotFoundError(f"Agent preset '{preset_id}' not found")

    async def get_current_version_for_preset(
        self, preset: AgentPreset
    ) -> AgentPresetVersion:
        """Return the current version for a preset."""
        if (
            preset.current_version_id is not None
            and (version := await self.get_version(preset.current_version_id))
            is not None
        ):
            return version

        stmt = (
            select(AgentPresetVersion)
            .where(
                AgentPresetVersion.workspace_id == self.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
            )
            .order_by(
                AgentPresetVersion.version.desc(),
                AgentPresetVersion.created_at.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        if version := result.scalars().first():
            return version
        raise TracecatNotFoundError(
            f"Agent preset version for preset '{preset.id}' not found"
        )

    @require_scope("agent:update")
    @audit_log(resource_type="agent_preset", action="update")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def restore_version(
        self, preset: AgentPreset, version: AgentPresetVersion
    ) -> AgentPreset:
        """Restore a historical version as the current preset head."""
        if version.preset_id != preset.id:
            raise TracecatValidationError(
                "Preset version does not belong to the selected preset"
            )

        self._sync_preset_head_from_version(preset, version)
        preset.current_version_id = version.id
        self.session.add(preset)
        await self.session.commit()
        await self.session.refresh(preset)
        return preset

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def compare_versions(
        self,
        base_version: AgentPresetVersion,
        compare_version: AgentPresetVersion,
    ) -> AgentPresetVersionDiff:
        """Return a structured diff between two preset versions."""
        if base_version.preset_id != compare_version.preset_id:
            raise TracecatValidationError("Can only compare versions for one preset")

        scalar_changes: list[ScalarFieldChange] = []
        for field in (
            "model_name",
            "model_provider",
            "base_url",
            "output_type",
            "retries",
            "enable_internet_access",
        ):
            old_value = getattr(base_version, field)
            new_value = getattr(compare_version, field)
            if old_value != new_value:
                scalar_changes.append(
                    ScalarFieldChange(
                        field=field,
                        old_value=old_value,
                        new_value=new_value,
                    )
                )

        list_changes: list[StringListFieldChange] = []
        for field in ("actions", "namespaces", "mcp_integrations"):
            base_values = set(getattr(base_version, field) or [])
            compare_values = set(getattr(compare_version, field) or [])
            added = sorted(compare_values - base_values)
            removed = sorted(base_values - compare_values)
            if added or removed:
                list_changes.append(
                    StringListFieldChange(
                        field=field,
                        added=added,
                        removed=removed,
                    )
                )

        tool_approval_changes: list[ToolApprovalFieldChange] = []
        base_approvals = base_version.tool_approvals or {}
        compare_approvals = compare_version.tool_approvals or {}
        for tool in sorted(set(base_approvals) | set(compare_approvals)):
            old_value = base_approvals.get(tool)
            new_value = compare_approvals.get(tool)
            if old_value != new_value:
                tool_approval_changes.append(
                    ToolApprovalFieldChange(
                        tool=tool,
                        old_value=old_value,
                        new_value=new_value,
                    )
                )

        instructions_changed = base_version.instructions != compare_version.instructions
        total_changes = (
            int(instructions_changed)
            + len(scalar_changes)
            + len(list_changes)
            + len(tool_approval_changes)
        )

        return AgentPresetVersionDiff(
            base_version_id=base_version.id,
            base_version=base_version.version,
            compare_version_id=compare_version.id,
            compare_version=compare_version.version,
            instructions_changed=instructions_changed,
            base_instructions=base_version.instructions,
            compare_instructions=compare_version.instructions,
            scalar_changes=scalar_changes,
            list_changes=list_changes,
            tool_approval_changes=tool_approval_changes,
            total_changes=total_changes,
        )

    async def _version_to_agent_config(
        self, version: AgentPresetVersion
    ) -> AgentConfig:
        mcp_servers = await self._resolve_mcp_integrations(version.mcp_integrations)
        model_settings: dict[str, Any] = {"reasoning_effort": "medium"}
        if version.actions or mcp_servers:
            model_settings["parallel_tool_calls"] = False
        return AgentConfig(
            model_name=version.model_name,
            model_provider=version.model_provider,
            base_url=version.base_url,
            instructions=version.instructions,
            output_type=cast(OutputType | None, version.output_type),
            actions=version.actions,
            namespaces=version.namespaces,
            tool_approvals=version.tool_approvals,
            mcp_servers=mcp_servers,
            retries=version.retries,
            model_settings=model_settings,
            enable_internet_access=version.enable_internet_access,
        )

    async def _create_version_from_preset(
        self, preset: AgentPreset
    ) -> AgentPresetVersion:
        """Create and flush a new immutable version from the preset head."""
        await self._lock_preset_for_versioning(preset.id)
        stmt = (
            select(AgentPresetVersion.version)
            .where(
                AgentPresetVersion.workspace_id == self.workspace_id,
                AgentPresetVersion.preset_id == preset.id,
            )
            .order_by(AgentPresetVersion.version.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        current_version = result.scalar_one_or_none()
        next_version = (current_version or 0) + 1

        version = AgentPresetVersion(
            workspace_id=self.workspace_id,
            preset_id=preset.id,
            version=next_version,
            instructions=preset.instructions,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            base_url=preset.base_url,
            output_type=preset.output_type,
            actions=preset.actions,
            namespaces=preset.namespaces,
            tool_approvals=preset.tool_approvals,
            mcp_integrations=preset.mcp_integrations,
            retries=preset.retries,
            enable_internet_access=preset.enable_internet_access,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    def _sync_preset_head_from_version(
        self,
        preset: AgentPreset,
        version: AgentPresetVersion,
    ) -> None:
        """Copy versioned execution fields onto the mutable preset head."""
        preset.instructions = version.instructions
        preset.model_name = version.model_name
        preset.model_provider = version.model_provider
        preset.base_url = version.base_url
        preset.output_type = version.output_type
        preset.actions = version.actions
        preset.namespaces = version.namespaces
        preset.tool_approvals = version.tool_approvals
        preset.mcp_integrations = version.mcp_integrations
        preset.retries = version.retries
        preset.enable_internet_access = version.enable_internet_access
