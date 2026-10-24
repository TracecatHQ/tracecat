"""Tests for agent Temporal activities.

These tests cover:
- Session management activities (create, load)
- run_agent_activity (sandboxed/direct subprocess execution)
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    StatusCode,
    TraceFlags,
    TraceState,
)
from pydantic import HttpUrl
from temporalio.exceptions import ApplicationError
from tracecat_ee.agent import activities as agent_activities
from tracecat_ee.agent.activities import (
    AgentActivities,
    BuildAgentScopeToolDefsArgs,
    BuildAgentToolDefsArgs,
    BuildToolDefsArgs,
    BuildToolDefsResult,
    EmitSessionDoneInputs,
    EmitSessionErrorInputs,
)

from tracecat import config
from tracecat.agent.common.config import build_agent_runtime_uv_env
from tracecat.agent.common.exceptions import (
    AgentToolLimitExceededError,
    UserMCPDiscoveryAuthError,
    UserMCPDiscoveryTimeoutError,
)
from tracecat.agent.common.fs import force_rmtree
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.diagnostics import LLMErrorDiagnostics
from tracecat.agent.error_policy import (
    agent_executor_timed_out,
    user_agent_execution_failed,
)
from tracecat.agent.executor.activity import (
    AgentExecutorInput,
    AgentExecutorResult,
    AgentOtelInputs,
    SandboxedAgentExecutor,
    _cancel_task_with_timeout,
    _hydrate_sdk_session_history,
    run_agent_activity,
)
from tracecat.agent.executor.loopback import (
    LoopbackHandler,
    LoopbackInput,
    LoopbackResult,
)
from tracecat.agent.mcp import user_client
from tracecat.agent.otel_config import AgentOtelConfig, ResolvedAgentOtelConfig
from tracecat.agent.otel_delivery import OtelDeliveryPool
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.runtime.claude_code.broker import (
    ClaudeTurnRequest,
    ConcurrentSessionTurnError,
)
from tracecat.agent.runtime.session_paths import job_uv_state_dir
from tracecat.agent.sandbox.llm_proxy import LLMProxyError
from tracecat.agent.schemas import ToolFilters
from tracecat.agent.session.activities import (
    CreateSessionInput,
    LoadSessionInput,
    LoadSessionResult,
    create_session_activity,
    load_session_activity,
)
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.subagents import (
    ResolvedAgentsConfig,
    ResolvedAttachedSubagentRef,
    has_manual_tool_approvals,
)
from tracecat.agent.tools import BuildToolsResult, create_tool_from_registry
from tracecat.agent.types import AgentConfig, Tool
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    EntitlementRequired,
    RegistryLockAmbiguousActionError,
    RegistryLockInvalidDataError,
)
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.observability.otel import (
    initialize_platform_tracing,
    shutdown_platform_tracing,
)
from tracecat.observability.types import PlatformErrorCapture
from tracecat.registry.actions.types import IndexedActionResult
from tracecat.registry.lock.service import RegistryLockService
from tracecat.registry.lock.types import RegistryLock
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.temporal.errors import extract_error_classification


@pytest.fixture
def mock_role() -> Role:
    """Create a mock role for testing."""
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-agent-executor"],
    )


@pytest.fixture
def mock_session_id() -> uuid.UUID:
    """Create a mock session ID for testing."""
    return uuid.uuid4()


@pytest.fixture
def mock_agent_config() -> AgentConfig:
    """Create a mock agent config for testing."""
    agent_config = cast(Any, AgentConfig)
    return cast(
        AgentConfig,
        agent_config(
            model_name="claude-3-5-sonnet-20241022",
            model_provider="anthropic",
        ),
    )


def _seed_realistic_agent_job_tree(job_dir: Path) -> list[Path]:
    """Seed the job-scoped paths that the executor removes after each run."""
    seeded_paths: list[Path] = []
    uv_state_dir = job_uv_state_dir(job_dir)
    for env_var, configured_path in build_agent_runtime_uv_env(uv_state_dir).items():
        if env_var == "UV_LINK_MODE":
            continue
        nested_file = Path(configured_path) / "nested" / f"{env_var.lower()}.state"
        nested_file.parent.mkdir(parents=True, exist_ok=True)
        nested_file.write_text("job-scoped uv state")
        seeded_paths.extend((nested_file.parent, nested_file))

    socket_path = job_dir / "sockets" / "control.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.write_text("socket placeholder")
    skill_path = job_dir / "home" / ".claude" / "skills" / "example" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("# Example")
    seeded_paths.extend((uv_state_dir, socket_path, skill_path))
    return seeded_paths


@pytest.fixture
def executor_input(
    mock_role: Role,
    mock_session_id: uuid.UUID,
    mock_agent_config: AgentConfig,
) -> AgentExecutorInput:
    return AgentExecutorInput(
        session_id=mock_session_id,
        workspace_id=mock_role.workspace_id or uuid.uuid4(),
        user_prompt="Test prompt",
        config=mock_agent_config,
        role=mock_role,
        mcp_auth_token="mock-jwt-token",
        llm_gateway_auth_token="mock-llm-token",
    )


def _async_ctx(obj: object) -> Callable[..., AbstractAsyncContextManager[Any]]:
    @asynccontextmanager
    async def ctx(*_args: object, **_kwargs: object) -> AsyncIterator[Any]:
        yield obj

    return ctx


def _service_ctx(service: object) -> AsyncMock:
    ctx = AsyncMock()
    ctx.__aenter__.return_value = service
    return ctx


def _patch_registry(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lock: RegistryLock | Exception | None = None,
    build_tools: Callable[..., Awaitable[BuildToolsResult]] | None = None,
) -> MagicMock:
    lock_service = MagicMock(spec=RegistryLockService)
    if isinstance(lock, Exception):
        lock_service.resolve_lock_with_bindings = AsyncMock(side_effect=lock)
    else:
        lock_service.resolve_lock_with_bindings = AsyncMock(
            return_value=lock or RegistryLock(origins={}, actions={})
        )
    monkeypatch.setattr(RegistryLockService, "with_session", _async_ctx(lock_service))
    monkeypatch.setattr(
        agent_activities,
        "build_agent_tools",
        build_tools
        or AsyncMock(return_value=BuildToolsResult(tools=[], collected_secrets=set())),
    )
    return lock_service


def _patch_preset_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    policies: dict[uuid.UUID, dict[str, MCPToolSummary]],
    secrets: dict[str, str] | None = None,
    refs: list[dict[str, Any]] | None = None,
) -> MagicMock:
    service = MagicMock(spec=AgentPresetService)
    service.resolve_mcp_integration_tool_policies = AsyncMock(return_value=policies)
    service.resolve_mcp_integration_refs = AsyncMock(return_value=refs)
    service.resolve_mcp_integration_secrets = AsyncMock(return_value=secrets or {})
    monkeypatch.setattr(AgentPresetService, "with_session", _async_ctx(service))
    return service


def _mcp_tools(server: str, *names: str) -> dict[str, MCPToolDefinition]:
    return {
        f"mcp__{server}__{name}": MCPToolDefinition(
            name=f"mcp__{server}__{name}",
            description="Synthetic tool",
            parameters_json_schema={"type": "object"},
        )
        for name in names
    }


REGISTRY_APPROVAL_DEFAULTS = {
    "custom.delete": True,
    "custom.read": False,
    "custom.waived": True,
}


@pytest.fixture
def registry_default_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build tools from REGISTRY_APPROVAL_DEFAULTS through the real tool factory."""

    def indexed(name: str) -> IndexedActionResult:
        manifest_action = SimpleNamespace(
            description=f"Synthetic {name}",
            interface={"expects": {"type": "object"}},
        )
        return cast(
            IndexedActionResult,
            SimpleNamespace(
                index_entry=SimpleNamespace(
                    options={"requires_approval": REGISTRY_APPROVAL_DEFAULTS[name]}
                ),
                manifest=SimpleNamespace(actions={name: manifest_action}),
            ),
        )

    async def mock_build_agent_tools(
        *,
        actions: list[str] | None,
        tool_approvals: dict[str, bool] | None,
        **_kwargs: Any,
    ) -> BuildToolsResult:
        tools = [
            await create_tool_from_registry(
                name, indexed(name), tool_approvals=tool_approvals
            )
            for name in actions or []
        ]
        return BuildToolsResult(tools=tools, collected_secrets=set())

    _patch_registry(monkeypatch, build_tools=mock_build_agent_tools)


