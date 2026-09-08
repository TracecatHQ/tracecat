"""Load and validate tool dependencies of immutable skill versions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.skill.types import (
    ResolvedSkillRef,
    SkillToolMetadata,
)
from tracecat.agent.skill.validation import get_mcp_grant_support_error
from tracecat.db.models import SkillVersion
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


class SkillToolDependencyService(BaseWorkspaceService):
    """Load and authorize projections for already-resolved skill versions."""

    service_name = "skill_tool_dependency"

    async def load_metadata(
        self,
        skill_version_ids: Sequence[uuid.UUID],
        *,
        mcp_integration_ids: Sequence[str] = (),
    ) -> SkillToolMetadata:
        """Load a shared metadata view without credentials or remote discovery."""
        stmt = (
            select(SkillVersion)
            .where(
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.id.in_(skill_version_ids),
            )
            .options(
                selectinload(SkillVersion.tools),
                selectinload(SkillVersion.mcp_tools),
            )
        )
        versions_by_id = (
            {
                version.id: version
                for version in (await self.session.execute(stmt)).scalars().all()
            }
            if skill_version_ids
            else {}
        )
        needs_mcp = bool(mcp_integration_ids) or any(
            version.mcp_tools for version in versions_by_id.values()
        )
        integrations = (
            await IntegrationService(
                self.session, role=self.role
            ).list_mcp_integrations()
            if needs_mcp
            else []
        )
        return SkillToolMetadata(
            versions=versions_by_id,
            integrations={integration.id: integration for integration in integrations},
        )

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def validate_dependencies(
        self,
        *,
        resolved_skills: Sequence[ResolvedSkillRef],
        metadata: SkillToolMetadata,
        preset_version_id: uuid.UUID | None = None,
    ) -> None:
        """Validate original declarations before policy combines their grants."""
        if not resolved_skills:
            return
        preset_context = (
            str(preset_version_id) if preset_version_id is not None else None
        )
        version_ids = [skill.skill_version_id for skill in resolved_skills]
        versions_by_id = metadata.versions
        missing_version_ids = sorted(
            str(version_id) for version_id in set(version_ids) - versions_by_id.keys()
        )
        if missing_version_ids:
            raise TracecatValidationError(
                "Resolved skills require unavailable versions",
                detail={
                    "code": "skill_versions_unavailable",
                    "skill_version_ids": missing_version_ids,
                    "preset_version_id": preset_context,
                },
            )

        versions = [versions_by_id[version_id] for version_id in version_ids]
        registry_tool_ids = tuple(
            sorted({tool.tool_id for version in versions for tool in version.tools})
        )
        if registry_tool_ids:
            registry_service = RegistryActionsService(self.session, role=self.role)
            available_entries = await registry_service.list_actions_from_index(
                include_keys=set(registry_tool_ids)
            )
            available_tool_ids = {
                f"{entry.namespace}.{entry.name}" for entry, _ in available_entries
            }
            if missing := set(registry_tool_ids) - available_tool_ids:
                raise TracecatValidationError(
                    "Attached skills require unavailable registry tools",
                    detail={
                        "code": "skill_registry_tools_unavailable",
                        "tool_ids": sorted(missing),
                        "preset_version_id": preset_context,
                    },
                )

        mcp_rows = [tool for version in versions for tool in version.mcp_tools]
        null_integration_tool_ids = sorted(
            tool.tool_id for tool in mcp_rows if tool.mcp_integration_id is None
        )
        if null_integration_tool_ids:
            raise TracecatValidationError(
                "Attached skills require deleted MCP integrations",
                detail={
                    "code": "skill_mcp_integrations_unavailable",
                    "tool_ids": null_integration_tool_ids,
                    "preset_version_id": preset_context,
                },
            )

        requested_integration_ids = {
            tool.mcp_integration_id
            for tool in mcp_rows
            if tool.mcp_integration_id is not None
        }
        integrations_by_id = metadata.integrations
        if missing_integrations := (
            requested_integration_ids - integrations_by_id.keys()
        ):
            raise TracecatValidationError(
                "Attached skills require unavailable MCP integrations",
                detail={
                    "code": "skill_mcp_integrations_unavailable",
                    "mcp_integration_ids": sorted(
                        str(integration_id) for integration_id in missing_integrations
                    ),
                    "preset_version_id": preset_context,
                },
            )

        unavailable_tool_ids: list[str] = []
        for row in mcp_rows:
            integration_id = row.mcp_integration_id
            if integration_id is None:
                continue
            if row.tool_name is None:
                continue
            # Every explicit dependency must remain available, even when another
            # declaration grants the whole integration. Validate before unioning
            # so row or skill ordering cannot change whether resolution succeeds.
            integration = integrations_by_id[integration_id]
            if support_error := get_mcp_grant_support_error(
                server_type=integration.server_type,
                tool_name=row.tool_name,
                tool_id=row.tool_id,
            ):
                raise TracecatValidationError(
                    support_error.message,
                    detail={
                        "code": support_error.code,
                        "tool_ids": [row.tool_id],
                        "preset_version_id": preset_context,
                    },
                )
            policies = MCPToolSummary.validate_stored(
                integration.tools,
                mcp_integration_id=integration.id,
            )
            policy = next(
                (tool for tool in policies or () if tool.name == row.tool_name),
                None,
            )
            if policy is None or not policy.enabled or policy.status != "available":
                unavailable_tool_ids.append(row.tool_id)
                continue

        if unavailable_tool_ids:
            raise TracecatValidationError(
                "Attached skills require unavailable MCP tools",
                detail={
                    "code": "skill_mcp_tools_unavailable",
                    "tool_ids": sorted(unavailable_tool_ids),
                    "preset_version_id": preset_context,
                },
            )
