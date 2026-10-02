"""Workspace-authorized compiler lookups through existing resource services.

Both authoring and pinned runtime compilation read stored metadata. Neither path
contacts an MCP server or resolves credentials. Live schemas, locks, persistence,
and call-time authorization belong to subsequent preparation/dispatch layers.
"""

import hashlib
from functools import cached_property
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat import config
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.preset.tool_policy import resolve_tool_policy
from tracecat.agent.preset.types import PresetToolInputs
from tracecat.agent.references.compiler_types import (
    Declaration,
    GraphLimits,
    ReferenceLookupError,
    ReferenceResource,
    ScopeGrants,
    ScopePolicy,
)
from tracecat.agent.references.contracts import AuthoredSource, SelectedReference
from tracecat.agent.references.uri import ReferenceDiagnosticCode as Code
from tracecat.agent.references.uri import ReferenceKind as Kind
from tracecat.agent.references.uri import ReferenceTarget, ReferenceURIError
from tracecat.agent.skill.service import SkillService
from tracecat.agent.skill.types import SkillMcpGrant
from tracecat.agent.skill.validation import get_mcp_grant_support_error
from tracecat.agent.subagents import AgentSubagentsConfig, ResolvedAttachedSubagentRef
from tracecat.authz.controls import check_scopes
from tracecat.db.models import AgentPresetVersionSkill, MCPIntegration, SkillVersion
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.integrations.service import IntegrationService
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TablesService
from tracecat.tiers.enums import Entitlement
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService

_READ_SCOPES = {
    Kind.SKILL: "agent:read",
    Kind.AGENT: "agent:read",
    Kind.TABLE: "table:read",
    Kind.WORKFLOW: "workflow:read",
    Kind.MCP_SERVER: "integration:read",
    Kind.MCP_TOOL: "integration:read",
    Kind.TOOL: "org:registry:read",
}