class TestBuildToolDefinitionsActivity:
    @pytest.mark.anyio
    @pytest.mark.parametrize("raw_names", [("a__b", "a.b"), ("a.b", "a__b")])
    @pytest.mark.parametrize(
        ("stored_approval", "precomputed_approval"),
        [(True, False), (False, True), (False, False)],
    )
    async def test_rejected_name_cannot_erase_surviving_tool_approval(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
        raw_names: tuple[str, str],
        stored_approval: bool,
        precomputed_approval: bool,
    ) -> None:
        integration_id = uuid.uuid4()
        approval_key = "mcp.example.a.b"
        _patch_preset_service(
            monkeypatch,
            policies={
                integration_id: {
                    "a__b": MCPToolSummary(
                        name="a__b", requires_approval=stored_approval
                    ),
                    "a.b": MCPToolSummary(name="a.b", requires_approval=True),
                }
            },
        )
        _patch_registry(monkeypatch)
        monkeypatch.setattr(
            user_client,
            "discover_user_mcp_tools",
            AsyncMock(return_value=_mcp_tools("example", *raw_names)),
        )
        result = await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                tool_approvals={approval_key: True} if precomputed_approval else None,
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "example",
                        "url": "https://example.test/mcp",
                        "id": str(integration_id),
                    }
                ],
            )
        )
        assert set(result.tool_definitions) == {"mcp__example__a__b"}
        if stored_approval or precomputed_approval:
            assert result.tool_approvals == {approval_key: True}
        else:
            assert result.tool_approvals is None

    @pytest.mark.anyio
    async def test_records_registry_default_approvals_unless_overridden(
        self,
        registry_default_tools: None,
    ) -> None:
        """MCP definitions drop Tool.requires_approval, so the approval map
        must carry registry defaults; explicit rules still win."""
        result = await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=Role(type="service", service_id="tracecat-api"),
                tool_filters=ToolFilters(actions=list(REGISTRY_APPROVAL_DEFAULTS)),
                tool_approvals={"custom.waived": False},
            )
        )

        assert set(result.tool_definitions) == set(REGISTRY_APPROVAL_DEFAULTS)
        assert result.tool_approvals == {
            "custom.delete": True,
            "custom.waived": False,
        }

    @pytest.mark.anyio
    async def test_records_registry_default_approvals_per_agent_scope(
        self,
        registry_default_tools: None,
    ) -> None:
        """Durable compilation sees a child's registry default and rejects it."""
        result = await AgentActivities().build_agent_tool_definitions(
            BuildAgentToolDefsArgs(
                role=Role(type="service", service_id="tracecat-api"),
                scopes=[
                    BuildAgentScopeToolDefsArgs(
                        scope="root",
                        tool_filters=ToolFilters(actions=["custom.read"]),
                    ),
                    BuildAgentScopeToolDefsArgs(
                        scope="child",
                        tool_filters=ToolFilters(
                            actions=["custom.delete", "custom.read"]
                        ),
                    ),
                ],
            )
        )

        assert result.scopes["root"].tool_approvals is None
        assert result.scopes["child"].tool_approvals == {"custom.delete": True}
        assert has_manual_tool_approvals(result.scopes["child"].tool_approvals)

    @pytest.mark.anyio
    async def test_maps_tool_definition_errors_to_application_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_build_agent_tools(**_kwargs: Any) -> BuildToolsResult:
            raise AgentToolLimitExceededError(requested=101, limit=100)

        monkeypatch.setattr(
            agent_activities, "build_agent_tools", mock_build_agent_tools
        )

        args = BuildToolDefsArgs(
            role=Role(type="service", service_id="tracecat-api"),
            tool_filters=ToolFilters(actions=["core.http_request"]),
        )

        with pytest.raises(ApplicationError) as exc_info:
            await AgentActivities().build_tool_definitions(args)

        app_error = exc_info.value
        classification = extract_error_classification(app_error)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.USER
        assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
        assert app_error.non_retryable is True
        assert app_error.message == "Agent requests 101 tools; the limit is 100"

    @pytest.mark.anyio
    async def test_maps_builtin_sync_pending_to_application_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_registry(
            monkeypatch,
            lock=BuiltinRegistryHasNoSelectionError(
                "Builtin registry sync is still in progress. Please retry shortly.",
                detail={"origin": "tracecat_registry"},
            ),
        )

        with pytest.raises(ApplicationError) as exc_info:
            await AgentActivities().build_tool_definitions(
                BuildToolDefsArgs(
                    role=Role(type="service", service_id="tracecat-api"),
                    tool_filters=ToolFilters(actions=[]),
                )
            )

        app_error = exc_info.value
        assert app_error.type == "BuiltinRegistryHasNoSelectionError"
        assert app_error.non_retryable is False
        assert app_error.details[0] == {"origin": "tracecat_registry"}

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("error", "owner", "kind", "message_parts"),
        [
            pytest.param(
                EntitlementRequired("custom_registry"),
                RuntimeErrorOwner.USER,
                RuntimeErrorKind.TENANT_ENTITLEMENT_DENIED,
                (),
                id="entitlement-denied",
            ),
            pytest.param(
                RegistryLockInvalidDataError(
                    "Ambiguous action 'tools.example.get' found in multiple registries"
                ),
                RuntimeErrorOwner.PLATFORM,
                RuntimeErrorKind.REGISTRY_LOCK_INVALID_DATA,
                (),
                id="lock-invalid-data",
            ),
            pytest.param(
                RegistryLockAmbiguousActionError(
                    "tools.example.get",
                    ["tracecat_registry", "git+ssh://git@example.com/acme/reg.git"],
                ),
                RuntimeErrorOwner.USER,
                RuntimeErrorKind.REGISTRY_LOCK_ACTION_AMBIGUOUS,
                ("tools.example.get", "tracecat_registry"),
                id="ambiguous-action",
            ),
        ],
    )
    async def test_classifies_registry_lock_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        owner: RuntimeErrorOwner,
        kind: RuntimeErrorKind,
        message_parts: tuple[str, ...],
    ) -> None:
        _patch_registry(monkeypatch, lock=error)

        with pytest.raises(ApplicationError) as exc_info:
            await AgentActivities().build_tool_definitions(
                BuildToolDefsArgs(
                    role=Role(type="service", service_id="tracecat-api"),
                    tool_filters=ToolFilters(actions=[]),
                )
            )

        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is owner
        assert classification.kind is kind
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
        for part in message_parts:
            assert part in classification.message
        assert exc_info.value.non_retryable is True

    @pytest.mark.anyio
    @pytest.mark.parametrize("timed_out", [False, True])
    async def test_strict_mcp_discovery_failure_fails_scope_compilation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        timed_out: bool,
    ) -> None:
        discover_fail_flags: list[bool] = []

        async def mock_discover_user_mcp_tools(
            _configs: list[dict[str, Any]],
            *,
            fail_on_error: bool = False,
        ) -> dict[str, Any]:
            discover_fail_flags.append(fail_on_error)
            if timed_out:
                raise UserMCPDiscoveryTimeoutError("broken")
            raise UserMCPDiscoveryAuthError("broken")

        _patch_registry(monkeypatch)
        monkeypatch.setattr(
            user_client,
            "discover_user_mcp_tools",
            mock_discover_user_mcp_tools,
        )

        args = BuildToolDefsArgs(
            role=Role(type="service", service_id="tracecat-api"),
            tool_filters=ToolFilters(actions=[]),
            mcp_servers=[
                {
                    "type": "http",
                    "name": "broken",
                    "url": "https://broken.example/mcp",
                }
            ],
            fail_on_mcp_discovery_error=True,
        )

        with pytest.raises(ApplicationError) as exc_info:
            await AgentActivities().build_tool_definitions(args)

        assert discover_fail_flags == [True]
        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.USER
        assert classification.kind is (
            RuntimeErrorKind.AGENT_MCP_UNAVAILABLE
            if timed_out
            else RuntimeErrorKind.AGENT_MCP_AUTH_FAILED
        )
        assert exc_info.value.message == (
            "MCP server 'broken' timed out during tool discovery; "
            "check availability and retry"
            if timed_out
            else "MCP server 'broken' rejected the configured credentials; "
            "reconnect the integration"
        )
        assert classification.retry_disposition is (
            RetryDisposition.RETRYABLE if timed_out else RetryDisposition.NON_RETRYABLE
        )
        assert exc_info.value.non_retryable is (not timed_out)

    @pytest.mark.anyio
    async def test_mcp_tool_policy_filters_and_maps_approvals(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        integration_id = uuid.uuid4()

        async def mock_discover_user_mcp_tools(
            configs: list[dict[str, Any]],
            *,
            fail_on_error: bool = False,
        ) -> dict[str, MCPToolDefinition]:
            assert fail_on_error is False
            assert configs[0]["headers"] == {"authorization": "Bearer test-token"}
            return _mcp_tools("Jira", "getIssue", "deleteIssue", "updateIssue")

        preset_service = _patch_preset_service(
            monkeypatch,
            policies={
                integration_id: {
                    "getIssue": MCPToolSummary(
                        name="getIssue",
                        requires_approval=True,
                    ),
                    "deleteIssue": MCPToolSummary(
                        name="deleteIssue",
                        enabled=False,
                    ),
                    "updateIssue": MCPToolSummary(name="updateIssue"),
                }
            },
            secrets={"authorization": "Bearer test-token"},
            refs=[
                {
                    "type": "http",
                    "name": "Jira",
                    "url": "https://mcp.example.com/mcp",
                    "id": str(integration_id),
                }
            ],
        )
        lock_service = _patch_registry(monkeypatch)
        monkeypatch.setattr(
            user_client,
            "discover_user_mcp_tools",
            mock_discover_user_mcp_tools,
        )

        result = await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "Jira",
                        "url": "https://mcp.example.com/mcp",
                        "id": str(integration_id),
                        "tools": [
                            {
                                "name": "getIssue",
                                "description": "Get issue",
                            }
                        ],
                    }
                ],
            )
        )

        assert set(result.tool_definitions) == {"mcp__Jira__getIssue"}
        assert result.tool_approvals == {"mcp.Jira.getIssue": True}
        assert result.user_mcp_claims is not None
        assert result.user_mcp_claims[0].id == integration_id
        preset_service.resolve_mcp_integration_tool_policies.assert_awaited_once_with(
            [integration_id]
        )
        preset_service.resolve_mcp_integration_secrets.assert_awaited_once_with(
            integration_id
        )
        lock_service.resolve_lock_with_bindings.assert_awaited_once_with(set())

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "payload_tunnel_id", [None, "33333333-3333-3333-3333-333333333333"]
    )
    async def test_mcp_discovery_routes_by_integration_row_tunnel(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
        payload_tunnel_id: str | None,
    ) -> None:
        from tracecat.integrations import mcp_tunnel

        integration_id = uuid.uuid4()
        tunnel_id = str(uuid.uuid4())
        server: dict[str, Any] = {
            "type": "http",
            "name": "Private",
            "url": "https://mcp.internal.example/mcp",
            "id": str(integration_id),
        }
        _patch_preset_service(
            monkeypatch,
            policies={integration_id: {"search": MCPToolSummary(name="search")}},
            refs=[{**server, "tunnel_id": tunnel_id}],
        )
        _patch_registry(monkeypatch)
        resolve_proxy = AsyncMock(return_value="http://token@gateway:8080")
        monkeypatch.setattr(mcp_tunnel, "resolve_mcp_tunnel_proxy", resolve_proxy)
        discovered: list[dict[str, Any]] = []

        async def mock_discover_user_mcp_tools(
            configs: list[dict[str, Any]], *, fail_on_error: bool = False
        ) -> dict[str, MCPToolDefinition]:
            discovered.extend(configs)
            return _mcp_tools("Private", "search")

        monkeypatch.setattr(
            user_client, "discover_user_mcp_tools", mock_discover_user_mcp_tools
        )
        if payload_tunnel_id is not None:
            server["tunnel_id"] = payload_tunnel_id

        await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=[cast(Any, server)],
            )
        )

        assert resolve_proxy.await_args is not None
        assert resolve_proxy.await_args.kwargs["tunnel_id"] == uuid.UUID(tunnel_id)
        assert discovered[0]["tunnel_proxy"] == "http://token@gateway:8080"

    @pytest.mark.anyio
    @pytest.mark.parametrize("strict", [False, True])
    async def test_mcp_discovery_skips_server_without_integration_row(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
        strict: bool,
    ) -> None:
        integration_id = uuid.uuid4()
        server: dict[str, Any] = {
            "type": "http",
            "name": "Private",
            "url": "https://mcp.internal.example/mcp",
            "id": str(integration_id),
        }
        _patch_preset_service(monkeypatch, policies={}, refs=None)
        _patch_registry(monkeypatch)
        discover = AsyncMock(return_value={})
        monkeypatch.setattr(user_client, "discover_user_mcp_tools", discover)
        args = BuildToolDefsArgs(
            role=mock_role,
            tool_filters=ToolFilters(actions=[]),
            mcp_servers=[cast(Any, server)],
            fail_on_mcp_discovery_error=strict,
        )

        if strict:
            with pytest.raises(Exception, match="was not found in this workspace"):
                await AgentActivities().build_tool_definitions(args)
            discover.assert_not_awaited()
        else:
            await AgentActivities().build_tool_definitions(args)
            assert discover.await_args is not None
            assert discover.await_args.args[0] == []

    @pytest.mark.anyio
    async def test_mcp_discovery_drops_caller_supplied_tunnel_proxy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        """A proxy URL in the incoming config is never used: it would bypass
        the tunnel's ownership and allowed-target checks."""
        integration_id = uuid.uuid4()
        backed: dict[str, Any] = {
            "type": "http",
            "name": "Backed",
            "url": "https://mcp.example.com/mcp",
            "id": str(integration_id),
        }
        _patch_preset_service(monkeypatch, policies={}, refs=[dict(backed)])
        _patch_registry(monkeypatch)
        discover = AsyncMock(return_value={})
        monkeypatch.setattr(user_client, "discover_user_mcp_tools", discover)
        inline = {
            "type": "http",
            "name": "Inline",
            "url": "https://mcp.other.example/mcp",
        }
        attacker_proxy = "http://attacker.example:8080"

        await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=[
                    cast(Any, {**backed, "tunnel_proxy": attacker_proxy}),
                    cast(Any, {**inline, "tunnel_proxy": attacker_proxy}),
                ],
            )
        )

        assert discover.await_args is not None
        configs = discover.await_args.args[0]
        assert [cfg["name"] for cfg in configs] == ["Backed", "Inline"]
        assert all("tunnel_proxy" not in cfg for cfg in configs)

    @pytest.mark.anyio
    @pytest.mark.parametrize("strict", [False, True])
    async def test_mcp_discovery_refuses_inline_server_with_tunnel(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
        strict: bool,
    ) -> None:
        """An inline server can't carry its tunnel into tool calls, so it is
        never discovered rather than having its calls go direct."""
        from tracecat.integrations import mcp_tunnel

        _patch_registry(monkeypatch)
        resolve_proxy = AsyncMock(return_value="http://token@gateway:8080")
        monkeypatch.setattr(mcp_tunnel, "resolve_mcp_tunnel_proxy", resolve_proxy)
        discover = AsyncMock(return_value={})
        monkeypatch.setattr(user_client, "discover_user_mcp_tools", discover)
        args = BuildToolDefsArgs(
            role=mock_role,
            tool_filters=ToolFilters(actions=[]),
            mcp_servers=[
                cast(
                    Any,
                    {
                        "type": "http",
                        "name": "Inline",
                        "url": "https://mcp.internal.example/mcp",
                        "tunnel_id": str(uuid.uuid4()),
                    },
                )
            ],
            fail_on_mcp_discovery_error=strict,
        )

        if strict:
            with pytest.raises(Exception, match="only supported on MCP integrations"):
                await AgentActivities().build_tool_definitions(args)
            discover.assert_not_awaited()
        else:
            await AgentActivities().build_tool_definitions(args)
            assert discover.await_args is not None
            assert discover.await_args.args[0] == []
        resolve_proxy.assert_not_awaited()

    @pytest.mark.anyio
    async def test_mcp_discovery_uses_integration_row_address(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        """URL, timeout and tunnel all come from the integration row, so
        fresh credentials never reach an address from a stale payload."""
        from tracecat.integrations import mcp_tunnel

        integration_id = uuid.uuid4()
        tunnel_id = str(uuid.uuid4())
        _patch_preset_service(
            monkeypatch,
            policies={},
            secrets={"authorization": "Bearer fresh"},
            refs=[
                {
                    "type": "http",
                    "name": "Private",
                    "url": "https://mcp.current.example/mcp",
                    "id": str(integration_id),
                    "timeout": 30,
                    "tunnel_id": tunnel_id,
                }
            ],
        )
        _patch_registry(monkeypatch)
        monkeypatch.setattr(
            mcp_tunnel,
            "resolve_mcp_tunnel_proxy",
            AsyncMock(return_value="http://token@gateway:8080"),
        )
        discover = AsyncMock(return_value={})
        monkeypatch.setattr(user_client, "discover_user_mcp_tools", discover)

        await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=[
                    cast(
                        Any,
                        {
                            "type": "http",
                            "name": "Private",
                            "url": "https://mcp.former.example/mcp",
                            "transport": "sse",
                            "timeout": 5,
                            "id": str(integration_id),
                        },
                    )
                ],
            )
        )

        assert discover.await_args is not None
        [config] = discover.await_args.args[0]
        assert config["url"] == "https://mcp.current.example/mcp"
        assert config["timeout"] == 30
        assert "transport" not in config
        assert config["tunnel_id"] == tunnel_id
        assert config["headers"] == {"authorization": "Bearer fresh"}
        assert config["tunnel_proxy"] == "http://token@gateway:8080"

    @pytest.mark.anyio
    async def test_mcp_discovery_drops_payload_headers_for_integrations(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        """Stale headers in an integration-backed payload never reach the
        integration's current URL, even when it now resolves no credentials."""
        integration_id = uuid.uuid4()
        _patch_preset_service(
            monkeypatch,
            policies={},
            secrets={},
            refs=[
                {
                    "type": "http",
                    "name": "Private",
                    "url": "https://mcp.current.example/mcp",
                    "id": str(integration_id),
                }
            ],
        )
        _patch_registry(monkeypatch)
        discover = AsyncMock(return_value={})
        monkeypatch.setattr(user_client, "discover_user_mcp_tools", discover)

        await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                mcp_servers=[
                    cast(
                        Any,
                        {
                            "type": "http",
                            "name": "Private",
                            "url": "https://mcp.former.example/mcp",
                            "headers": {"authorization": "Bearer stale"},
                            "id": str(integration_id),
                        },
                    )
                ],
            )
        )

        assert discover.await_args is not None
        [config] = discover.await_args.args[0]
        assert config["url"] == "https://mcp.current.example/mcp"
        assert "headers" not in config

    @pytest.mark.anyio
    async def test_rejects_tool_tunnels_for_tools_the_agent_lacks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        _patch_registry(monkeypatch)

        with pytest.raises(ApplicationError, match="tools.sublime.serch"):
            await AgentActivities().build_tool_definitions(
                BuildToolDefsArgs(
                    role=mock_role,
                    tool_filters=ToolFilters(actions=[]),
                    tool_tunnels={"tools.sublime.serch": "onprem"},
                )
            )

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("unsupported_name", "approval_name"),
        [
            ("issue.get", "issue.delete"),
            ("x" * 65, "y" * 65),
            ("with space", "with space too"),
            ("x\n", "y\n"),
            ("", " "),
        ],
    )
    async def test_mcp_tool_with_unsupported_remote_name_always_dropped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
        unsupported_name: str,
        approval_name: str,
    ) -> None:
        """Drop unsupported remote names and their precomputed approval policy."""
        integration_id = uuid.uuid4()
        _patch_preset_service(
            monkeypatch,
            policies={
                integration_id: {
                    approval_name: MCPToolSummary(
                        name=approval_name,
                        requires_approval=True,
                    ),
                }
            },
        )
        _patch_registry(monkeypatch)
        monkeypatch.setattr(
            user_client,
            "discover_user_mcp_tools",
            AsyncMock(
                return_value=_mcp_tools(
                    "Jira", unsupported_name, approval_name, "x", "x" * 64
                )
            ),
        )

        result = await AgentActivities().build_tool_definitions(
            BuildToolDefsArgs(
                role=mock_role,
                tool_filters=ToolFilters(actions=[]),
                tool_approvals={f"mcp.Jira.{approval_name}": True},
                mcp_servers=[
                    {
                        "type": "http",
                        "name": "Jira",
                        "url": "https://mcp.example.com/mcp",
                        "id": str(integration_id),
                    }
                ],
            )
        )

        assert set(result.tool_definitions) == {
            "mcp__Jira__x",
            f"mcp__Jira__{'x' * 64}",
        }
        # Dropped tools must not leave behind approval entries.
        assert not (result.tool_approvals or {})

    @pytest.mark.anyio
    async def test_build_agent_tool_definitions_returns_partitioned_scopes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        build_calls: list[list[str] | None] = []

        async def mock_build_agent_tools(**kwargs: Any) -> BuildToolsResult:
            actions = kwargs.get("actions")
            build_calls.append(actions)
            action_name = actions[0] if actions else "core.default"
            return BuildToolsResult(
                tools=[
                    Tool(
                        name=action_name,
                        description=f"{action_name} tool",
                        parameters_json_schema={"type": "object"},
                    )
                ],
                collected_secrets=set(),
            )

        lock_service = _patch_registry(monkeypatch, build_tools=mock_build_agent_tools)
        lock_service.resolve_lock_with_bindings.side_effect = lambda actions: (
            RegistryLock(
                origins={"tracecat_registry": "test-version"},
                actions=dict.fromkeys(actions, "tracecat_registry"),
            )
        )

        result = await AgentActivities().build_agent_tool_definitions(
            BuildAgentToolDefsArgs(
                role=mock_role,
                scopes=[
                    BuildAgentScopeToolDefsArgs(
                        scope="root",
                        tool_filters=ToolFilters(actions=["core.root"]),
                    ),
                    BuildAgentScopeToolDefsArgs(
                        scope="analyst",
                        tool_filters=ToolFilters(actions=["core.child"]),
                    ),
                ],
            )
        )

        assert set(result.scopes) == {"root", "analyst"}
        assert set(result.scopes["root"].tool_definitions) == {"core.root"}
        assert set(result.scopes["analyst"].tool_definitions) == {"core.child"}
        assert build_calls == [["core.root"], ["core.child"]]

    @pytest.mark.anyio
    async def test_duplicate_compile_scope_is_platform_invariant(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_role: Role,
    ) -> None:
        activities = AgentActivities()
        monkeypatch.setattr(
            activities,
            "_build_scope_tool_definitions",
            AsyncMock(
                return_value=BuildToolDefsResult(
                    tool_definitions={},
                    registry_lock=RegistryLock(origins={}, actions={}),
                )
            ),
        )
        duplicate_scope = BuildAgentScopeToolDefsArgs(
            scope="root",
            tool_filters=ToolFilters(),
        )

        with pytest.raises(ApplicationError) as exc_info:
            await activities.build_agent_tool_definitions(
                BuildAgentToolDefsArgs(
                    role=mock_role,
                    scopes=[duplicate_scope, duplicate_scope],
                )
            )

        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED
        assert exc_info.value.non_retryable is True


