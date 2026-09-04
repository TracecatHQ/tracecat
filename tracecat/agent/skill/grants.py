"""Compile tool grants from resolved immutable skill versions."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.skill.types import (
    ResolvedSkillRef,
    SkillMcpGrant,
    SkillToolGrants,
)
from tracecat.db.models import SkillVersion
from tracecat.exceptions import TracecatValidationError
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


class SkillToolGrantService(BaseWorkspaceService):
    """Load and authorize projections for already-resolved skill versions."""

    service_name = "skill_tool_grant"

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def compile_tool_grants(
        self,
        *,
        preset_version_id: uuid.UUID,
        resolved_skills: Sequence[ResolvedSkillRef],
    ) -> SkillToolGrants:
        """Compile grants for the same versions selected by head resolution."""

        if not resolved_skills:
            return SkillToolGrants()

        version_ids = [skill.skill_version_id for skill in resolved_skills]
        stmt = (
            select(SkillVersion)
            .where(
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.id.in_(version_ids),
            )
            .options(
                selectinload(SkillVersion.tools),
                selectinload(SkillVersion.mcp_tools),
            )
        )
        versions_by_id = {
            version.id: version
            for version in (await self.session.execute(stmt)).scalars().all()
        }
        missing_version_ids = sorted(
            str(version_id) for version_id in set(version_ids) - versions_by_id.keys()
        )
        if missing_version_ids:
            raise TracecatValidationError(
                "Resolved skills require unavailable versions",
                detail={
                    "code": "skill_versions_unavailable",
                    "skill_version_ids": missing_version_ids,
                    "preset_version_id": str(preset_version_id),
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
                        "preset_version_id": str(preset_version_id),
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
                    "preset_version_id": str(preset_version_id),
                },
            )

        requested_integration_ids = {
            tool.mcp_integration_id
            for tool in mcp_rows
            if tool.mcp_integration_id is not None
        }
        available_integrations = (
            await IntegrationService(
                self.session, role=self.role
            ).list_mcp_integrations()
            if requested_integration_ids
            else []
        )
        integrations_by_id = {
            integration.id: integration
            for integration in available_integrations
            if integration.id in requested_integration_ids
        }
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
                    "preset_version_id": str(preset_version_id),
                },
            )

        grants_by_integration: dict[uuid.UUID, set[str] | None] = {}
        unavailable_tool_ids: list[str] = []
        for row in mcp_rows:
            integration_id = row.mcp_integration_id
            if integration_id is None:
                continue
            if row.tool_name is None:
                grants_by_integration[integration_id] = None
                continue
            if (
                integration_id in grants_by_integration
                and grants_by_integration[integration_id] is None
            ):
                continue
            integration = integrations_by_id[integration_id]
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
            tool_names = grants_by_integration.setdefault(integration_id, set())
            if tool_names is not None:
                tool_names.add(row.tool_name)

        if unavailable_tool_ids:
            raise TracecatValidationError(
                "Attached skills require unavailable MCP tools",
                detail={
                    "code": "skill_mcp_tools_unavailable",
                    "tool_ids": sorted(unavailable_tool_ids),
                    "preset_version_id": str(preset_version_id),
                },
            )

        return SkillToolGrants(
            registry_tool_ids=registry_tool_ids,
            mcp_grants=tuple(
                SkillMcpGrant(
                    mcp_integration_id=integration_id,
                    tool_names=(
                        frozenset(tool_names) if tool_names is not None else None
                    ),
                )
                for integration_id, tool_names in sorted(
                    grants_by_integration.items(), key=lambda item: item[0]
                )
            ),
        )
