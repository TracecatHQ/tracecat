from __future__ import annotations

from typing import Any

import mcp.types as mcp_types
import pytest
from mcp import McpError
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    PromptMessage,
    TextContent,
    TextResourceContents,
)
from pydantic import AnyUrl

from tracecat.agent.common.types import MCPHttpServerConfig
from tracecat.agent.mcp import user_client
from tracecat.agent.mcp.user_client import UserMCPClient, infer_transport_type


def _server_config() -> MCPHttpServerConfig:
    return {
        "name": "remote-catalog",
        "url": "https://example.test/mcp",
        "transport": "http",
        "headers": {"Authorization": "Bearer token"},
        "timeout": 30,
    }


def _fake_client_factory(
    *,
    tools: list[mcp_types.Tool] | Exception,
    resources: list[mcp_types.Resource] | Exception,
    prompts: list[mcp_types.Prompt] | Exception,
):
    class FakeClient:
        def __init__(self, transport: Any) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: Any,
        ) -> bool:
            return False

        async def list_tools(self) -> list[mcp_types.Tool]:
            if isinstance(tools, Exception):
                raise tools
            return tools

        async def list_resources(self) -> list[mcp_types.Resource]:
            if isinstance(resources, Exception):
                raise resources
            return resources

        async def list_prompts(self) -> list[mcp_types.Prompt]:
            if isinstance(prompts, Exception):
                raise prompts
            return prompts

    return FakeClient


@pytest.mark.anyio
async def test_discover_mcp_server_catalog_normalizes_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = mcp_types.Tool(
        name="search",
        title="Search docs",
        description="Search remote docs",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        outputSchema={"type": "object"},
        _meta={"origin": "tool-meta"},
    )
    resource = mcp_types.Resource(
        name="Playbook",
        title="Playbook resource",
        uri=AnyUrl("resource://playbooks/1"),
        description="Incident playbook",
        mimeType="text/markdown",
        size=42,
        _meta={"origin": "resource-meta"},
    )
    prompt = mcp_types.Prompt(
        name="draft_report",
        title="Draft report",
        description="Create a report draft",
        arguments=[
            mcp_types.PromptArgument(
                name="incident_id",
                description="Incident identifier",
                required=True,
            )
        ],
        _meta={"origin": "prompt-meta"},
    )

    monkeypatch.setattr(
        user_client,
        "Client",
        _fake_client_factory(tools=[tool], resources=[resource], prompts=[prompt]),
    )

    catalog = await user_client.discover_mcp_server_catalog(_server_config())

    assert catalog.server_name == "remote-catalog"
    assert len(catalog.tools) == 1
    assert len(catalog.resources) == 1
    assert len(catalog.prompts) == 1
    assert [artifact["artifact_type"] for artifact in catalog.artifacts] == [
        "tool",
        "resource",
        "prompt",
    ]

    tool_artifact = catalog.tools[0]
    assert tool_artifact["artifact_ref"] == "search"
    assert tool_artifact["display_name"] == "Search docs"
    assert tool_artifact["input_schema"] == tool.inputSchema
    assert tool_artifact["metadata"] == {
        "meta": {"origin": "tool-meta"},
        "output_schema": {"type": "object"},
    }
    assert tool_artifact["raw_payload"]["name"] == "search"
    assert tool_artifact["raw_payload"]["title"] == "Search docs"
    assert tool_artifact["content_hash"]

    resource_artifact = catalog.resources[0]
    assert resource_artifact["artifact_ref"] == "resource://playbooks/1"
    assert resource_artifact["display_name"] == "Playbook resource"
    assert resource_artifact["input_schema"] is None
    assert resource_artifact["metadata"] == {
        "name": "Playbook",
        "mime_type": "text/markdown",
        "size": 42,
        "meta": {"origin": "resource-meta"},
    }
    assert resource_artifact["raw_payload"]["mimeType"] == "text/markdown"

    prompt_artifact = catalog.prompts[0]
    assert prompt_artifact["artifact_ref"] == "draft_report"
    assert prompt_artifact["display_name"] == "Draft report"
    assert prompt_artifact["input_schema"] == {
        "type": "object",
        "properties": {
            "incident_id": {
                "type": "string",
                "description": "Incident identifier",
            }
        },
        "required": ["incident_id"],
        "additionalProperties": False,
    }
    assert prompt_artifact["metadata"] == {
        "arguments": [
            {
                "name": "incident_id",
                "description": "Incident identifier",
                "required": True,
            }
        ],
        "meta": {"origin": "prompt-meta"},
    }

    tool_definitions = catalog.to_tool_definitions()
    assert tool_definitions == {
        "mcp__remote-catalog__search": user_client.MCPToolDefinition(
            name="mcp__remote-catalog__search",
            description="Search remote docs",
            parameters_json_schema=tool.inputSchema,
        )
    }


