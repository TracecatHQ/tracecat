"""Conversions between workflow-safe agent config payloads and runtime config."""

from __future__ import annotations

import uuid

from tracecat.agent.common.types import (
    MCPHttpServerConfig,
    MCPServerConfig,
    MCPStdioServerConfig,
)
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import (
    AgentConfigPayload,
    MCPHttpServerConfigPayload,
    MCPServerConfigPayload,
    MCPStdioServerConfigPayload,
)


def _mcp_server_to_payload(
    server: MCPServerConfig,
) -> MCPHttpServerConfigPayload | MCPStdioServerConfigPayload:
    match server:
        case {
            "type": "stdio",
            "name": str(name),
            "command": str(command),
        }:
            return MCPStdioServerConfigPayload(
                type="stdio",
                name=name,
                command=command,
                args=server.get("args"),
                env=server.get("env"),
                timeout=server.get("timeout"),
            )
        case {
            "name": str(name),
            "url": str(url),
        }:
            return MCPHttpServerConfigPayload(
                type="http",
                name=name,
                url=url,
                headers=server.get("headers"),
                transport=server.get("transport"),
                timeout=server.get("timeout"),
            )
        case _:
            raise ValueError(f"Unsupported MCP server config: {server!r}")


def _mcp_server_from_payload(server: MCPServerConfigPayload) -> MCPServerConfig:
    match server:
        case MCPStdioServerConfigPayload():
            stdio_server: MCPStdioServerConfig = {
                "type": "stdio",
                "name": server.name,
                "command": server.command,
            }
            if server.args is not None:
                stdio_server["args"] = server.args
            if server.env is not None:
                stdio_server["env"] = server.env
            if server.timeout is not None:
                stdio_server["timeout"] = server.timeout
            return stdio_server
        case MCPHttpServerConfigPayload():
            http_server: MCPHttpServerConfig = {
                "type": "http",
                "name": server.name,
                "url": server.url,
            }
            if server.headers is not None:
                http_server["headers"] = server.headers
            if server.transport is not None:
                http_server["transport"] = server.transport
            if server.timeout is not None:
                http_server["timeout"] = server.timeout
            return http_server


def agent_config_to_payload(config: AgentConfig) -> AgentConfigPayload:
    """Convert runtime AgentConfig to workflow-safe payload."""
    return AgentConfigPayload(
        model_name=config.model_name,
        model_provider=config.model_provider,
        source_id=(str(config.source_id) if config.source_id is not None else None),
        base_url=config.base_url,
        instructions=config.instructions,
        output_type=config.output_type,
        actions=config.actions,
        namespaces=config.namespaces,
        tool_approvals=config.tool_approvals,
        model_settings=config.model_settings,
        mcp_servers=(
            [_mcp_server_to_payload(server) for server in config.mcp_servers]
            if config.mcp_servers
            else None
        ),
        retries=config.retries,
        enable_internet_access=config.enable_internet_access,
    )


def agent_config_from_payload(payload: AgentConfigPayload) -> AgentConfig:
    """Convert workflow-safe payload back to runtime AgentConfig."""
    return AgentConfig(
        model_name=payload.model_name,
        model_provider=payload.model_provider,
        source_id=(
            uuid.UUID(payload.source_id) if payload.source_id is not None else None
        ),
        base_url=payload.base_url,
        instructions=payload.instructions,
        output_type=payload.output_type,
        actions=payload.actions,
        namespaces=payload.namespaces,
        tool_approvals=payload.tool_approvals,
        model_settings=payload.model_settings,
        mcp_servers=(
            [_mcp_server_from_payload(server) for server in payload.mcp_servers]
            if payload.mcp_servers
            else None
        ),
        retries=payload.retries,
        enable_internet_access=payload.enable_internet_access,
    )
