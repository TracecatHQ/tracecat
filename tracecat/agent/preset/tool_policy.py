"""Resolve authored and skill-derived tools before evaluating preset policy."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from tracecat.agent.mcp.utils import (
    MCP_TOOL_NAME_RE,
    REGISTRY_MCP_SERVER_NAME,
    normalize_mcp_tool_name,
)
from tracecat.agent.preset.types import (
    EffectivePresetTools,
    PresetToolInputs,
    PresetToolSource,
)
from tracecat.agent.skill.dependencies import SkillToolDependencyService
from tracecat.agent.skill.types import SkillMcpGrant
from tracecat.agent.tools import EXCLUDED_AGENT_ACTIONS
from tracecat.db.models import MCPIntegration, SkillVersion
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.service import BaseWorkspaceService


class PresetToolPolicyService(BaseWorkspaceService):
    """Batch metadata loading shared by runtime, authoring, and API reporting.

    This resolves policy, not credentials or remote tool discovery. Runtime
    dependency validation and execution-time policy guards remain authoritative.
    """

    service_name = "preset_tool_policy"

    async def resolve_many(
        self, inputs: Sequence[PresetToolInputs]
    ) -> dict[uuid.UUID, EffectivePresetTools]:
        if not inputs:
            return {}
        metadata = await SkillToolDependencyService(
            self.session, role=self.role
        ).load_metadata(
            list({vid for item in inputs for vid in item.skill_version_ids}),
            mcp_integration_ids=[
                mid for item in inputs for mid in item.mcp_integrations
            ],
        )
        return {
            item.key: resolve_tool_policy(
                item, metadata.versions, metadata.integrations
            )
            for item in inputs
        }


def resolve_tool_policy(
    inputs: PresetToolInputs,
    versions: Mapping[uuid.UUID, SkillVersion],
    integrations: Mapping[uuid.UUID, MCPIntegration],
) -> EffectivePresetTools:
    """Combine declarations, then derive policy once regardless of origin."""
    registry_sources = [PresetToolSource(tool_id=tool) for tool in inputs.actions]
    mcp_sources: list[tuple[SkillMcpGrant, PresetToolSource]] = []
    for raw_id in inputs.mcp_integrations:
        try:
            integration_id = uuid.UUID(raw_id)
        except ValueError:
            continue  # Runtime reference validation retains responsibility for bad IDs.
        mcp_sources.append(
            (
                SkillMcpGrant(integration_id, None),
                PresetToolSource(tool_id=f"mcp.{integration_id}"),
            )
        )
    for version_id in inputs.skill_version_ids:
        version = versions.get(version_id)
        if version is None:
            continue  # Read reporting must remain available for broken dependencies.
        registry_sources.extend(
            PresetToolSource(tool.tool_id, version.skill_id, version.name)
            for tool in version.tools
        )
        for tool in version.mcp_tools:
            if tool.mcp_integration_id is not None:
                mcp_sources.append(
                    (
                        SkillMcpGrant(
                            tool.mcp_integration_id,
                            frozenset([tool.tool_name]) if tool.tool_name else None,
                        ),
                        PresetToolSource(tool.tool_id, version.skill_id, version.name),
                    )
                )

    actions: dict[str, None] = {}
    blocked: list[PresetToolSource] = []
    for source in registry_sources:
        if inputs.namespaces and not any(
            source.tool_id.startswith(namespace) for namespace in inputs.namespaces
        ):
            blocked.append(source)
        elif source.tool_id not in EXCLUDED_AGENT_ACTIONS:
            actions[source.tool_id] = None

    grants: dict[uuid.UUID, set[str] | None] = {}
    internet_sources: list[PresetToolSource] = []
    for grant, source in mcp_sources:
        integration = integrations.get(grant.mcp_integration_id)
        if integration is not None and integration.server_type == "stdio":
            internet_sources.append(source)
        if grant.tool_names is None:
            grants[grant.mcp_integration_id] = None
        else:
            names = grants.setdefault(grant.mcp_integration_id, set())
            if names is not None:
                names.update(grant.tool_names)

    approvals = dict(inputs.tool_approvals)
    for integration_id, allowed_names in grants.items():
        integration = integrations.get(integration_id)
        # Stdio policy is enforced by the local transport, not HTTP discovery.
        if integration is None or integration.server_type == "stdio":
            continue
        for tool in (
            MCPToolSummary.validate_stored(
                integration.tools, mcp_integration_id=integration.id
            )
            or ()
        ):
            if (
                tool.enabled
                and tool.status == "available"
                and tool.requires_approval
                and MCP_TOOL_NAME_RE.fullmatch(tool.name)
                and (allowed_names is None or tool.name in allowed_names)
            ):
                key = normalize_mcp_tool_name(
                    f"mcp__{REGISTRY_MCP_SERVER_NAME}__mcp__{integration.slug}__{tool.name}"
                )
                approvals[key] = True

    return EffectivePresetTools(
        actions=tuple(actions),
        mcp_grants=tuple(
            SkillMcpGrant(
                integration_id, frozenset(names) if names is not None else None
            )
            for integration_id, names in grants.items()
        ),
        tool_approvals=approvals,
        requires_internet_access=bool(internet_sources),
        blocked_tools=tuple(blocked),
        internet_sources=tuple(internet_sources),
    )
