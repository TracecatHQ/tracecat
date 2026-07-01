from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import (
    UUID4,
    BaseModel,
    Field,
)
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.agent.common.stream_types import UnifiedStreamEvent
from tracecat.agent.common.types import (
    MCPHttpServerConfig,
    MCPServerConfig,
    MCPToolDefinition,
    is_http_mcp_server,
)
from tracecat.agent.mcp.internal_tools import (
    BUILDER_BUNDLED_ACTIONS,
    BUILDER_INTERNAL_TOOL_NAMES,
    get_builder_internal_tool_definitions,
)
from tracecat.agent.mcp.utils import (
    REGISTRY_MCP_SERVER_NAME,
    normalize_mcp_tool_name,
)
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.stream.connector import AgentStream
from tracecat.agent.tokens import InternalToolContext, UserMCPServerClaim
from tracecat.agent.tools import build_agent_tools
from tracecat.auth.types import Role
from tracecat.common import all_activities
from tracecat.contexts import ctx_role
from tracecat.exceptions import BuiltinRegistryHasNoSelectionError
from tracecat.logger import logger
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.tiers.entitlements import Entitlement, EntitlementService
from tracecat.tiers.service import TierService

if TYPE_CHECKING:
    from tracecat.integrations.schemas import MCPToolSummary


class BuildToolDefsArgs(BaseModel):
    role: Role
    tool_filters: ToolFilters
    tool_approvals: dict[str, bool] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    """User-defined MCP server configurations to discover tools from."""
    internal_tool_context: InternalToolContext | None = None
    """Context for internal tools (e.g., preset_id for builder assistant)."""
    fail_on_mcp_discovery_error: bool = False
    """If true, fail closed when configured user MCP tools cannot be discovered."""


class BuildAgentScopeToolDefsArgs(BaseModel):
    scope: str
    tool_filters: ToolFilters
    tool_approvals: dict[str, bool] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    """User-defined MCP server configurations to discover tools from."""
    internal_tool_context: InternalToolContext | None = None
    """Context for internal tools (e.g., preset_id for builder assistant)."""
    fail_on_mcp_discovery_error: bool = False
    """If true, fail closed when configured user MCP tools cannot be discovered."""


class BuildToolDefsResult(BaseModel):
    tool_definitions: dict[str, MCPToolDefinition]
    registry_lock: RegistryLock
    user_mcp_claims: list[UserMCPServerClaim] | None = None
    """Resolved user MCP server configs for JWT claims."""
    allowed_internal_tools: list[str] | None = None
    """List of allowed internal tool names for JWT claims."""
    tool_approvals: dict[str, bool] | None = None
    """Effective tool approval policy for the compiled scope."""


class BuildAgentToolDefsArgs(BaseModel):
    role: Role
    scopes: list[BuildAgentScopeToolDefsArgs]


class BuildAgentToolDefsResult(BaseModel):
    scopes: dict[str, BuildToolDefsResult]


class ToolApprovalPayload(BaseModel):
    tool_call_id: str
    tool_name: str
    args: dict[str, Any] | str | None = None
    metadata: dict[str, Any] | None = None


class ExecuteRemoteMCPToolArgs(BaseModel):
    mcp_auth_token: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class PersistApprovalsActivityInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    approvals: list[ToolApprovalPayload]


class ApprovalDecisionPayload(BaseModel):
    tool_call_id: str
    approved: bool
    reason: str | None = None
    decision: bool | dict[str, Any] | None = None
    decision_metadata: dict[str, Any] | None = None
    approved_by: UUID4 | None = None


class ApplyApprovalResultsActivityInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    decisions: list[ApprovalDecisionPayload]


class EmitSessionErrorInputs(BaseModel):
    role: Role
    session_id: uuid.UUID
    workspace_id: uuid.UUID
    message: str
    active_stream_id: uuid.UUID | None = None
    # When False, only persist last_error and skip the SSE stream. The runtime
    # error path has already streamed the error inline via the loopback, so it
    # persists-only; pre-stream failures stream too.
    should_stream: bool = True