@pytest.mark.anyio
async def test_discover_mcp_server_catalog_treats_unsupported_optional_capabilities_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = mcp_types.Tool(
        name="lookup",
        description="Lookup a record",
        inputSchema={"type": "object"},
    )
    unsupported_error = McpError(
        mcp_types.ErrorData(
            code=mcp_types.METHOD_NOT_FOUND,
            message="Method not found",
        )
    )

    monkeypatch.setattr(
        user_client,
        "Client",
        _fake_client_factory(
            tools=[tool],
            resources=unsupported_error,
            prompts=unsupported_error,
        ),
    )

    catalog = await user_client.discover_mcp_server_catalog(_server_config())

    assert len(catalog.tools) == 1
    assert catalog.resources == ()
    assert catalog.prompts == ()


@pytest.mark.anyio
async def test_discover_user_mcp_tools_preserves_existing_bootstrap_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = mcp_types.Tool(
        name="lookup",
        description="Lookup a record",
        inputSchema={"type": "object", "properties": {"id": {"type": "string"}}},
    )

    monkeypatch.setattr(
        user_client,
        "Client",
        _fake_client_factory(tools=[tool], resources=[], prompts=[]),
    )

    discovered = await user_client.discover_user_mcp_tools([_server_config()])

    assert discovered == {
        "mcp__remote-catalog__lookup": user_client.MCPToolDefinition(
            name="mcp__remote-catalog__lookup",
            description="Lookup a record",
            parameters_json_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        )
    }


@pytest.mark.anyio
async def test_discover_mcp_server_catalog_still_raises_tool_discovery_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_error = McpError(
        mcp_types.ErrorData(
            code=mcp_types.METHOD_NOT_FOUND,
            message="Method not found",
        )
    )

    monkeypatch.setattr(
        user_client,
        "Client",
        _fake_client_factory(tools=tool_error, resources=[], prompts=[]),
    )

    with pytest.raises(McpError):
        await user_client.discover_mcp_server_catalog(_server_config())


class _FakeClient:
    def __init__(self, transport: Any) -> None:
        self.transport = transport

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=f"{name}:{arguments['value']}")],
            structuredContent={"ok": True},
            isError=False,
        )

    async def read_resource(self, uri: str) -> list[TextResourceContents]:
        return [
            TextResourceContents(uri=AnyUrl("https://example.com/readme"), text=uri)
        ]

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None
    ) -> GetPromptResult:
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"{name}:{(arguments or {}).get('severity', 'none')}",
                    ),
                )
            ]
        )


def test_infer_transport_type_uses_sse_suffix() -> None:
    assert infer_transport_type("https://example.com/mcp/sse") == "sse"
    assert infer_transport_type("https://example.com/mcp") == "http"


@pytest.mark.anyio
async def test_user_mcp_client_supports_full_result_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config: MCPHttpServerConfig = {
        "type": "http",
        "name": "server",
        "url": "https://example.com/mcp",
        "transport": "http",
    }
    monkeypatch.setattr(user_client, "Client", _FakeClient)
    client = UserMCPClient([config])

    tool_result = await client.call_tool_result("server", "list_repos", {"value": "x"})
    resource_result = await client.read_resource("server", "docs://readme")
    prompt_result = await client.get_prompt(
        "server", "triage_incident", {"severity": "high"}
    )

    assert tool_result.structuredContent == {"ok": True}
    assert resource_result[0].model_dump(mode="json")["text"] == "docs://readme"
    assert (
        prompt_result.messages[0].content.model_dump(mode="json")["text"]
        == "triage_incident:high"
    )
