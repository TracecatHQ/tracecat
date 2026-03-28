from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tracecat.agent.common.types import MCPHttpServerConfig, MCPStdioServerConfig
from tracecat.agent.factory import build_agent
from tracecat.agent.mcp.utils import is_http_server
from tracecat.agent.types import AgentConfig


def testis_http_server_accepts_legacy_untyped_http_config() -> None:
    legacy_http_config: MCPHttpServerConfig = {
        "name": "legacy",
        "url": "http://localhost:8080",
    }

    assert is_http_server(legacy_http_config) is True


def testis_http_server_accepts_explicit_http_type() -> None:
    typed_http_config: MCPHttpServerConfig = {
        "type": "http",
        "name": "typed",
        "url": "http://localhost:8080",
    }

    assert is_http_server(typed_http_config) is True


def testis_http_server_rejects_stdio_type() -> None:
    stdio_config: MCPStdioServerConfig = {
        "type": "stdio",
        "name": "local-tools",
        "command": "npx",
    }

    assert is_http_server(stdio_config) is False


@pytest.mark.anyio
async def test_build_agent_routes_mcp_actions_to_filtered_toolsets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_agent_tools_mock = AsyncMock(
        return_value=SimpleNamespace(tools=[], collected_secrets=set())
    )
    captured_http_servers: list[dict[str, object]] = []

    class FakeHTTPToolset:
        def __init__(
            self,
            *,
            url: str,
            headers: dict[str, str],
            allowed_tool_names: set[str] | None = None,
            timeout: float | None = None,
        ) -> None:
            captured_http_servers.append(
                {
                    "url": url,
                    "headers": headers,
                    "allowed_tool_names": allowed_tool_names,
                    "timeout": timeout,
                }
            )

    class FakeAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(
        "tracecat.agent.factory.build_agent_tools", build_agent_tools_mock
    )
    monkeypatch.setattr(
        "tracecat.agent.factory._FilteredMCPServerStreamableHTTP",
        FakeHTTPToolset,
    )
    monkeypatch.setattr("tracecat.agent.factory.Agent", FakeAgent)
    monkeypatch.setattr("tracecat.agent.factory.get_model", lambda *args: "model")
    monkeypatch.setattr("tracecat.agent.factory.parse_output_type", lambda value: str)

    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        actions=["core.http_request", "mcp.Linear.list_issues"],
        mcp_servers=[
            {
                "type": "http",
                "name": "Linear",
                "url": "https://mcp.linear.app/mcp",
                "headers": {"Authorization": "Bearer token"},
            }
        ],
        instructions="test",
    )

    fake_agent = cast(FakeAgent, await build_agent(config))

    build_agent_tools_mock.assert_awaited_once()
    assert build_agent_tools_mock.await_args is not None
    assert build_agent_tools_mock.await_args.kwargs["actions"] == ["core.http_request"]
    assert captured_http_servers == [
        {
            "url": "https://mcp.linear.app/mcp",
            "headers": {"Authorization": "Bearer token"},
            "allowed_tool_names": {"list_issues"},
            "timeout": None,
        }
    ]
    assert fake_agent.kwargs["toolsets"] is not None


@pytest.mark.anyio
async def test_build_agent_preserves_dotted_mcp_tool_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_agent_tools_mock = AsyncMock(
        return_value=SimpleNamespace(tools=[], collected_secrets=set())
    )
    captured_http_servers: list[dict[str, object]] = []

    class FakeHTTPToolset:
        def __init__(
            self,
            *,
            url: str,
            headers: dict[str, str],
            allowed_tool_names: set[str] | None = None,
            timeout: float | None = None,
        ) -> None:
            captured_http_servers.append(
                {
                    "url": url,
                    "headers": headers,
                    "allowed_tool_names": allowed_tool_names,
                    "timeout": timeout,
                }
            )

    class FakeAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(
        "tracecat.agent.factory.build_agent_tools", build_agent_tools_mock
    )
    monkeypatch.setattr(
        "tracecat.agent.factory._FilteredMCPServerStreamableHTTP",
        FakeHTTPToolset,
    )
    monkeypatch.setattr("tracecat.agent.factory.Agent", FakeAgent)
    monkeypatch.setattr("tracecat.agent.factory.get_model", lambda *args: "model")
    monkeypatch.setattr("tracecat.agent.factory.parse_output_type", lambda value: str)

    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        actions=["mcp.linear.foo.bar"],
        mcp_servers=[
            {
                "type": "http",
                "name": "linear",
                "url": "https://mcp.linear.app/mcp",
                "headers": {"Authorization": "Bearer token"},
            }
        ],
        instructions="test",
    )

    await build_agent(config)

    assert captured_http_servers == [
        {
            "url": "https://mcp.linear.app/mcp",
            "headers": {"Authorization": "Bearer token"},
            "allowed_tool_names": {"foo.bar"},
            "timeout": None,
        }
    ]


@pytest.mark.anyio
async def test_build_agent_rejects_mcp_tool_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.factory.get_model",
        lambda *args: "model",
    )
    monkeypatch.setattr("tracecat.agent.factory.parse_output_type", lambda value: str)

    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        actions=["mcp.Linear.list_issues"],
        tool_approvals={"mcp.Linear.list_issues": True},
        mcp_servers=[
            {
                "type": "http",
                "name": "Linear",
                "url": "https://mcp.linear.app/mcp",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="MCP tool approvals are not supported in the PydanticAI runtime",
    ):
        await build_agent(config)


@pytest.mark.anyio
async def test_build_agent_rejects_stdio_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.factory.get_model",
        lambda *args: "model",
    )
    monkeypatch.setattr("tracecat.agent.factory.parse_output_type", lambda value: str)

    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        actions=["mcp.acme.com.list_issues"],
        mcp_servers=[
            {
                "type": "stdio",
                "name": "acme.com",
                "command": "acme-mcp",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="Stdio MCP servers are not supported in the PydanticAI runtime",
    ):
        await build_agent(config)


@pytest.mark.anyio
async def test_build_agent_ignores_unused_stdio_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_agent_tools_mock = AsyncMock(
        return_value=SimpleNamespace(tools=[], collected_secrets=set())
    )

    class FakeAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(
        "tracecat.agent.factory.build_agent_tools", build_agent_tools_mock
    )
    monkeypatch.setattr("tracecat.agent.factory.Agent", FakeAgent)
    monkeypatch.setattr("tracecat.agent.factory.get_model", lambda *args: "model")
    monkeypatch.setattr("tracecat.agent.factory.parse_output_type", lambda value: str)

    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        actions=["core.http_request"],
        mcp_servers=[
            {
                "type": "stdio",
                "name": "acme.com",
                "command": "acme-mcp",
            }
        ],
    )

    fake_agent = cast(FakeAgent, await build_agent(config))

    build_agent_tools_mock.assert_awaited_once()
    assert build_agent_tools_mock.await_args is not None
    assert build_agent_tools_mock.await_args.kwargs["actions"] == ["core.http_request"]
    assert fake_agent.kwargs["toolsets"] is None