class ReferenceService(BaseWorkspaceService):
    """Use one instance per compile; mutable integration metadata stays local."""

    service_name = "references"

    @cached_property
    def _integrations(self) -> dict[UUID, MCPIntegration]:
        return {}

    async def resolve(
        self, target: ReferenceTarget, *, version_id: UUID | None, limits: GraphLimits
    ) -> ReferenceResource:
        try:
            check_scopes(self.role, _READ_SCOPES[target.kind])
            match target.kind:
                case Kind.SKILL:
                    return await self._skill(target, version_id, limits)
                case Kind.AGENT:
                    return await self._agent(target, version_id, limits)
                case Kind.TOOL:
                    action = await RegistryActionsService(
                        self.session, role=self.role
                    ).get_action_from_index(target.identity)
                    if action is None:
                        raise ReferenceLookupError()
                case Kind.MCP_TOOL | Kind.MCP_SERVER:
                    await self._integration(target)
                case Kind.TABLE:
                    await TablesService(self.session, role=self.role).get_table(
                        UUID(target.identity)
                    )
                case Kind.WORKFLOW:
                    return await self._workflow(target, version_id)
            return ReferenceResource(selected=SelectedReference(target=target))
        except (
            TracecatNotFoundError,
            EntitlementRequired,
            TracecatAuthorizationError,
            TracecatValidationError,
        ) as exc:
            # No distinction between an absent and an inaccessible identity.
            raise ReferenceLookupError() from exc
        except (ReferenceURIError, ValidationError) as exc:
            raise ReferenceLookupError(Code.INVALID_SOURCE) from exc

    async def _skill(
        self, target: ReferenceTarget, version_id: UUID | None, limits: GraphLimits
    ) -> ReferenceResource:
        service = SkillService(self.session, role=self.role)
        skill = await service.get_skill(UUID(target.identity))
        if skill is None or (version_id is None and skill.current_version_id is None):
            raise ReferenceLookupError()
        selected_id = version_id or skill.current_version_id
        assert selected_id is not None
        version = await service.get_version_read(
            skill_id=skill.id, version_id=selected_id
        )
        if (
            version.total_size_bytes > limits.source_bytes
            or version.file_count > limits.edges
        ):
            raise ReferenceLookupError(Code.LIMIT_EXCEEDED)
        sources: list[AuthoredSource] = []
        for entry in version.files:
            if not entry.path.lower().endswith(".md"):
                continue
            file = await service.get_version_file(
                skill_id=skill.id, version_id=version.id, path=entry.path
            )
            if file is None or file.text_content is None:
                raise ReferenceLookupError(Code.INVALID_SOURCE)
            sources.append(
                AuthoredSource(
                    origin="skill_file",
                    owner=target,
                    version_id=version.id,
                    path=entry.path,
                    content_hash=entry.sha256,
                    markdown=file.text_content,
                )
            )
        stmt = (
            select(SkillVersion)
            .where(
                SkillVersion.workspace_id == self.workspace_id,
                SkillVersion.id == version.id,
            )
            .options(
                selectinload(SkillVersion.tools), selectinload(SkillVersion.mcp_tools)
            )
        )
        row = (await self.session.execute(stmt)).scalar_one()
        declarations = [
            Declaration(ReferenceTarget(Kind.TOOL, t.tool_id), "skill_tool")
            for t in row.tools
        ]
        for tool in row.mcp_tools:
            if tool.mcp_integration_id is None:
                raise ReferenceLookupError()
            kind = Kind.MCP_TOOL if tool.tool_name else Kind.MCP_SERVER
            declarations.append(
                Declaration(
                    ReferenceTarget(kind, str(tool.mcp_integration_id), tool.tool_name),
                    "skill_tool",
                )
            )
        return ReferenceResource(
            SelectedReference(
                target=target,
                version_id=version.id,
                content_hash=version.manifest_sha256,
            ),
            tuple(sources),
            tuple(declarations),
            slug=skill.slug or "",
        )

    async def _agent(
        self, target: ReferenceTarget, version_id: UUID | None, limits: GraphLimits
    ) -> ReferenceResource:
        service = AgentPresetService(self.session, role=self.role)
        version = await service.resolve_agent_preset_version(
            preset_id=UUID(target.identity), preset_version_id=version_id
        )
        preset = await service.get_preset(version.preset_id)
        if preset is None:
            raise ReferenceLookupError()
        text = version.instructions or ""
        if len(text.encode()) > limits.source_bytes:
            raise ReferenceLookupError(Code.LIMIT_EXCEEDED)
        source = AuthoredSource(
            origin="agent_instructions",
            owner=target,
            version_id=version.id,
            path="instructions.md",
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            markdown=text,
        )
        declarations = [
            Declaration(ReferenceTarget(Kind.TOOL, action))
            for action in version.actions or ()
        ]
        declarations.extend(
            Declaration(ReferenceTarget(Kind.MCP_SERVER, integration))
            for integration in version.mcp_integrations or ()
        )
        # Read explicit identities, not the flattened actions or latest skill heads.
        skill_ids = (
            (
                await self.session.execute(
                    select(AgentPresetVersionSkill.skill_id).where(
                        AgentPresetVersionSkill.workspace_id == self.workspace_id,
                        AgentPresetVersionSkill.preset_version_id == version.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        declarations.extend(
            Declaration(ReferenceTarget(Kind.SKILL, str(sid))) for sid in skill_ids
        )
        for ref in AgentSubagentsConfig.model_validate(version.agents).subagents:
            if isinstance(ref, ResolvedAttachedSubagentRef):
                child_id = ref.preset_id
            else:
                child = await service.get_preset_by_slug(ref.preset)
                if child is None:
                    raise ReferenceLookupError()
                child_id = child.id
            declarations.append(
                Declaration(ReferenceTarget(Kind.AGENT, str(child_id)), alias=ref.alias)
            )
        return ReferenceResource(
            SelectedReference(target=target, version_id=version.id),
            (source,),
            tuple(declarations),
            ScopePolicy(
                tuple(version.namespaces or ()),
                tuple((version.tool_approvals or {}).items()),
                version.enable_internet_access,
            ),
            preset.slug,
        )

    async def _workflow(
        self, target: ReferenceTarget, version_id: UUID | None
    ) -> ReferenceResource:
        wid = WorkflowUUID.new(target.identity)
        workflow = await WorkflowsManagementService(
            self.session, role=self.role
        ).get_workflow(wid, normalize=False)
        if workflow is None:
            raise ReferenceLookupError()
        service = WorkflowDefinitionsService(self.session, role=self.role)
        definition = (
            await service.get_definition_by_id(version_id)
            if version_id
            else await service.get_definition_by_workflow_id(
                wid, load_relationships=False
            )
        )
        if definition is None or definition.workflow_id != wid:
            raise ReferenceLookupError()
        return ReferenceResource(
            SelectedReference(target=target, version_id=definition.id)
        )

    async def _integration(self, target: ReferenceTarget) -> MCPIntegration:
        identity = UUID(target.identity)
        integration = self._integrations.get(identity)
        if integration is None:
            integration = await IntegrationService(
                self.session, role=self.role
            ).get_mcp_integration(mcp_integration_id=identity)
            if integration is not None:
                self._integrations[identity] = integration
        if integration is None:
            raise ReferenceLookupError()
        if integration.server_type not in {"http", "stdio"}:
            raise ReferenceLookupError(Code.UNSUPPORTED_CAPABILITY)
        if integration.server_type == "http" and integration.tools is None:
            raise ReferenceLookupError()
        MCPToolSummary.validate_stored(
            integration.tools, mcp_integration_id=integration.id
        )
        if get_mcp_grant_support_error(
            server_type=integration.server_type,
            tool_name=target.tool_name,
            tool_id=target.identity,
        ):
            raise ReferenceLookupError(Code.UNSUPPORTED_CAPABILITY)
        if target.kind == Kind.MCP_TOOL:
            tools = (
                MCPToolSummary.validate_stored(
                    integration.tools, mcp_integration_id=integration.id
                )
                or ()
            )
            if not any(
                t.name == target.tool_name and t.enabled and t.status == "available"
                for t in tools
            ):
                raise ReferenceLookupError()
        return integration

    async def policy(
        self, targets: tuple[ReferenceTarget, ...], policy: ScopePolicy
    ) -> ScopeGrants:
        actions = tuple(t.identity for t in targets if t.kind == Kind.TOOL)
        grants: list[SkillMcpGrant] = []
        integrations: dict[UUID, MCPIntegration] = {}
        for target in targets:
            if target.kind in {Kind.MCP_SERVER, Kind.MCP_TOOL}:
                integration = await self._integration(target)
                integrations[integration.id] = integration
                grants.append(
                    SkillMcpGrant(
                        integration.id,
                        frozenset((target.tool_name,)) if target.tool_name else None,
                    )
                )
        effective = resolve_tool_policy(
            PresetToolInputs(
                UUID(int=0), (), policy.namespaces, (), dict(policy.approvals), ()
            ),
            {},
            integrations,
            derived_actions=actions,
            derived_mcp_grants=grants,
        )
        if denied := set(actions) - set(effective.actions):
            raise ReferenceLookupError(
                Code.FORBIDDEN, target=ReferenceTarget(Kind.TOOL, sorted(denied)[0])
            )
        count = len(effective.actions)
        for grant in effective.mcp_grants:
            integration = integrations[grant.mcp_integration_id]
            tools = (
                MCPToolSummary.validate_stored(
                    integration.tools, mcp_integration_id=integration.id
                )
                or ()
            )
            count += (
                len(grant.tool_names)
                if grant.tool_names is not None
                else sum(t.enabled and t.status == "available" for t in tools)
            )
        if (
            config.TRACECAT__AGENT_MAX_TOOLS > 0
            and len(effective.actions) > config.TRACECAT__AGENT_MAX_TOOLS
        ):
            raise ReferenceLookupError(Code.LIMIT_EXCEEDED)
        if effective.tool_approvals:
            try:
                await self.require_entitlement(Entitlement.AGENT_ADDONS)
            except EntitlementRequired as exc:
                raise ReferenceLookupError(Code.FORBIDDEN) from exc
        return ScopeGrants(
            effective.actions,
            tuple((g.mcp_integration_id, g.tool_names) for g in effective.mcp_grants),
            tuple(effective.tool_approvals.items()),
            policy.internet_access or effective.requires_internet_access,
            count,
        )