# Cap stored error summaries so a runaway traceback can't bloat the session row
# or the inbox payload. The detail banner only needs a short, human-readable
# reason.
MAX_LAST_ERROR_LEN = 2000


class EmitSessionCancelledInputs(BaseModel):
    session_id: uuid.UUID
    workspace_id: uuid.UUID
    reason: str | None = None
    active_stream_id: uuid.UUID | None = None


def _stored_user_mcp_tool_policy(
    tool_name: str,
    *,
    integration_id_by_server_name: dict[str, uuid.UUID],
    policies_by_integration_id: dict[uuid.UUID, dict[str, MCPToolSummary]],
) -> MCPToolSummary | None:
    """Look up the stored per-tool policy for a discovered user MCP tool.

    Returns None when the tool name is not a user MCP tool, its server has no
    backing integration, or the integration has no stored policy for the tool.
    """
    from tracecat.agent.mcp.user_client import UserMCPClient

    parsed = UserMCPClient.parse_user_mcp_tool_name(tool_name)
    if parsed is None:
        return None
    server_name, remote_tool_name = parsed
    integration_id = integration_id_by_server_name.get(server_name)
    if integration_id is None:
        return None
    return policies_by_integration_id.get(integration_id, {}).get(remote_tool_name)


class AgentActivities:
    """Activities for agent execution."""

    def get_activities(self) -> list[Callable[..., Any]]:
        return all_activities(self)

    @staticmethod
    async def _check_tool_approval_entitlement(role: Role) -> None:
        if role.organization_id is None:
            raise ValueError("Role must have organization_id to validate entitlements")
        async with TierService.with_session() as tier_service:
            entitlement_service = EntitlementService(tier_service)
            await entitlement_service.check_entitlement(
                role.organization_id, Entitlement.AGENT_ADDONS
            )

    async def _build_scope_tool_definitions(
        self,
        args: BuildAgentScopeToolDefsArgs,
        *,
        role: Role,
    ) -> BuildToolDefsResult:
        effective_tool_approvals = dict(args.tool_approvals or {})

        # Check if this is a builder assistant session
        is_builder = (
            args.internal_tool_context is not None
            and args.internal_tool_context.entity_type == "agent_preset_builder"
        )

        # For builder sessions, add bundled actions to the tool filters
        actions_to_build = list(args.tool_filters.actions or [])
        if is_builder:
            # Add bundled registry actions for builder (core.table.*, tools.exa.*)
            for action in BUILDER_BUNDLED_ACTIONS:
                if action not in actions_to_build:
                    actions_to_build.append(action)

        try:
            result = await build_agent_tools(
                namespaces=args.tool_filters.namespaces,
                actions=actions_to_build if actions_to_build else None,
                tool_approvals=args.tool_approvals,
            )
        except ValueError as e:
            raise ApplicationError(
                str(e),
                type="AgentToolDefinitionError",
                non_retryable=True,
            ) from e
        # Convert to dict[str, MCPToolDefinition] keyed by canonical action name
        # Tools already have canonical names (with dots, e.g., "core.cases.list_cases")
        defs: dict[str, MCPToolDefinition] = {}
        for tool in result.tools:
            defs[tool.name] = MCPToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters_json_schema=tool.parameters_json_schema,
            )

        # Add internal tools for builder assistant
        allowed_internal_tools: list[str] | None = None
        if is_builder:
            # Add builder internal tools to definitions
            internal_defs = get_builder_internal_tool_definitions()
            defs.update(internal_defs)
            allowed_internal_tools = list(BUILDER_INTERNAL_TOOL_NAMES)
            logger.info(
                "Added builder internal tools",
                tool_count=len(internal_defs),
                tools=list(internal_defs.keys()),
            )

        # Discover user MCP tools if configured
        user_mcp_claims: list[UserMCPServerClaim] | None = None
        if args.mcp_servers:
            from tracecat.agent.mcp.user_client import (
                UserMCPClient,
                discover_user_mcp_tools,
            )
            from tracecat.agent.preset.service import AgentPresetService

            http_servers = [cfg for cfg in args.mcp_servers if is_http_mcp_server(cfg)]
            if not http_servers:
                logger.info("No HTTP MCP servers configured for discovery")
                http_servers = []

            # Hydrate headers from the DB for the duration of this activity.
            # Configs that arrive here carry ``id`` but no ``headers`` (the
            # boundary-safe shape produced by ``resolve_mcp_integration_refs``).
            # Fetch secrets per server, attach for ``tools/list``, and drop
            # them before returning so they never enter ``BuildToolDefsResult``
            # or leak across the Temporal boundary.
            hydrated_servers: list[MCPHttpServerConfig] = [
                {**cfg} for cfg in http_servers
            ]
            configs_with_integration_id: list[
                tuple[MCPHttpServerConfig, uuid.UUID]
            ] = []
            integration_id_by_server_name: dict[str, uuid.UUID] = {}
            for hydrated in hydrated_servers:
                if integration_id_str := hydrated.get("id"):
                    try:
                        integration_id = uuid.UUID(integration_id_str)
                    except ValueError:
                        logger.warning(
                            "Invalid MCP integration id on server config",
                            server_name=hydrated["name"],
                            integration_id=integration_id_str,
                        )
                        continue
                    configs_with_integration_id.append((hydrated, integration_id))
                    integration_id_by_server_name[hydrated["name"]] = integration_id
            tool_policies_by_integration_id: dict[
                uuid.UUID, dict[str, MCPToolSummary]
            ] = {}
            if configs_with_integration_id:
                async with AgentPresetService.with_session(role=role) as svc:
                    tool_policies_by_integration_id = (
                        await svc.resolve_mcp_integration_tool_policies(
                            [
                                integration_id
                                for _, integration_id in configs_with_integration_id
                            ]
                        )
                    )
                    for hydrated, integration_id in configs_with_integration_id:
                        try:
                            secrets = await svc.resolve_mcp_integration_secrets(
                                integration_id
                            )
                        except ValueError:
                            secrets = None
                        if secrets:
                            hydrated["headers"] = secrets

            try:
                user_mcp_tools = await discover_user_mcp_tools(
                    hydrated_servers,
                    fail_on_error=args.fail_on_mcp_discovery_error,
                )
                # Add user MCP tools to definitions, honoring stored policy:
                # disabled or missing tools are dropped, approval-gated tools
                # are recorded in the effective approval map.
                for tool_name, tool_def in user_mcp_tools.items():
                    parsed = UserMCPClient.parse_user_mcp_tool_name(tool_name)
                    has_dotted_remote_name = parsed is not None and "." in parsed[1]
                    # Unlike registry/internal tools, user MCP tool names are
                    # registered with the trusted MCP server verbatim (see
                    # ``build_token_scoped_tools``), so a dotted remote name
                    # (e.g. ``issue.get``) reaches the model provider as
                    # ``mcp__{server}__issue.get``. Provider tool-name
                    # constraints reject dots, so an otherwise-valid tool would
                    # make the agent fail to start. Drop these regardless of
                    # approval status; approval-gated ones also can't round-trip
                    # their ``mcp.{server}.{tool}`` approval key back to a
                    # router name.
                    if has_dotted_remote_name:
                        logger.warning(
                            "Skipping user MCP tool with unsupported dotted name",
                            tool_name=tool_name,
                            remote_tool_name=parsed[1] if parsed else None,
                        )
                        continue
                    policy = _stored_user_mcp_tool_policy(
                        tool_name,
                        integration_id_by_server_name=integration_id_by_server_name,
                        policies_by_integration_id=tool_policies_by_integration_id,
                    )
                    if policy is not None:
                        if not policy.enabled or policy.status != "available":
                            logger.info(
                                "Skipping disabled MCP tool", tool_name=tool_name
                            )
                            continue
                        if policy.requires_approval:
                            approval_key = normalize_mcp_tool_name(
                                f"mcp__{REGISTRY_MCP_SERVER_NAME}__{tool_name}"
                            )
                            effective_tool_approvals[approval_key] = True
                    defs[tool_name] = tool_def

                # JWT claims carry the source integration id when available so
                # the trusted MCP server can re-resolve headers per call. For
                # legacy in-flight payloads that don't carry ``id`` (recorded
                # before the refs-only cutover), fall back to the pre-rollout
                # inline shape — otherwise discovery would add ``mcp__*`` tools
                # that the trusted server can't authorize at call time.
                user_mcp_claims = []
                for cfg in http_servers:
                    integration_id_str = cfg.get("id")
                    if integration_id_str:
                        user_mcp_claims.append(
                            UserMCPServerClaim(
                                name=cfg["name"],
                                id=uuid.UUID(integration_id_str),
                            )
                        )
                        continue
                    # Legacy replay path: no id → trusted server uses inline
                    # url/headers from the claim itself. ``execute_user_mcp_tool``
                    # has the matching read-side branch.
                    user_mcp_claims.append(
                        UserMCPServerClaim(
                            name=cfg["name"],
                            url=cfg["url"],
                            transport=cfg.get("transport", "http"),
                            headers=cfg.get("headers", {}),
                            timeout=cfg.get("timeout"),
                        )
                    )

                logger.info(
                    "Discovered user MCP tools",
                    tool_count=len(user_mcp_tools),
                    server_count=len(hydrated_servers),
                )
            except Exception as e:
                logger.error(
                    "Failed to discover user MCP tools",
                    error_type=type(e).__name__,
                    server_count=len(hydrated_servers),
                )
                if args.fail_on_mcp_discovery_error:
                    raise ApplicationError(
                        "Failed to discover configured MCP tools for agent scope",
                        str(e),
                        type="AgentToolDefinitionError",
                        non_retryable=True,
                    ) from e
                # Continue without user MCP tools - don't fail the whole operation
            finally:
                # Defensive: ensure hydrated configs (with headers) drop out
                # of scope before this activity returns. Local variable; this
                # is documentation more than enforcement.
                hydrated_servers = []

        if any(effective_tool_approvals.values()):
            await self._check_tool_approval_entitlement(role)

        # Resolve registry lock for these actions
        # This provides origin→version mappings needed for action execution
        # Note: User MCP tools and internal tools don't need registry lock resolution
        registry_action_names = {
            name
            for name in defs.keys()
            if not name.startswith("mcp__") and not name.startswith("internal.")
        }
        try:
            async with RegistryLockService.with_session() as lock_service:
                registry_lock = await lock_service.resolve_lock_with_bindings(
                    registry_action_names
                )
        except BuiltinRegistryHasNoSelectionError as e:
            raise ApplicationError(
                str(e),
                e.detail,
                type=e.__class__.__name__,
            ) from e

        return BuildToolDefsResult(
            tool_definitions=defs,
            registry_lock=registry_lock,
            user_mcp_claims=user_mcp_claims,
            allowed_internal_tools=allowed_internal_tools,
            tool_approvals=effective_tool_approvals or None,
        )

    @activity.defn
    async def build_tool_definitions(
        self,
        args: BuildToolDefsArgs,
    ) -> BuildToolDefsResult:
        # Set role context for services that require organization context
        ctx_role.set(args.role)

        # Runtime guard for approval-gated agent flows. This ensures direct
        # workflow execution paths still enforce entitlements.
        if args.tool_approvals:
            await self._check_tool_approval_entitlement(args.role)

        return await self._build_scope_tool_definitions(
            BuildAgentScopeToolDefsArgs(
                scope="root",
                tool_filters=args.tool_filters,
                tool_approvals=args.tool_approvals,
                mcp_servers=args.mcp_servers,
                internal_tool_context=args.internal_tool_context,
                fail_on_mcp_discovery_error=args.fail_on_mcp_discovery_error,
            ),
            role=args.role,
        )

    @activity.defn
    async def build_agent_tool_definitions(
        self,
        args: BuildAgentToolDefsArgs,
    ) -> BuildAgentToolDefsResult:
        # Compile all agent scopes in one activity while preserving partitioned
        # outputs for MCP tokens, approvals, user MCP claims, and registry locks.
        ctx_role.set(args.role)
        if any(scope.tool_approvals for scope in args.scopes):
            await self._check_tool_approval_entitlement(args.role)

        results: dict[str, BuildToolDefsResult] = {}
        for scope in args.scopes:
            if scope.scope in results:
                raise ApplicationError(
                    f"Duplicate agent compile scope '{scope.scope}'",
                    non_retryable=True,
                )
            results[scope.scope] = await self._build_scope_tool_definitions(
                scope,
                role=args.role,
            )

        return BuildAgentToolDefsResult(scopes=results)

    @activity.defn
    async def emit_session_error(self, args: EmitSessionErrorInputs) -> None:
        """Finalize a terminal agent error.

        Persists ``last_error`` on the session (the sole durable run-outcome
        signal the inbox reads) and, for pre-stream failures, also pushes the
        error onto the SSE stream since those happen before the loopback is
        wired up. The runtime path streams inline already and passes
        ``should_stream=False`` to persist-only.

        Best-effort: a persistence failure must not mask the agent's real error
        or abort propagation, so it is logged and swallowed.
        """
        from tracecat.agent.session.service import AgentSessionService

        ctx_role.set(args.role)
        try:
            async with AgentSessionService.with_session(role=args.role) as service:
                agent_session = await service.get_session(args.session_id)
                if agent_session is not None:
                    agent_session.last_error = args.message[:MAX_LAST_ERROR_LEN]
                    service.session.add(agent_session)
                    await service.session.commit()
                else:
                    logger.warning(
                        "Cannot persist error for unknown agent session",
                        session_id=str(args.session_id),
                    )
        except Exception as e:
            logger.warning(
                "Failed to persist terminal agent session error",
                session_id=str(args.session_id),
                error=str(e),
            )

        if not args.should_stream:
            return

        stream = await AgentStream.new(
            session_id=args.session_id,
            workspace_id=args.workspace_id,
            stream_id=args.active_stream_id,
        )
        await stream.error(args.message)
        await stream.done()

    @activity.defn
    async def emit_session_cancelled(self, args: EmitSessionCancelledInputs) -> None:
        """Push an advisory cancelled-turn notice onto the session's SSE stream.

        Used by workflow branches that cancel while waiting on approval, since
        those happen outside a running executor activity and never reach the
        loopback's own cancelled-notice emission.
        """
        stream = await AgentStream.new(
            session_id=args.session_id,
            workspace_id=args.workspace_id,
            stream_id=args.active_stream_id,
        )
        await stream.append(UnifiedStreamEvent.cancelled_event(reason=args.reason))
        await stream.done()

    @activity.defn
    async def execute_remote_mcp_tool(self, args: ExecuteRemoteMCPToolArgs) -> str:
        """Execute an approved remote MCP tool through the trusted MCP router."""
        from fastmcp.exceptions import ToolError

        from tracecat.agent.mcp.trusted_server import call_token_scoped_tool
        from tracecat.agent.tokens import verify_mcp_token

        try:
            claims = verify_mcp_token(args.mcp_auth_token)
        except ValueError as e:
            raise ApplicationError(
                "MCP token verification failed",
                type="AgentToolExecutionError",
                non_retryable=True,
            ) from e

        try:
            return await call_token_scoped_tool(args.tool_name, args.args, claims)
        except ToolError as e:
            raise ApplicationError(
                str(e),
                type="AgentToolExecutionError",
                non_retryable=True,
            ) from e
