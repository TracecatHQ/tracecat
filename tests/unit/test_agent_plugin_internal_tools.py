"""Internal tools registered by plugins through the entry point."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable, Collection, Iterator, Sequence
from typing import Any, cast

import pytest
from fastmcp.exceptions import ToolError
from tracecat_ee.agent.workflows.durable import _approved_internal_tool_name

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.agent.executor.activity import AgentExecutorInput, SandboxedAgentExecutor
from tracecat.agent.mcp import plugin_tools, trusted_server
from tracecat.agent.mcp.internal_tools import InternalToolError
from tracecat.agent.mcp.plugin_tools import (
    PluginInternalTool,
    granted_plugin_internal_tools,
    plugin_internal_tools,
)
from tracecat.agent.tokens import MCPTokenClaims
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role

READ = "internal.demo.read"
WRITE = "internal.demo.write"


async def _echo(args: dict[str, Any], claims: MCPTokenClaims) -> dict[str, Any]:
    if args.get("fail"):
        raise InternalToolError("refused for the agent")
    return {"session": str(claims.session_id), "args": args}


def _tool(name: str, *, requires_approval: bool = False) -> PluginInternalTool:
    return PluginInternalTool(
        definition=MCPToolDefinition(
            name=name,
            description=name,
            parameters_json_schema={"type": "object", "additionalProperties": False},
        ),
        handler=_echo,
        requires_approval=requires_approval,
    )


class _Plugin:
    def __init__(
        self,
        tools: Sequence[PluginInternalTool],
        granted: Collection[str] = (),
        *,
        fails: bool = False,
    ) -> None:
        self._tools = tools
        self._granted = granted
        self._fails = fails

    def tools(self) -> Sequence[PluginInternalTool]:
        return self._tools

    async def granted_tools(self, role: Role, session_id: uuid.UUID) -> Collection[str]:
        if self._fails:
            raise RuntimeError("boom")
        return self._granted


class _EntryPoint:
    def __init__(self, name: str, plugin: _Plugin | None) -> None:
        self.name = name
        self._plugin = plugin

    def load(self) -> Any:
        if self._plugin is None:
            raise ImportError("plugin does not import")
        return lambda: self._plugin


InstallPlugins = Callable[..., None]


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Iterator[InstallPlugins]:
    def install(*plugins: _Plugin | None) -> None:
        installed = [_EntryPoint(f"p{i}", plugin) for i, plugin in enumerate(plugins)]
        plugin_tools._loaded_plugins.cache_clear()
        monkeypatch.setattr(plugin_tools, "entry_points", lambda group: installed)

    yield install
    plugin_tools._loaded_plugins.cache_clear()


def _role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-agent-executor",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )


def _claims(allowed: list[str], deferred: list[str] | None = None) -> MCPTokenClaims:
    return MCPTokenClaims(
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        allowed_actions=allowed,
        allowed_internal_tools=allowed,
        deferred_actions=deferred or [],
    )


def test_a_plugin_that_fails_to_load_or_misnames_a_tool_is_skipped(
    install: InstallPlugins,
) -> None:
    # One broken plugin must not fail every turn: its tools are simply absent.
    install(
        _Plugin([_tool("core.demo")]),
        _Plugin([_tool(READ)]),
        _Plugin([_tool(READ), _tool("internal.dup.other")]),
        _Plugin([_tool("internal.builder.update_preset")]),
        _Plugin([_tool("internal.bad__name.tool")]),
        None,
        _Plugin([_tool(WRITE)]),
    )

    assert sorted(plugin_internal_tools()) == [READ, WRITE]


@pytest.mark.anyio
async def test_a_session_gets_only_granted_tools_and_a_failing_plugin_grants_none(
    install: InstallPlugins,
) -> None:
    install(
        _Plugin([_tool(READ), _tool(WRITE)], granted=[WRITE, "internal.other.x"]),
        _Plugin(
            [_tool("internal.broken.tool")],
            granted=["internal.broken.tool"],
            fails=True,
        ),
        _Plugin([_tool("internal.none.tool")], granted=cast(Collection[str], None)),
    )
    granted = await granted_plugin_internal_tools(_role(), uuid.uuid4())
    assert [tool.name for tool in granted] == [WRITE]


class _StuckPlugin(_Plugin):
    async def granted_tools(self, role: Role, session_id: uuid.UUID) -> Collection[str]:
        await asyncio.sleep(60)
        return self._granted


@pytest.mark.anyio
async def test_a_stuck_grant_gives_up_and_grants_nothing(
    install: InstallPlugins, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_tools, "PLUGIN_GRANT_TIMEOUT_SECONDS", 0.01)
    install(
        _StuckPlugin([_tool(READ)], granted=[READ]), _Plugin([_tool(WRITE)], [WRITE])
    )

    granted = await granted_plugin_internal_tools(_role(), uuid.uuid4())

    assert [tool.name for tool in granted] == [WRITE]


@pytest.mark.anyio
async def test_the_trusted_server_lists_and_runs_only_allowed_plugin_tools(
    install: InstallPlugins,
) -> None:
    install(_Plugin([_tool(READ), _tool(WRITE, requires_approval=True)]))
    claims = _claims([READ], deferred=[WRITE])
    build = await trusted_server._build_token_scoped_tools(claims)
    assert sorted(tool.name for tool in build.tools) == [
        "internal__demo__read",
        "internal__demo__write",
    ]

    result = json.loads(
        await trusted_server.call_token_scoped_tool(
            "internal__demo__read", {"a": 1}, claims
        )
    )
    assert result == {"session": str(claims.session_id), "args": {"a": 1}}
    with pytest.raises(ToolError, match="requires approval"):
        await trusted_server.call_token_scoped_tool("internal__demo__write", {}, claims)
    with pytest.raises(ToolError, match="not authorized"):
        await trusted_server.call_token_scoped_tool(
            "internal__demo__read", {}, _claims([])
        )
    with pytest.raises(ToolError, match="refused for the agent"):
        await trusted_server.call_token_scoped_tool(
            "internal__demo__read", {"fail": True}, claims
        )


def test_approved_internal_calls_route_to_the_trusted_router() -> None:
    assert (
        _approved_internal_tool_name("mcp__tracecat-registry__internal__demo__write")
        == "internal__demo__write"
    )
    assert (
        _approved_internal_tool_name("internal.demo.write") == "internal__demo__write"
    )
    assert _approved_internal_tool_name("core.http_request") is None
    assert _approved_internal_tool_name("mcp.Jira.getIssue") is None


def test_runtime_payload_marks_plugin_tools_root_only_without_loading_plugins() -> None:
    role = _role()
    assert role.workspace_id is not None
    executor = SandboxedAgentExecutor(
        input=AgentExecutorInput(
            session_id=uuid.uuid4(),
            workspace_id=role.workspace_id,
            user_prompt="hi",
            config=AgentConfig(model_name="m", model_provider="p"),
            role=role,
            mcp_auth_token="mcp",
            llm_gateway_auth_token="llm",
            allowed_actions={
                name: _tool(name).definition
                for name in (READ, "internal.builder.get_session", "core.http_request")
            },
        )
    )

    payload = executor._build_runtime_init_payload()

    # No plugin is installed here: an unloadable plugin's tools still stay root-only.
    assert payload.root_only_actions == [READ]
    assert payload.to_dict()["root_only_actions"] == [READ]