@pytest.fixture
def session_service() -> Iterator[AsyncMock]:
    service = AsyncMock()
    with patch(
        "tracecat.agent.session.activities.AgentSessionService.with_session",
        return_value=_service_ctx(service),
    ):
        yield service


def _agent_session(**attrs: object) -> MagicMock:
    agent_session = MagicMock(backend_id="oss", harness_type="claude_code")
    agent_session.agents_binding = None
    agent_session.sdk_session_id = None
    agent_session.forked_from_session_id = None
    for name, value in attrs.items():
        setattr(agent_session, name, value)
    return agent_session


class TestCreateSessionActivity:
    """Tests for create_session_activity."""

    @pytest.mark.anyio
    async def test_backfills_empty_agents_binding_for_existing_session(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
    ):
        """A missing binding is persisted as an empty resolved binding."""
        agents_binding = ResolvedAgentsConfig()
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.AGENT_PRESET,
            entity_id=uuid.uuid4(),
            agents_binding=agents_binding,
        )

        mock_agent_session = _agent_session()
        session_service.get_or_create_session.return_value = (
            mock_agent_session,
            False,
        )
        session_service.session = MagicMock()
        session_service.session.commit = AsyncMock()

        result = await create_session_activity(input)

        assert result.success is True
        assert mock_agent_session.agents_binding == agents_binding.model_dump(
            mode="json"
        )
        session_service.session.add.assert_called_once_with(mock_agent_session)
        session_service.session.commit.assert_awaited_once()

    @pytest.mark.parametrize(
        (
            "incoming_agents_binding",
            "persisted_agents_binding",
            "sdk_session_id",
            "source_session_id",
            "expected_success",
            "expected_backfill",
        ),
        [
            pytest.param(
                ResolvedAgentsConfig.model_validate({"subagents": []}),
                None,
                None,
                None,
                True,
                True,
                id="fresh-null-backfills-resolved-agents",
            ),
            pytest.param(
                ResolvedAgentsConfig.model_validate({"subagents": []}),
                None,
                "sdk-session-1",
                None,
                True,
                False,
                id="resumable-null-remains-unset",
            ),
            pytest.param(
                ResolvedAgentsConfig.model_validate({"subagents": []}),
                None,
                None,
                uuid.UUID("00000000-0000-0000-0000-000000000001"),
                True,
                False,
                id="fork-null-remains-unset",
            ),
            pytest.param(
                ResolvedAgentsConfig(),
                {},
                "sdk-session-1",
                None,
                True,
                False,
                id="default-equivalent-jsonb",
            ),
            pytest.param(
                ResolvedAgentsConfig.model_validate({"subagents": []}),
                {},
                None,
                None,
                True,
                False,
                id="empty-bindings-are-equivalent",
            ),
            pytest.param(
                None,
                {"subagents": []},
                None,
                None,
                True,
                False,
                id="missing-incoming-matches-empty-binding",
            ),
            pytest.param(
                ResolvedAgentsConfig(
                    subagents=[
                        ResolvedAttachedSubagentRef(
                            preset="analyst",
                            preset_id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
                            preset_version_id=uuid.UUID(
                                "00000000-0000-0000-0000-000000000011"
                            ),
                        )
                    ]
                ),
                {"subagents": []},
                "sdk-session-1",
                None,
                False,
                False,
                id="attached-subagent-binding-mismatch",
            ),
        ],
    )
    @pytest.mark.anyio
    @pytest.mark.parametrize("enforce_session_agents_binding", [True, False])
    async def test_existing_session_agents_binding_must_match(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
        incoming_agents_binding: ResolvedAgentsConfig | None,
        persisted_agents_binding: dict[str, object] | None,
        sdk_session_id: str | None,
        source_session_id: uuid.UUID | None,
        expected_success: bool,
        expected_backfill: bool,
        enforce_session_agents_binding: bool,
    ):
        """Only legacy activity inputs enforce session-wide bindings."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.AGENT_PRESET,
            entity_id=uuid.uuid4(),
            agents_binding=incoming_agents_binding,
            enforce_session_agents_binding=enforce_session_agents_binding,
        )

        mock_agent_session = _agent_session(
            agents_binding=persisted_agents_binding,
            sdk_session_id=sdk_session_id,
            forked_from_session_id=source_session_id,
        )
        session_service.get_or_create_session.return_value = (
            mock_agent_session,
            False,
        )
        session_service.session = MagicMock()
        session_service.session.commit = AsyncMock()

        if expected_success or not enforce_session_agents_binding:
            result = await create_session_activity(input)
            assert result.success is True
            assert result.error is None
        else:
            with pytest.raises(ApplicationError) as exc_info:
                await create_session_activity(input)
            classification = extract_error_classification(exc_info.value)
            assert classification is not None
            assert classification.owner is RuntimeErrorOwner.USER
            assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
            assert exc_info.value.message == "Agent configuration is invalid"
            assert exc_info.value.non_retryable is True

        if expected_backfill and enforce_session_agents_binding:
            assert incoming_agents_binding is not None
            assert (
                mock_agent_session.agents_binding
                == incoming_agents_binding.model_dump(mode="json")
            )
            session_service.session.add.assert_called_once_with(mock_agent_session)
            session_service.session.commit.assert_awaited_once()
        else:
            session_service.session.add.assert_not_called()
            session_service.session.commit.assert_not_awaited()

    @pytest.mark.anyio
    async def test_uses_existing_session_when_required(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
    ):
        """Test that require_existing reuses a pre-existing session."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            require_existing=True,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )
        session_service.get_session.return_value = _agent_session()

        result = await create_session_activity(input)

        assert result.success is True
        session_service.get_session.assert_awaited_once_with(mock_session_id)
        session_service.get_or_create_session.assert_not_called()

    @pytest.mark.anyio
    async def test_fails_when_required_session_is_missing(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
    ):
        """Test that require_existing rejects unknown sessions."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            require_existing=True,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
        )
        session_service.get_session.return_value = None

        with pytest.raises(ApplicationError) as exc_info:
            await create_session_activity(input)

        classification = extract_error_classification(exc_info.value)
        assert classification is not None
        assert classification.owner is RuntimeErrorOwner.USER
        assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
        assert exc_info.value.message == "Agent configuration is invalid"
        assert str(mock_session_id) not in str(exc_info.value)
        assert exc_info.value.non_retryable is True
        session_service.get_session.assert_awaited_once_with(mock_session_id)
        session_service.get_or_create_session.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize("created", [True, False])
    async def test_auto_titles_only_new_session_with_initial_prompt(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
        created: bool,
    ):
        """An initial prompt auto-titles new sessions but not existing ones."""
        input = CreateSessionInput(
            role=mock_role,
            session_id=mock_session_id,
            entity_type=AgentSessionEntity.WORKFLOW,
            entity_id=uuid.uuid4(),
            initial_user_prompt="Investigate login failures",
        )
        mock_agent_session = _agent_session()
        session_service.get_or_create_session.return_value = (
            mock_agent_session,
            created,
        )
        session_service.auto_title_session_on_first_prompt = AsyncMock()

        result = await create_session_activity(input)

        assert result.success is True
        if created:
            session_service.auto_title_session_on_first_prompt.assert_awaited_once_with(
                mock_agent_session,
                "Investigate login failures",
                expected_title=mock_agent_session.title,
            )
        else:
            session_service.auto_title_session_on_first_prompt.assert_not_awaited()


class TestLoadSessionActivity:
    """Tests for load_session_activity."""

    @pytest.mark.anyio
    async def test_returns_not_found_for_missing_session(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
    ):
        """Test that loading a non-existent session returns found=False."""
        session_service.get_session.return_value = None

        result = await load_session_activity(
            LoadSessionInput(role=mock_role, session_id=mock_session_id)
        )

        assert isinstance(result, LoadSessionResult)
        assert result.found is False

    @pytest.mark.anyio
    async def test_loads_session_without_history(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
    ):
        """Test loading a session that has no history."""
        session_service.get_session.return_value = _agent_session()

        result = await load_session_activity(
            LoadSessionInput(role=mock_role, session_id=mock_session_id)
        )

        assert result.found is True
        assert result.sdk_session_id is None
        assert result.sdk_session_data is None
        assert result.is_fork is False
        assert result.agents_binding is None
        assert result.has_resume_state is False

    @pytest.mark.anyio
    async def test_loads_session_with_history(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
    ):
        """Test loading a session with existing history."""
        agents_binding = ResolvedAgentsConfig.model_validate({"subagents": []})
        session_service.get_session.return_value = _agent_session(
            agents_binding=agents_binding.model_dump(mode="json"),
            sdk_session_id="sdk-session-123",
        )

        result = await load_session_activity(
            LoadSessionInput(role=mock_role, session_id=mock_session_id)
        )

        assert result.found is True
        assert result.sdk_session_id == "sdk-session-123"
        assert result.sdk_session_data is None
        assert result.is_fork is False
        assert result.agents_binding == agents_binding
        assert result.has_resume_state is True

    @pytest.mark.anyio
    @pytest.mark.parametrize("source_sdk_id", ["source-sdk-session", None])
    async def test_loads_forked_source_session_metadata(
        self,
        session_service: AsyncMock,
        mock_role: Role,
        mock_session_id: uuid.UUID,
        source_sdk_id: str | None,
    ):
        """Forked first turns use captured native identity when one exists."""
        mock_source_session = MagicMock()
        mock_source_session.sdk_session_id = "later-sdk-session"
        session_service.get_session.side_effect = [
            _agent_session(
                forked_from_session_id=uuid.uuid4(),
                forked_from_sdk_session_id=source_sdk_id,
            ),
            mock_source_session,
        ]

        result = await load_session_activity(
            LoadSessionInput(role=mock_role, session_id=mock_session_id)
        )

        assert result.found is True
        assert result.sdk_session_id == source_sdk_id
        assert result.sdk_session_data is None
        assert result.is_fork is (source_sdk_id is not None)
        assert result.agents_binding is None
        assert result.has_resume_state is True


class TestRunAgentActivity:
    """Tests for run_agent_activity Temporal activity."""

    @pytest.fixture
    def mock_executor_input(
        self, executor_input: AgentExecutorInput
    ) -> AgentExecutorInput:
        return executor_input.model_copy(update={"curr_run_id": uuid.uuid4()})

    @pytest.mark.anyio
    async def test_absent_timeout_inherits_hardcoded_default(
        self,
        mock_executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An absent explicit timeout gets the default even under a raised ceiling."""
        expected_result = AgentExecutorResult(success=True)
        monkeypatch.setattr(
            "tracecat.agent.types.TRACECAT__AGENT_SANDBOX_TIMEOUT",
            21600,
        )

        with (
            patch("tracecat.agent.executor.activity.activity") as mock_activity,
            patch(
                "tracecat.agent.executor.activity.set_current_span_attributes"
            ) as set_span_attributes,
            patch(
                "tracecat.agent.executor.activity.SandboxedAgentExecutor"
            ) as mock_executor_cls,
        ):
            mock_activity.heartbeat = MagicMock()
            mock_executor = MagicMock()
            mock_executor.run = AsyncMock(return_value=expected_result)
            mock_executor_cls.return_value = mock_executor

            result = await run_agent_activity(mock_executor_input)

            assert result == expected_result
            mock_executor_cls.assert_called_once_with(
                input=mock_executor_input,
                timeout_seconds=7200,
            )
            set_span_attributes.assert_called_once_with(
                {
                    "tracecat.organization.id": str(
                        mock_executor_input.role.organization_id
                    ),
                    "tracecat.workspace.id": str(mock_executor_input.workspace_id),
                    "tracecat.agent.session.id": str(mock_executor_input.session_id),
                    "tracecat.agent.run.id": str(mock_executor_input.curr_run_id),
                    "temporal.activity.attempt": mock_activity.info.return_value.attempt,
                    "temporal.task_queue": mock_activity.info.return_value.task_queue,
                }
            )

    @pytest.mark.anyio
    async def test_emit_session_done_pushes_done_to_active_stream(
        self,
        mock_role: Role,
        mock_session_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace_id = mock_role.workspace_id or uuid.uuid4()
        active_stream_id = uuid.uuid4()
        stream = SimpleNamespace(done=AsyncMock())
        stream_new = AsyncMock(return_value=stream)
        monkeypatch.setattr(
            "tracecat_ee.agent.activities.AgentStream.new",
            stream_new,
        )

        await AgentActivities().emit_session_done(
            EmitSessionDoneInputs(
                role=mock_role,
                session_id=mock_session_id,
                workspace_id=workspace_id,
                active_stream_id=active_stream_id,
            )
        )

        stream_new.assert_awaited_once_with(
            session_id=mock_session_id,
            workspace_id=workspace_id,
            stream_id=active_stream_id,
        )
        stream.done.assert_awaited_once()

    @pytest.mark.anyio
    async def test_emit_session_error_leaves_done_to_workflow(
        self,
        mock_role: Role,
        mock_session_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace_id = mock_role.workspace_id or uuid.uuid4()
        active_stream_id = uuid.uuid4()
        agent_session = SimpleNamespace(last_error=None)
        service = SimpleNamespace(
            get_session=AsyncMock(return_value=agent_session),
            session=SimpleNamespace(add=MagicMock(), commit=AsyncMock()),
        )
        service_context = AsyncMock()
        service_context.__aenter__.return_value = service
        with_session = MagicMock(return_value=service_context)
        monkeypatch.setattr(
            "tracecat.agent.session.service.AgentSessionService.with_session",
            with_session,
        )
        stream = SimpleNamespace(error=AsyncMock(), done=AsyncMock())
        stream_new = AsyncMock(return_value=stream)
        monkeypatch.setattr(
            "tracecat_ee.agent.activities.AgentStream.new",
            stream_new,
        )

        await AgentActivities().emit_session_error(
            EmitSessionErrorInputs(
                role=mock_role,
                session_id=mock_session_id,
                workspace_id=workspace_id,
                active_stream_id=active_stream_id,
                message="runtime failed",
            )
        )

        assert agent_session.last_error == "runtime failed"
        stream.error.assert_awaited_once_with("runtime failed")
        stream.done.assert_not_awaited()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("result", "expected_outcome", "expected_status"),
        [
            (AgentExecutorResult(success=True), "success", StatusCode.UNSET),
            (
                AgentExecutorResult(success=True, approval_requested=True),
                "approval",
                StatusCode.UNSET,
            ),
            (
                AgentExecutorResult(success=False, cancelled=True),
                "cancelled",
                StatusCode.UNSET,
            ),
            (AgentExecutorResult(success=False), "failure", StatusCode.ERROR),
        ],
    )
    async def test_runtime_span_records_terminal_outcome(
        self,
        mock_executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        result: AgentExecutorResult,
        expected_outcome: str,
        expected_status: StatusCode,
    ) -> None:
        exporter = InMemorySpanExporter()
        shutdown_platform_tracing()
        monkeypatch.setattr(config, "TRACECAT__PLATFORM_OTEL_ENABLED", True)
        runtime = initialize_platform_tracing(
            "tracecat-agent-executor", exporter=exporter
        )
        assert runtime is not None
        try:
            with (
                patch("tracecat.agent.executor.activity.activity") as mock_activity,
                patch(
                    "tracecat.agent.executor.activity.SandboxedAgentExecutor"
                ) as mock_executor_cls,
            ):
                mock_activity.heartbeat = MagicMock()
                mock_executor = MagicMock()
                mock_executor.run = AsyncMock(return_value=result)
                mock_executor_cls.return_value = mock_executor

                await run_agent_activity(mock_executor_input)
                assert mock_activity.heartbeat.call_count >= 2
        finally:
            shutdown_platform_tracing()

        runtime_spans = [
            span
            for span in exporter.get_finished_spans()
            if span.name == "tracecat.agent.runtime"
        ]
        assert len(runtime_spans) == 1
        runtime_span = runtime_spans[0]
        assert runtime_span.attributes is not None
        assert runtime_span.attributes["tracecat.agent.outcome"] == expected_outcome
        assert runtime_span.status.status_code is expected_status


def _loopback_handler(executor: SandboxedAgentExecutor) -> LoopbackHandler:
    return LoopbackHandler(
        input=LoopbackInput(
            session_id=executor.input.session_id,
            workspace_id=executor.input.workspace_id,
        )
    )


async def _run_with_broker(
    executor: SandboxedAgentExecutor,
    handler: LoopbackHandler,
    job_dir: Path,
    *,
    init_payload: RuntimeInitPayload | None = None,
    otel_socket_path: Path | None = None,
) -> AgentExecutorResult:
    executor._job_dir = job_dir
    if executor._llm_proxy is None:
        executor._llm_proxy = AsyncMock()
    result = AgentExecutorResult(success=False, terminal_stream_error_emitted=False)
    await executor._run_with_broker(
        result=result,
        handler=handler,
        init_payload=init_payload or executor._build_runtime_init_payload(),
        socket_dir=job_dir / "sockets",
        llm_socket_path=job_dir / "sockets" / "llm.sock",
        artifact_working_set=None,
        otel_socket_path=otel_socket_path,
    )
    return result


class _FakeBroker:
    """Claude runtime broker whose turn blocks until cancelled by default."""

    def __init__(
        self,
        run_turn: Callable[[ClaudeTurnRequest, LoopbackHandler], Awaitable[None]]
        | None = None,
        *,
        cancel_turn: AsyncMock | None = None,
        interrupt_turn: Callable[[str, str], Awaitable[bool | None]] | None = None,
    ) -> None:
        self._run_turn = run_turn
        self.cancel_turn = cancel_turn or AsyncMock()
        if interrupt_turn is not None:
            self.interrupt_turn = interrupt_turn

    @asynccontextmanager
    async def session_turn_lease(self, _session_id: str) -> AsyncIterator[None]:
        yield

    async def run_turn_in_session_lease(
        self, request: ClaudeTurnRequest, handler: LoopbackHandler
    ) -> None:
        if self._run_turn is None:
            await asyncio.Event().wait()
        else:
            await self._run_turn(request, handler)


def _install_broker(monkeypatch: pytest.MonkeyPatch, broker: object) -> None:
    monkeypatch.setattr(
        "tracecat.agent.executor.activity.get_claude_runtime_broker",
        lambda: broker,
    )


class TestSandboxedAgentExecutorHelpers:
    """Tests for SandboxedAgentExecutor helper methods."""

    def test_apply_loopback_result_omits_output_for_approval_turn(
        self,
    ) -> None:
        result = AgentExecutorResult(success=False)
        loopback_result = LoopbackResult(
            success=True,
            approval_requested=True,
            output={"status": "waiting-for-approval"},
        )

        SandboxedAgentExecutor._apply_loopback_result(result, loopback_result)

        assert result.success is True
        assert result.approval_requested is True
        assert result.output is None

    def test_apply_loopback_result_keeps_output_for_final_turn(
        self,
    ) -> None:
        result = AgentExecutorResult(success=False)
        loopback_result = LoopbackResult(
            success=True,
            output={"status": "completed"},
        )

        SandboxedAgentExecutor._apply_loopback_result(result, loopback_result)

        assert result.success is True
        assert result.approval_requested is False
        assert result.output == {"status": "completed"}

    def test_apply_loopback_result_copies_terminal_stream_error_flag(
        self,
    ) -> None:
        result = AgentExecutorResult(
            success=False,
            terminal_stream_error_emitted=False,
        )
        loopback_result = LoopbackResult(
            success=False,
            error="runtime failed",
            terminal_stream_error_emitted=True,
        )

        SandboxedAgentExecutor._apply_loopback_result(result, loopback_result)

        assert result.success is False
        assert result.error == "runtime failed"
        assert result.terminal_stream_error_emitted is True

    async def _run_broker_leaf(
        self,
        *,
        executor: SandboxedAgentExecutor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        concurrent: bool = False,
        cancel_fails: bool = False,
    ) -> AgentExecutorResult:
        handler = _loopback_handler(executor)
        monkeypatch.setattr(
            handler,
            "emit_terminal_error",
            AsyncMock(return_value=True),
        )

        class LeafBroker(_FakeBroker):
            @asynccontextmanager
            async def session_turn_lease(self, _session_id: str) -> AsyncIterator[None]:
                if concurrent:
                    raise ConcurrentSessionTurnError("active turn")
                yield

        async def wait_for_cancel_signal(**_kwargs: Any) -> None:
            await asyncio.Event().wait()

        _install_broker(
            monkeypatch,
            LeafBroker(
                cancel_turn=AsyncMock(
                    side_effect=ConcurrentSessionTurnError("synthetic cleanup conflict")
                    if cancel_fails
                    else None
                )
            ),
        )
        monkeypatch.setattr(executor, "_watch_cancel_signal", wait_for_cancel_signal)
        return await _run_with_broker(executor, handler, tmp_path)

    @pytest.mark.anyio
    async def test_otel_receiver_start_failure_disables_telemetry_and_runs_turn(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An OTel bind failure clears relay state without failing the turn."""
        executor = SandboxedAgentExecutor(input=executor_input)
        executor._otel_receiver = cast(
            Any,
            SimpleNamespace(start=AsyncMock(side_effect=OSError("bind failed"))),
        )
        requests: list[ClaudeTurnRequest] = []

        async def run_turn(
            request: ClaudeTurnRequest, turn_handler: LoopbackHandler
        ) -> None:
            requests.append(request)
            turn_handler._result = LoopbackResult(success=True)

        broker = _FakeBroker(run_turn)
        _install_broker(monkeypatch, broker)
        monkeypatch.setattr(
            "tracecat.agent.executor.activity._agent_fs_persistence_enabled",
            lambda: False,
        )
        init_payload = executor._build_runtime_init_payload()
        init_payload.agent_otel_sandbox_env = {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}

        result = await _run_with_broker(
            executor,
            _loopback_handler(executor),
            tmp_path,
            init_payload=init_payload,
            otel_socket_path=tmp_path / "sockets" / "otel.sock",
        )

        assert result.success is True
        assert executor._otel_receiver is None
        assert init_payload.agent_otel_sandbox_env is None
        assert len(requests) == 1
        assert requests[0].otel_socket_path is None
        broker.cancel_turn.assert_not_awaited()

    @pytest.mark.anyio
    async def test_fatal_proxy_classification_reaches_executor_result(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        classification = agent_executor_timed_out(TimeoutError("secret"))
        capture = PlatformErrorCapture.for_error("a" * 32, classification)
        executor._fatal_error = LLMProxyError(
            message="raw gateway timeout",
            classification=classification,
            sentry_capture=capture,
            diagnostic=LLMErrorDiagnostics(
                route="managed", provider_configuration="custom"
            ),
        )
        executor._fatal_error_event.set()

        result = await self._run_broker_leaf(
            executor=executor,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )

        assert result.classification is not None
        assert result.classification.owner is RuntimeErrorOwner.PLATFORM
        assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT
        assert result.classification.retry_disposition is RetryDisposition.RETRYABLE
        assert "secret" not in result.classification.message
        assert result.sentry_capture == capture
        restored = AgentExecutorResult.model_validate_json(result.model_dump_json())
        assert restored.sentry_capture == capture
        assert result.diagnostic == executor._fatal_error.diagnostic
        assert "llm" not in result.model_dump(mode="json")["classification"]
        assert result.terminal_stream_error_emitted is True

    @pytest.mark.anyio
    async def test_fatal_proxy_error_survives_runtime_cleanup_send_done(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Runtime cleanup must preserve the proxy's authoritative failure."""
        executor = SandboxedAgentExecutor(input=executor_input)
        error = "provider request failed"
        classification = user_agent_execution_failed()
        executor._fatal_error = LLMProxyError(
            message=error,
            classification=classification,
        )
        executor._fatal_error_event.set()

        handler = _loopback_handler(executor)
        stream = SimpleNamespace(
            append=AsyncMock(),
            error=AsyncMock(),
            done=AsyncMock(),
        )
        handler._stream_sink = cast(Any, stream)
        capture = MagicMock()
        monkeypatch.setattr(
            "tracecat.agent.executor.loopback.capture_activity_failure",
            capture,
        )

        async def run_turn(
            _request: ClaudeTurnRequest, turn_handler: LoopbackHandler
        ) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await turn_handler.send_done()

        _install_broker(monkeypatch, _FakeBroker(run_turn))

        result = await _run_with_broker(executor, handler, tmp_path)

        assert result.success is False
        assert result.error == error
        assert result.classification == classification
        assert result.terminal_stream_error_emitted is True
        stream.error.assert_awaited_once_with(error)
        stream.done.assert_not_awaited()
        capture.assert_not_called()

    @pytest.mark.anyio
    async def test_cleanup_failure_drops_original_proxy_diagnostics(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        executor._fatal_error = LLMProxyError(
            message="synthetic proxy timeout",
            classification=agent_executor_timed_out(),
            diagnostic=LLMErrorDiagnostics(
                route="managed", provider_configuration="custom"
            ),
        )
        executor._fatal_error_event.set()

        result = await self._run_broker_leaf(
            executor=executor,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            cancel_fails=True,
        )

        assert result.classification is not None
        assert (
            result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED
        )
        assert result.diagnostic is None

    @pytest.mark.anyio
    async def test_elapsed_deadline_is_platform_timeout(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input, timeout_seconds=0)

        result = await self._run_broker_leaf(
            executor=executor,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
        )

        assert result.classification is not None
        assert result.classification.owner is RuntimeErrorOwner.PLATFORM
        assert result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT
        assert result.classification.retry_disposition is RetryDisposition.RETRYABLE
        assert result.terminal_stream_error_emitted is True

    @pytest.mark.anyio
    async def test_concurrent_turn_is_platform_unavailable(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)

        result = await self._run_broker_leaf(
            executor=executor,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            concurrent=True,
        )

        assert result.classification is not None
        assert result.classification.owner is RuntimeErrorOwner.PLATFORM
        assert (
            result.classification.kind is RuntimeErrorKind.AGENT_EXECUTOR_UNCLASSIFIED
        )
        assert result.classification.retry_disposition is RetryDisposition.RETRYABLE
        assert result.terminal_stream_error_emitted is True

    @pytest.mark.anyio
    async def test_cancel_task_with_timeout_does_not_wait_forever(self) -> None:
        release = asyncio.Event()

        async def stubborn_task() -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await release.wait()

        task = asyncio.create_task(stubborn_task())
        await asyncio.sleep(0)

        await _cancel_task_with_timeout(
            task,
            task_name="stubborn_task",
            timeout_seconds=0.01,
        )

        assert not task.done()
        release.set()
        await task

    @pytest.mark.anyio
    async def test_hydrates_session_history_for_runtime(
        self,
        executor_input: AgentExecutorInput,
    ) -> None:
        executor_input.sdk_session_id = "sdk-session-123"
        mock_history = MagicMock()
        mock_history.sdk_session_id = "sdk-session-123"
        mock_history.sdk_session_data = '{"type":"user"}\n'
        mock_history.is_fork = True
        mock_service = AsyncMock()
        mock_service.load_session_history.return_value = mock_history

        with patch(
            "tracecat.agent.executor.activity.AgentSessionService.with_session",
            return_value=_service_ctx(mock_service),
        ):
            hydrated = await _hydrate_sdk_session_history(executor_input)

        assert hydrated.sdk_session_id == "sdk-session-123"
        assert hydrated.sdk_session_data == '{"type":"user"}\n'
        assert hydrated.is_fork is True

    @pytest.mark.anyio
    @patch("tracecat.agent.executor.activity.AgentSessionService.with_session")
    async def test_preserves_legacy_session_data(
        self,
        mock_with_session,
        executor_input: AgentExecutorInput,
    ) -> None:
        executor_input.sdk_session_id = "legacy-sdk-session"
        executor_input.sdk_session_data = '{"type":"legacy"}\n'

        hydrated = await _hydrate_sdk_session_history(executor_input)

        assert hydrated is executor_input
        mock_with_session.assert_not_called()


class TestSandboxedAgentExecutorCancellation:
    """Tests for the graceful-cancellation branch in ``_run_with_broker``.

    A cancelled turn must not be reported as a successful turn when the
    interrupted runtime actually finished with an error - only the fact that
    the cancellation itself was processed should be signaled via
    ``result.cancelled``.
    """

    async def _run_cancelled_turn(
        self,
        executor: SandboxedAgentExecutor,
        *,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        loopback_result: LoopbackResult,
    ) -> AgentExecutorResult:
        """Drive ``_run_with_broker`` and cancel it before the turn completes."""
        handler = _loopback_handler(executor)
        interrupted = asyncio.Event()

        async def run_turn(_request: ClaudeTurnRequest, _handler: Any) -> None:
            # Blocks until interrupted, mirroring a live runtime turn that
            # keeps running until the graceful interrupt lands.
            await interrupted.wait()

        async def interrupt_turn(_session_id: str, _reason: str) -> None:
            # Simulate the loopback handler observing the interrupted
            # runtime's remaining events, including a terminal error, then
            # let the in-flight broker turn task finish.
            handler._result = loopback_result
            interrupted.set()

        _install_broker(
            monkeypatch, _FakeBroker(run_turn, interrupt_turn=interrupt_turn)
        )

        task = asyncio.create_task(_run_with_broker(executor, handler, tmp_path))
        # Yield control so the executor reaches the broker's blocking wait
        # before cancellation is delivered.
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        return await task

    @pytest.mark.anyio
    async def test_cancellation_after_turn_finishes_does_not_interrupt(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        interrupt = AsyncMock(side_effect=AssertionError("Turn already finished"))

        class FinishedTurnBroker(_FakeBroker):
            @asynccontextmanager
            async def session_turn_lease(self, _session_id: str) -> AsyncIterator[None]:
                yield
                # Deliver Temporal cancellation after the broker task finishes
                # but before the executor has returned from the turn lease.
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

        async def finish_turn(_request: ClaudeTurnRequest, _handler: Any) -> None:
            pass

        _install_broker(
            monkeypatch, FinishedTurnBroker(finish_turn, interrupt_turn=interrupt)
        )
        result = await _run_with_broker(executor, _loopback_handler(executor), tmp_path)
        interrupt.assert_not_awaited()
        assert result.cancelled is True
        assert result.success is True
        assert result.error is None
        assert result.sentry_capture is None

    @pytest.mark.anyio
    async def test_cancellation_preserves_error_when_interrupted_turn_failed(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A cancelled turn that ended with a runtime error must stay success=False."""
        executor = SandboxedAgentExecutor(input=executor_input)

        result = await self._run_cancelled_turn(
            executor,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            loopback_result=LoopbackResult(
                success=False,
                error="runtime crashed",
                classification=user_agent_execution_failed(),
            ),
        )

        assert result.cancelled is True
        assert result.cancelled_reason == "user_cancel"
        assert result.error == "runtime crashed"
        assert result.classification == user_agent_execution_failed()
        assert result.success is False

    @pytest.mark.anyio
    async def test_cancellation_reports_success_when_interrupted_turn_clean(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A cancelled turn with no runtime error is still a clean cancellation."""
        executor = SandboxedAgentExecutor(input=executor_input)

        result = await self._run_cancelled_turn(
            executor,
            monkeypatch=monkeypatch,
            tmp_path=tmp_path,
            loopback_result=LoopbackResult(success=False, error=None),
        )

        assert result.cancelled is True
        assert result.cancelled_reason == "user_cancel"
        assert result.error is None
        assert result.success is True

    @pytest.mark.anyio
    @pytest.mark.parametrize("interrupt_delivered", [False, True])
    @pytest.mark.parametrize("runtime_failed", [False, True])
    async def test_cancellation_cleanup_timeout_preserves_outcome(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        interrupt_delivered: bool,
        runtime_failed: bool,
    ) -> None:
        """A stuck shutdown must not escape as an executor-unavailable error."""
        executor = SandboxedAgentExecutor(input=executor_input)
        handler = _loopback_handler(executor)
        if runtime_failed:
            handler._result = LoopbackResult(
                success=False,
                error="runtime crashed",
                classification=user_agent_execution_failed(),
            )

        started = asyncio.Event()
        stopped = asyncio.Event()
        hard_cancel = AsyncMock()

        async def run_turn(_request: ClaudeTurnRequest, _handler: Any) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def interrupt_turn(_session_id: str, _reason: str) -> bool:
            # Cover both startup that cannot accept an interrupt and a
            # runtime that accepts it but never finishes shutting down.
            return interrupt_delivered

        _install_broker(
            monkeypatch,
            _FakeBroker(
                run_turn, cancel_turn=hard_cancel, interrupt_turn=interrupt_turn
            ),
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.GRACEFUL_CANCEL_TIMEOUT_SECONDS", 0.01
        )
        capture_failure = MagicMock()
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.capture_activity_failure",
            capture_failure,
        )
        task = asyncio.create_task(_run_with_broker(executor, handler, tmp_path))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        result = await asyncio.wait_for(task, timeout=5)

        hard_cancel.assert_awaited_once_with(str(executor_input.session_id))
        assert stopped.is_set()
        assert result.cancelled is True
        assert result.cancelled_reason == "user_cancel"
        assert result.success is not runtime_failed
        assert result.error == ("runtime crashed" if runtime_failed else None)
        assert result.classification == (
            user_agent_execution_failed() if runtime_failed else None
        )
        assert result.sentry_capture is None
        capture_failure.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
    async def test_cancel_signal_does_not_interrupt_finished_task(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        outcome: str,
    ) -> None:
        executor_input.curr_run_id = uuid.uuid4()
        executor = SandboxedAgentExecutor(input=executor_input)
        broker = MagicMock()
        broker.interrupt_turn = AsyncMock()
        broker.cancel_turn = AsyncMock()
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.read_turn_cancel_signal",
            AsyncMock(return_value="user_cancel"),
        )

        async def finished_turn() -> None:
            if outcome == "failed":
                raise RuntimeError("synthetic runtime failure")
            if outcome == "cancelled":
                raise asyncio.CancelledError

        broker_task = asyncio.create_task(finished_turn())
        await asyncio.wait({broker_task})
        await executor._watch_cancel_signal(
            broker=broker, handler=_loopback_handler(executor), broker_task=broker_task
        )
        broker.interrupt_turn.assert_not_awaited()
        broker.cancel_turn.assert_not_awaited()
        # The watcher does not consume or replace the main wait loop's outcome.
        if outcome == "failed":
            with pytest.raises(RuntimeError, match="synthetic runtime failure"):
                await broker_task
        elif outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await broker_task
        else:
            await broker_task

    @pytest.mark.anyio
    async def test_cancel_signal_interrupts_turn_without_task_cancellation(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Regression: user stop must not depend on Temporal cancel delivery.

        Temporal delivers activity cancellation via throttled heartbeat RPCs,
        so a live turn can finish before ``asyncio.CancelledError`` is ever
        raised in the activity. Here a cancel signal is written while the
        activity keeps running normally (its task is never cancelled); the
        executor must observe the signal out-of-band, interrupt the broker
        turn, and report the turn as cancelled.
        """
        executor_input.curr_run_id = uuid.uuid4()
        executor = SandboxedAgentExecutor(input=executor_input)
        handler = _loopback_handler(executor)

        cancel_signalled = asyncio.Event()
        interrupted = asyncio.Event()
        interrupt_reasons: list[str] = []

        async def fake_read_turn_cancel_signal(run_id: str) -> str | None:
            assert run_id == str(executor_input.curr_run_id)
            return "user_cancel" if cancel_signalled.is_set() else None

        monkeypatch.setattr(
            "tracecat.agent.executor.activity.read_turn_cancel_signal",
            fake_read_turn_cancel_signal,
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.TURN_CANCEL_POLL_INTERVAL_SECONDS",
            0.01,
        )

        async def run_turn(_request: ClaudeTurnRequest, _handler: Any) -> None:
            # Completes normally once interrupted - the activity task
            # itself is never cancelled in this scenario.
            await interrupted.wait()

        async def interrupt_turn(_session_id: str, reason: str) -> bool:
            interrupt_reasons.append(reason)
            # Simulate the interrupted runtime finishing cleanly.
            handler._result.success = True
            interrupted.set()
            return True

        broker = _FakeBroker(run_turn, interrupt_turn=interrupt_turn)
        _install_broker(monkeypatch, broker)

        task = asyncio.create_task(_run_with_broker(executor, handler, tmp_path))
        # Let the executor reach the broker's blocking wait, then signal the
        # cancel out-of-band instead of cancelling the task.
        for _ in range(5):
            await asyncio.sleep(0)
        cancel_signalled.set()
        result = await asyncio.wait_for(task, timeout=5)

        assert interrupt_reasons == ["user_cancel"]
        assert result.cancelled is True
        assert result.cancelled_reason == "user_cancel"
        assert result.success is True
        assert result.error is None
        broker.cancel_turn.assert_not_awaited()


def _stub_run_setup(
    monkeypatch: pytest.MonkeyPatch,
    executor: SandboxedAgentExecutor,
    *,
    create_job_directory: Callable[[], Awaitable[Path]],
    proxy: AsyncMock | None = None,
    run_with_broker: Callable[..., Awaitable[None]] | None = None,
    stub_cleanup: bool = True,
) -> None:
    monkeypatch.setattr(executor, "_create_job_directory", create_job_directory)
    monkeypatch.setattr(
        executor,
        "_create_llm_socket_proxy",
        AsyncMock(return_value=proxy or AsyncMock()),
    )
    monkeypatch.setattr(
        executor,
        "_load_artifact_working_set",
        AsyncMock(return_value=None),
    )
    if run_with_broker is not None:
        monkeypatch.setattr(executor, "_run_with_broker", run_with_broker)
    if stub_cleanup:
        monkeypatch.setattr(executor, "_cleanup", AsyncMock())


def _capture_run_with_broker() -> tuple[
    Callable[..., Awaitable[None]], list[RuntimeInitPayload], list[Path | None]
]:
    payloads: list[RuntimeInitPayload] = []
    socket_paths: list[Path | None] = []

    async def fake_run_with_broker(*_args: object, **kwargs: object) -> None:
        payloads.append(cast(RuntimeInitPayload, kwargs["init_payload"]))
        socket_paths.append(cast(Path | None, kwargs["otel_socket_path"]))
        cast(AgentExecutorResult, kwargs["result"]).success = True

    return fake_run_with_broker, payloads, socket_paths


def _patch_tenant_otel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tracecat.agent.executor.activity.load_org_agent_otel_inputs",
        AsyncMock(
            return_value=AgentOtelInputs(
                config=AgentOtelConfig(
                    enabled=True,
                    endpoint=HttpUrl("https://collector.example.com"),
                    metrics_enabled=False,
                    logs_enabled=False,
                    traces_enabled=True,
                ),
                headers={"Authorization": "Bearer synthetic-tenant-token"},
            )
        ),
    )
    monkeypatch.setattr(
        "tracecat.agent.executor.activity.platform_otel_collector_env",
        lambda: {},
    )


class TestSandboxedAgentExecutorFilesystemPersistence:
    """Tests for feature-flagged agent work-dir hydration and snapshotting."""

    def _patch_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        executor: SandboxedAgentExecutor,
        tmp_path: Path,
        *,
        broker: object,
        fs_enabled: bool,
        hydrate: Callable[..., Awaitable[None]],
        persist: Callable[..., Awaitable[None]],
        work_dir: Path | None = None,
    ) -> None:
        job_dir = tmp_path / "job"

        async def create_job_directory() -> Path:
            (job_dir / "sockets").mkdir(parents=True)
            return job_dir

        _stub_run_setup(
            monkeypatch, executor, create_job_directory=create_job_directory
        )
        _install_broker(monkeypatch, broker)
        monkeypatch.setattr(
            "tracecat.agent.executor.activity._agent_fs_persistence_enabled",
            lambda: fs_enabled,
        )
        if work_dir is not None:
            monkeypatch.setattr(
                "tracecat.agent.executor.activity.build_agent_sandbox_path_mapping",
                lambda **_kwargs: SimpleNamespace(host_work_dir=work_dir),
            )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.hydrate_agent_work_dir", hydrate
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.persist_agent_work_dir", persist
        )

    @pytest.mark.anyio
    async def test_run_skips_filesystem_persistence_when_feature_disabled(
        self,
        executor_input: AgentExecutorInput,
        mock_session_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        events: list[str] = []

        class SessionBroker(_FakeBroker):
            @asynccontextmanager
            async def session_turn_lease(self, session_id: str) -> AsyncIterator[None]:
                assert session_id == str(mock_session_id)
                yield

        async def run_turn(
            request: ClaudeTurnRequest, handler: LoopbackHandler
        ) -> None:
            assert request.hydrate_work_dir is None
            events.append("broker")
            handler._result = LoopbackResult(success=True)

        async def fail_hydrate_agent_work_dir(**_kwargs: Any) -> None:
            raise AssertionError("hydrate should not run when feature is disabled")

        async def fail_persist_agent_work_dir(**_kwargs: Any) -> None:
            raise AssertionError("snapshot should not run when feature is disabled")

        broker = SessionBroker(run_turn)
        self._patch_run(
            monkeypatch,
            executor,
            tmp_path,
            broker=broker,
            fs_enabled=False,
            hydrate=fail_hydrate_agent_work_dir,
            persist=fail_persist_agent_work_dir,
        )

        result = await executor.run()

        assert result.success is True
        assert events == ["broker"]
        broker.cancel_turn.assert_not_awaited()

    @pytest.mark.anyio
    async def test_run_hydrates_and_snapshots_when_feature_enabled(
        self,
        executor_input: AgentExecutorInput,
        mock_session_id: uuid.UUID,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        work_dir = tmp_path / "work"
        events: list[str] = []
        leased = False

        class LeaseTrackingBroker(_FakeBroker):
            @asynccontextmanager
            async def session_turn_lease(self, session_id: str) -> AsyncIterator[None]:
                nonlocal leased
                assert session_id == str(mock_session_id)
                leased = True
                try:
                    yield
                finally:
                    leased = False

        async def run_turn(
            request: ClaudeTurnRequest, handler: LoopbackHandler
        ) -> None:
            assert leased is True
            assert request.hydrate_work_dir is not None
            await request.hydrate_work_dir(work_dir)
            events.append("broker")
            handler._result = LoopbackResult(success=True)

        async def fake_hydrate_agent_work_dir(**kwargs: Any) -> None:
            events.append("hydrate")
            assert kwargs["session_id"] == mock_session_id
            assert kwargs["work_dir"] == work_dir

        async def fake_persist_agent_work_dir(**kwargs: Any) -> None:
            events.append("snapshot")
            assert leased is True
            assert kwargs["session_id"] == mock_session_id
            assert kwargs["workspace_id"] == executor_input.workspace_id
            assert kwargs["work_dir"] == work_dir

        broker = LeaseTrackingBroker(run_turn)
        self._patch_run(
            monkeypatch,
            executor,
            tmp_path,
            broker=broker,
            fs_enabled=True,
            hydrate=fake_hydrate_agent_work_dir,
            persist=fake_persist_agent_work_dir,
            work_dir=work_dir,
        )

        result = await executor.run()

        assert result.success is True
        assert events == ["hydrate", "broker", "snapshot"]
        broker.cancel_turn.assert_not_awaited()

    @pytest.mark.anyio
    async def test_run_succeeds_when_snapshot_after_success_fails(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        work_dir = tmp_path / "work"
        events: list[str] = []

        async def run_turn(
            request: ClaudeTurnRequest, handler: LoopbackHandler
        ) -> None:
            assert request.hydrate_work_dir is not None
            await request.hydrate_work_dir(work_dir)
            events.append("broker")
            handler._result = LoopbackResult(success=True)

        async def fake_hydrate_agent_work_dir(**_kwargs: Any) -> None:
            events.append("hydrate")

        async def fail_persist_agent_work_dir(**_kwargs: Any) -> None:
            events.append("snapshot")
            raise RuntimeError("snapshot store unavailable")

        broker = _FakeBroker(run_turn)
        self._patch_run(
            monkeypatch,
            executor,
            tmp_path,
            broker=broker,
            fs_enabled=True,
            hydrate=fake_hydrate_agent_work_dir,
            persist=fail_persist_agent_work_dir,
            work_dir=work_dir,
        )

        result = await executor.run()

        # Fail-open: a snapshot persistence failure is logged but the
        # already-successful turn is not failed.
        assert result.success is True
        assert result.error is None
        assert events == ["hydrate", "broker", "snapshot"]
        broker.cancel_turn.assert_not_awaited()

    @pytest.mark.anyio
    async def test_run_skips_snapshot_after_hydrate_fallback(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = SandboxedAgentExecutor(input=executor_input)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        (work_dir / "stale.txt").write_text("stale")
        events: list[str] = []

        async def run_turn(
            request: ClaudeTurnRequest, handler: LoopbackHandler
        ) -> None:
            assert request.hydrate_work_dir is not None
            await request.hydrate_work_dir(work_dir)
            events.append("broker")
            handler._result = LoopbackResult(success=True)

        async def fail_hydrate_agent_work_dir(**_kwargs: Any) -> None:
            events.append("hydrate")
            raise RuntimeError("snapshot missing")

        async def fail_persist_agent_work_dir(**_kwargs: Any) -> None:
            raise AssertionError("hydrate fallback should not be persisted")

        broker = _FakeBroker(run_turn)
        self._patch_run(
            monkeypatch,
            executor,
            tmp_path,
            broker=broker,
            fs_enabled=True,
            hydrate=fail_hydrate_agent_work_dir,
            persist=fail_persist_agent_work_dir,
        )

        result = await executor.run()

        assert result.success is True
        assert events == ["hydrate", "broker"]
        assert work_dir.is_dir()
        assert list(work_dir.iterdir()) == []
        broker.cancel_turn.assert_not_awaited()

    def test_build_sandbox_env_injects_receiver_bearer_jwt(self) -> None:
        """The host injects OTEL_EXPORTER_OTLP_HEADERS so Claude's exporter
        attaches the receiver JWT for the OtelSocketReceiver to verify."""
        resolved = ResolvedAgentOtelConfig(
            enabled=True,
            sandbox_env={
                "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "placeholder-removed-by-shim",
            },
        )
        env = SandboxedAgentExecutor._build_sandbox_env(
            resolved, otel_auth_token="receiver-jwt"
        )

        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env
        assert env["OTEL_EXPORTER_OTLP_HEADERS"] == "Authorization=Bearer receiver-jwt"
        assert env["OTEL_LOGS_EXPORTER"] == "otlp"

    def test_platform_only_sandbox_env_enables_content_free_native_traces(
        self,
    ) -> None:
        env = SandboxedAgentExecutor._build_sandbox_env(
            ResolvedAgentOtelConfig(enabled=False),
            otel_auth_token="receiver-jwt",
            platform_tracing=True,
        )

        assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
        assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"
        assert env["OTEL_TRACES_EXPORTER"] == "otlp"
        assert env["OTEL_METRICS_EXPORTER"] == "none"
        assert env["OTEL_LOGS_EXPORTER"] == "none"
        assert env["OTEL_LOG_USER_PROMPTS"] == "0"
        assert env["OTEL_LOG_TOOL_DETAILS"] == "0"
        assert env["OTEL_LOG_TOOL_CONTENT"] == "0"

    @pytest.mark.anyio
    async def test_run_builds_tenant_otel_receiver_from_org_settings(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Org settings become a tenant route and receiver-backed sandbox env."""
        executor = SandboxedAgentExecutor(
            input=executor_input.model_copy(
                update={"agent_otel_auth_token": "receiver-jwt"}
            )
        )
        job_dir = tmp_path / "job"
        run_with_broker, captured_payloads, captured_socket_paths = (
            _capture_run_with_broker()
        )
        _patch_tenant_otel(monkeypatch)
        _stub_run_setup(
            monkeypatch,
            executor,
            create_job_directory=AsyncMock(return_value=job_dir),
            run_with_broker=run_with_broker,
        )
        pool = cast(OtelDeliveryPool, object())
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.get_otel_delivery_pool", lambda: pool
        )

        result = await executor.run()

        assert result.success is True
        assert executor._otel_receiver is not None
        assert executor._otel_receiver._pool is pool
        assert executor._otel_receiver._plan.tenant is not None
        assert executor._otel_receiver._plan.platform_endpoint is None
        assert captured_socket_paths == [job_dir / "sockets" / "otel.sock"]
        assert len(captured_payloads) == 1
        sandbox_env = captured_payloads[0].agent_otel_sandbox_env
        assert sandbox_env is not None
        assert sandbox_env["OTEL_EXPORTER_OTLP_HEADERS"] == (
            "Authorization=Bearer receiver-jwt"
        )
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in sandbox_env

    @pytest.mark.parametrize("guard", ["missing-token", "missing-organization"])
    @pytest.mark.anyio
    async def test_run_skips_otel_receiver_when_required_context_is_missing(
        self,
        executor_input: AgentExecutorInput,
        mock_role: Role,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        guard: str,
    ) -> None:
        """Missing receiver auth or organization context disables telemetry."""
        update: dict[str, object] = {"agent_otel_auth_token": "receiver-jwt"}
        if guard == "missing-token":
            update["agent_otel_auth_token"] = None
        else:
            update["role"] = mock_role.model_copy(update={"organization_id": None})
        executor = SandboxedAgentExecutor(
            input=executor_input.model_copy(update=update)
        )
        run_with_broker, captured_payloads, captured_socket_paths = (
            _capture_run_with_broker()
        )
        warning = MagicMock()
        _patch_tenant_otel(monkeypatch)
        monkeypatch.setattr("tracecat.agent.executor.activity.logger.warning", warning)
        _stub_run_setup(
            monkeypatch,
            executor,
            create_job_directory=AsyncMock(return_value=tmp_path / "job"),
            run_with_broker=run_with_broker,
        )

        result = await executor.run()

        assert result.success is True
        assert executor._otel_receiver is None
        assert captured_socket_paths == [None]
        assert len(captured_payloads) == 1
        assert captured_payloads[0].agent_otel_sandbox_env is None
        warning.assert_called_once()

    @pytest.mark.anyio
    async def test_resolve_agent_otel_config_fails_open(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Organization OTel lookup failures return a disabled configuration."""
        warning = MagicMock()
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.load_org_agent_otel_inputs",
            AsyncMock(side_effect=OSError("settings unavailable")),
        )
        monkeypatch.setattr("tracecat.agent.executor.activity.logger.warning", warning)
        executor = SandboxedAgentExecutor(input=executor_input)

        resolved = await executor._resolve_agent_otel_config()

        assert resolved == ResolvedAgentOtelConfig(enabled=False)
        warning.assert_called_once()

    def test_platform_trace_parent_carries_agent_correlation(
        self,
        executor_input: AgentExecutorInput,
        mock_session_id: uuid.UUID,
    ) -> None:
        run_id = uuid.uuid4()
        executor = SandboxedAgentExecutor(
            input=executor_input.model_copy(update={"curr_run_id": run_id})
        )
        span_context = SpanContext(
            trace_id=0x1234567890ABCDEF1234567890ABCDEF,
            span_id=0x1234567890ABCDEF,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
        token = otel_context.attach(
            trace.set_span_in_context(NonRecordingSpan(span_context))
        )
        try:
            parent = executor._platform_trace_parent()
        finally:
            otel_context.detach(token)

        assert parent is not None
        assert parent.trace_id == span_context.trace_id.to_bytes(16, "big")
        assert parent.span_id == span_context.span_id.to_bytes(8, "big")
        assert parent.resource_attributes["tracecat.agent.session.id"] == str(
            mock_session_id
        )
        assert parent.resource_attributes["tracecat.agent.run.id"] == str(run_id)


class TestSandboxedAgentExecutorSkillCaching:
    """Tests for cached skill staging in the sandbox executor."""

    @pytest.mark.anyio
    async def test_ensure_cached_skill_dir_downloads_files_concurrently(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Cache population uses bounded concurrent blob downloads."""

        mock_executor = SandboxedAgentExecutor(input=executor_input)
        manifest_sha256 = "manifest-sha"
        skill_version_id = uuid.uuid4()
        version_files = [
            (
                "skill-a/SKILL.md",
                MagicMock(
                    key="k1",
                    bucket="skills",
                    sha256="s1",
                    size_bytes=10,
                ),
            ),
            (
                "skill-a/helper.py",
                MagicMock(
                    key="k2",
                    bucket="skills",
                    sha256="s2",
                    size_bytes=10,
                ),
            ),
            (
                "skill-a/README.md",
                MagicMock(
                    key="k3",
                    bucket="skills",
                    sha256="s3",
                    size_bytes=10,
                ),
            ),
        ]
        mock_service = AsyncMock()
        mock_service.get_version_file_materialization.return_value = version_files

        started_two_downloads = asyncio.Event()
        release_downloads = asyncio.Event()
        max_in_flight = 0
        in_flight = 0

        async def fake_download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            expected_sha256: str | None = None,
            max_bytes: int | None = None,
        ) -> int:
            del key, bucket, expected_sha256, max_bytes
            nonlocal in_flight, max_in_flight
            output_path.parent.mkdir(parents=True, exist_ok=True)
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            if in_flight >= 2:
                started_two_downloads.set()
            await release_downloads.wait()
            output_path.write_text("ok")
            in_flight -= 1
            return 2

        monkeypatch.setattr(
            "tracecat.agent.executor.activity.TRACECAT__AGENT_SKILL_CACHE_DIR",
            str(tmp_path),
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.TRACECAT__AGENT_SKILL_CACHE_MAX_CONCURRENT_DOWNLOADS",
            2,
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.blob.download_file_to_path",
            fake_download_file_to_path,
        )

        task = asyncio.create_task(
            mock_executor._ensure_cached_skill_dir(
                service=mock_service,
                manifest_sha256=manifest_sha256,
                skill_version_id=skill_version_id,
            )
        )
        await asyncio.wait_for(started_two_downloads.wait(), timeout=1)
        assert max_in_flight == 2
        release_downloads.set()
        cache_dir = await task

        assert cache_dir == tmp_path / manifest_sha256
        assert (cache_dir / "skill-a" / "SKILL.md").read_text() == "ok"
        assert (cache_dir / "skill-a" / "helper.py").read_text() == "ok"
        assert (cache_dir / "skill-a" / "README.md").read_text() == "ok"

    @pytest.mark.anyio
    async def test_ensure_cached_skill_dir_clamps_non_positive_download_limit(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Skill caching should still complete when concurrency config is invalid."""

        mock_executor = SandboxedAgentExecutor(input=executor_input)
        manifest_sha256 = "manifest-sha"
        skill_version_id = uuid.uuid4()
        mock_service = AsyncMock()
        mock_service.get_version_file_materialization.return_value = [
            (
                "skill-a/SKILL.md",
                MagicMock(
                    key="k1",
                    bucket="skills",
                    sha256="s1",
                    size_bytes=10,
                ),
            )
        ]

        async def fake_download_file_to_path(
            *,
            key: str,
            bucket: str,
            output_path: Path,
            expected_sha256: str | None = None,
            max_bytes: int | None = None,
        ) -> int:
            del key, bucket, expected_sha256, max_bytes
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("ok")
            return 2

        monkeypatch.setattr(
            "tracecat.agent.executor.activity.TRACECAT__AGENT_SKILL_CACHE_DIR",
            str(tmp_path),
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.TRACECAT__AGENT_SKILL_CACHE_MAX_CONCURRENT_DOWNLOADS",
            0,
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.blob.download_file_to_path",
            fake_download_file_to_path,
        )

        cache_dir = await asyncio.wait_for(
            mock_executor._ensure_cached_skill_dir(
                service=mock_service,
                manifest_sha256=manifest_sha256,
                skill_version_id=skill_version_id,
            ),
            timeout=1,
        )

        assert cache_dir == tmp_path / manifest_sha256
        assert (cache_dir / "skill-a" / "SKILL.md").read_text() == "ok"

    @pytest.mark.anyio
    async def test_create_job_directory_cleans_up_when_skill_staging_fails(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Failed skill staging removes the temp job directory before re-raising."""

        mock_executor = SandboxedAgentExecutor(input=executor_input)
        job_dir = tmp_path / "agent-job-test"

        async def fake_stage_resolved_skills(_skills_dir: Path) -> None:
            raise RuntimeError("staging failed")

        def fake_mkdtemp(*, prefix: str, dir: Path) -> str:
            del prefix, dir
            job_dir.mkdir()
            return str(job_dir)

        monkeypatch.setattr(
            mock_executor, "_stage_resolved_skills", fake_stage_resolved_skills
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.tempfile.mkdtemp", fake_mkdtemp
        )

        with pytest.raises(RuntimeError, match="staging failed"):
            await mock_executor._create_job_directory()

        assert not job_dir.exists()

    @pytest.mark.parametrize(
        "setup_error",
        [
            pytest.param(RuntimeError("staging failed"), id="ordinary-error"),
            pytest.param(
                asyncio.CancelledError("staging cancelled"),
                id="cancellation",
            ),
        ],
    )
    @pytest.mark.anyio
    async def test_create_job_directory_preserves_setup_failure_when_cleanup_fails(
        self,
        executor_input: AgentExecutorInput,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        setup_error: BaseException,
    ) -> None:
        """Cleanup errors do not replace the setup failure being handled."""
        mock_executor = SandboxedAgentExecutor(input=executor_input)
        job_dir = tmp_path / "agent-job-test"
        warning = MagicMock()

        async def fail_stage_resolved_skills(_skills_dir: Path) -> None:
            raise setup_error

        async def fail_cleanup_in_thread(
            func: object, /, *args: object, **kwargs: object
        ) -> None:
            assert func is force_rmtree
            assert args == (job_dir,)
            assert kwargs == {}
            raise OSError("cleanup failed")

        def fake_mkdtemp(*, prefix: str, dir: Path) -> str:
            del prefix, dir
            job_dir.mkdir()
            return str(job_dir)

        monkeypatch.setattr(
            mock_executor, "_stage_resolved_skills", fail_stage_resolved_skills
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.asyncio.to_thread",
            fail_cleanup_in_thread,
        )
        monkeypatch.setattr(
            "tracecat.agent.executor.activity.tempfile.mkdtemp", fake_mkdtemp
        )
        monkeypatch.setattr("tracecat.agent.executor.activity.logger.warning", warning)

        with pytest.raises(type(setup_error)) as exc_info:
            await mock_executor._create_job_directory()

        assert exc_info.value is setup_error
        assert job_dir.exists()
        warning.assert_called_once()


class TestSandboxedAgentExecutorJobCleanup:
    """Tests for activity-owned per-job directory cleanup."""

    @pytest.fixture
    def executor(self, executor_input: AgentExecutorInput) -> SandboxedAgentExecutor:
        return SandboxedAgentExecutor(input=executor_input)

    @pytest.mark.anyio
    async def test_cleanup_removes_realistic_uv_state_tree(
        self,
        executor: SandboxedAgentExecutor,
        tmp_path: Path,
    ) -> None:
        """Activity cleanup removes every job path, including nested UV state."""
        job_dir = tmp_path / "job"
        seeded_paths = _seed_realistic_agent_job_tree(job_dir)
        executor._job_dir = job_dir

        assert all(path.exists() for path in seeded_paths)

        await executor._cleanup()

        assert not job_dir.exists()
        assert all(not path.exists() for path in seeded_paths)
        assert not job_uv_state_dir(job_dir).exists()

    @pytest.mark.anyio
    async def test_run_cancellation_removes_job_scoped_uv_state(
        self,
        executor: SandboxedAgentExecutor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A propagated mid-run cancellation still removes job-scoped UV state."""
        job_dir = tmp_path / "job"
        uv_state_dir = job_uv_state_dir(job_dir)
        proxy = AsyncMock()

        async def fake_create_job_directory() -> Path:
            _seed_realistic_agent_job_tree(job_dir)
            return job_dir

        async def fail_run_with_broker(
            *_args: object,
            **_kwargs: object,
        ) -> None:
            raise asyncio.CancelledError("runtime cancelled mid-flight")

        _stub_run_setup(
            monkeypatch,
            executor,
            create_job_directory=fake_create_job_directory,
            proxy=proxy,
            run_with_broker=fail_run_with_broker,
            stub_cleanup=False,
        )

        with pytest.raises(
            asyncio.CancelledError, match="runtime cancelled mid-flight"
        ):
            await executor.run()

        assert not job_dir.exists()
        assert not uv_state_dir.exists()
        proxy.stop.assert_awaited_once()

    @pytest.mark.anyio
    async def test_run_success_removes_job_scoped_uv_state(
        self,
        executor: SandboxedAgentExecutor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A normally completed run removes its job directory and UV state."""
        job_dir = tmp_path / "job"
        uv_state_dir = job_uv_state_dir(job_dir)
        proxy = AsyncMock()

        async def fake_create_job_directory() -> Path:
            _seed_realistic_agent_job_tree(job_dir)
            return job_dir

        async def fake_run_with_broker(
            *_args: object,
            **kwargs: object,
        ) -> None:
            result = cast(AgentExecutorResult, kwargs["result"])
            result.success = True

        _stub_run_setup(
            monkeypatch,
            executor,
            create_job_directory=fake_create_job_directory,
            proxy=proxy,
            run_with_broker=fake_run_with_broker,
            stub_cleanup=False,
        )

        result = await executor.run()

        assert result.success is True
        assert not job_dir.exists()
        assert not uv_state_dir.exists()
        proxy.stop.assert_awaited_once()

    @pytest.mark.anyio
    async def test_cleanup_swallows_rmtree_error_and_warns(
        self,
        executor: SandboxedAgentExecutor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Activity cleanup logs and swallows an explicit rmtree failure."""
        job_dir = tmp_path / "job"
        _seed_realistic_agent_job_tree(job_dir)
        executor._job_dir = job_dir
        real_rmtree = shutil.rmtree
        warning = MagicMock()

        def fail_rmtree(path: Path) -> None:
            assert path == job_dir
            raise OSError("simulated cleanup failure")

        monkeypatch.setattr("tracecat.agent.common.fs.shutil.rmtree", fail_rmtree)
        monkeypatch.setattr("tracecat.agent.executor.activity.logger.warning", warning)

        try:
            await executor._cleanup()

            assert job_dir.exists()
            warning.assert_called_once()
        finally:
            real_rmtree(job_dir, ignore_errors=True)

    @pytest.mark.anyio
    async def test_cleanup_removes_read_only_uv_subtree(
        self,
        executor: SandboxedAgentExecutor,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Activity cleanup removes a sandbox-owned 0555 UV cache subtree."""
        job_dir = tmp_path / "job"
        _seed_realistic_agent_job_tree(job_dir)
        read_only_dir = job_uv_state_dir(job_dir) / "cache" / "read-only"
        nested_file = read_only_dir / "archive" / "artifact"
        nested_file.parent.mkdir(parents=True)
        nested_file.write_text("cached artifact")
        read_only_dir.chmod(0o555)
        executor._job_dir = job_dir
        warning = MagicMock()
        monkeypatch.setattr("tracecat.agent.executor.activity.logger.warning", warning)

        try:
            await executor._cleanup()

            assert not job_dir.exists()
            assert not read_only_dir.exists()
            warning.assert_not_called()
        finally:
            if read_only_dir.exists():
                read_only_dir.chmod(0o700)
            shutil.rmtree(job_dir, ignore_errors=True)
