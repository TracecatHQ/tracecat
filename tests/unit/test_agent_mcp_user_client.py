import pytest

from tracecat.agent.common.types import MCPHttpServerConfig, MCPToolDefinition
from tracecat.agent.mcp.user_client import UserMCPClient


def _mcp_server(name: str) -> MCPHttpServerConfig:
    return {
        "name": name,
        "url": f"https://{name}.example/mcp",
    }


@pytest.mark.anyio
async def test_discover_tools_continues_on_server_failure_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover_server_tools(
        self: UserMCPClient,
        server_name: str,
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        del self, config
        if server_name == "broken":
            raise RuntimeError("discovery failed")
        return {
            f"mcp__{server_name}__search": MCPToolDefinition(
                name=f"mcp__{server_name}__search",
                description="Search",
                parameters_json_schema={"type": "object"},
            )
        }

    monkeypatch.setattr(
        UserMCPClient,
        "_discover_server_tools",
        fake_discover_server_tools,
    )
    client = UserMCPClient([_mcp_server("working"), _mcp_server("broken")])

    tools = await client.discover_tools()

    assert list(tools) == ["mcp__working__search"]


@pytest.mark.anyio
async def test_discover_tools_fails_closed_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover_server_tools(
        self: UserMCPClient,
        server_name: str,
        config: MCPHttpServerConfig,
    ) -> dict[str, MCPToolDefinition]:
        del self, config
        if server_name == "broken":
            raise RuntimeError("discovery failed")
        return {}

    monkeypatch.setattr(
        UserMCPClient,
        "_discover_server_tools",
        fake_discover_server_tools,
    )
    client = UserMCPClient([_mcp_server("working"), _mcp_server("broken")])

    with pytest.raises(
        RuntimeError,
        match="Failed to discover tools from user MCP server 'broken'",
    ):
        await client.discover_tools(fail_on_error=True)
