"""Shared MCP catalog normalization helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

import mcp.types as mcp_types
import orjson
from mcp import McpError

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.logger import logger


class MCPServerCatalogArtifact(TypedDict):
    """Normalized MCP artifact payload for persisted discovery."""

    artifact_type: str
    artifact_ref: str
    display_name: str | None
    description: str | None
    input_schema: dict[str, Any] | None
    metadata: dict[str, Any] | None
    raw_payload: dict[str, Any]
    content_hash: str


@dataclass(frozen=True, slots=True)
class MCPServerCatalog:
    """Normalized catalog for a single MCP server."""

    server_name: str
    tools: tuple[MCPServerCatalogArtifact, ...]
    resources: tuple[MCPServerCatalogArtifact, ...]
    prompts: tuple[MCPServerCatalogArtifact, ...]

    @property
    def artifacts(self) -> tuple[MCPServerCatalogArtifact, ...]:
        """Return a flattened view across all MCP artifact types."""
        return self.tools + self.resources + self.prompts

    def to_tool_definitions(self) -> dict[str, MCPToolDefinition]:
        """Convert catalog tools into the agent bootstrap tool definition map."""
        tools: dict[str, MCPToolDefinition] = {}
        for tool in self.tools:
            tool_name = tool["artifact_ref"]
            prefixed_name = f"mcp__{self.server_name}__{tool_name}"
            tools[prefixed_name] = MCPToolDefinition(
                name=prefixed_name,
                description=tool["description"] or f"Tool from {self.server_name}",
                parameters_json_schema=tool["input_schema"] or {},
            )
        return tools


def model_to_payload(model: Any) -> dict[str, Any]:
    """Convert an MCP model into a stable JSON payload."""
    if not hasattr(model, "model_dump"):
        raise TypeError(f"Unsupported MCP artifact type: {type(model).__name__}")
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    if not isinstance(payload, dict):
        raise TypeError(f"Unexpected MCP artifact payload: {type(payload).__name__}")
    return payload


def content_hash(payload: dict[str, Any]) -> str:
    """Compute a stable content hash for a normalized MCP artifact."""
    canonical = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(canonical).hexdigest()


def compact_metadata(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Drop empty metadata fields to keep persisted payloads compact."""
    compact = {key: value for key, value in metadata.items() if value is not None}
    return compact or None


def prompt_input_schema(
    arguments: list[mcp_types.PromptArgument] | None,
) -> dict[str, Any]:
    """Build a JSON schema for prompt arguments."""
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for argument in arguments or []:
        property_schema: dict[str, Any] = {"type": "string"}
        if argument.description is not None:
            property_schema["description"] = argument.description
        properties[argument.name] = property_schema
        if argument.required:
            required.append(argument.name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required
    return input_schema


def normalize_tool(tool: mcp_types.Tool) -> MCPServerCatalogArtifact:
    """Normalize an MCP tool into the persisted discovery shape."""
    raw_payload = model_to_payload(tool)
    display_name = tool.title or (tool.annotations.title if tool.annotations else None)
    metadata = compact_metadata(
        {
            "icons": [
                icon.model_dump(mode="json", exclude_none=True) for icon in tool.icons
            ]
            if tool.icons
            else None,
            "annotations": tool.annotations.model_dump(mode="json", exclude_none=True)
            if tool.annotations
            else None,
            "meta": tool.meta,
            "output_schema": tool.outputSchema,
            "execution": tool.execution.model_dump(mode="json", exclude_none=True)
            if tool.execution
            else None,
        }
    )
    return {
        "artifact_type": "tool",
        "artifact_ref": tool.name,
        "display_name": display_name or tool.name,
        "description": tool.description,
        "input_schema": tool.inputSchema,
        "metadata": metadata,
        "raw_payload": raw_payload,
        "content_hash": content_hash(raw_payload),
    }


def normalize_resource(resource: mcp_types.Resource) -> MCPServerCatalogArtifact:
    """Normalize an MCP resource into the persisted discovery shape."""
    raw_payload = model_to_payload(resource)
    metadata = compact_metadata(
        {
            "name": resource.name,
            "mime_type": resource.mimeType,
            "size": resource.size,
            "icons": [
                icon.model_dump(mode="json", exclude_none=True)
                for icon in resource.icons
            ]
            if resource.icons
            else None,
            "annotations": resource.annotations.model_dump(
                mode="json", exclude_none=True
            )
            if resource.annotations
            else None,
            "meta": resource.meta,
        }
    )
    display_name = resource.title or resource.name or str(resource.uri)
    return {
        "artifact_type": "resource",
        "artifact_ref": str(resource.uri),
        "display_name": display_name,
        "description": resource.description,
        "input_schema": None,
        "metadata": metadata,
        "raw_payload": raw_payload,
        "content_hash": content_hash(raw_payload),
    }


def normalize_prompt(prompt: mcp_types.Prompt) -> MCPServerCatalogArtifact:
    """Normalize an MCP prompt into the persisted discovery shape."""
    raw_payload = model_to_payload(prompt)
    metadata = compact_metadata(
        {
            "arguments": [
                argument.model_dump(mode="json", exclude_none=True)
                for argument in prompt.arguments or []
            ],
            "icons": [
                icon.model_dump(mode="json", exclude_none=True) for icon in prompt.icons
            ]
            if prompt.icons
            else None,
            "meta": prompt.meta,
        }
    )
    return {
        "artifact_type": "prompt",
        "artifact_ref": prompt.name,
        "display_name": prompt.title or prompt.name,
        "description": prompt.description,
        "input_schema": prompt_input_schema(prompt.arguments),
        "metadata": metadata,
        "raw_payload": raw_payload,
        "content_hash": content_hash(raw_payload),
    }


def is_unsupported_optional_capability(exc: McpError) -> bool:
    """Return whether a list_resources/list_prompts error means unsupported."""
    message = exc.error.message.lower()
    return exc.error.code == mcp_types.METHOD_NOT_FOUND or (
        "method not found" in message or "not supported" in message
    )


async def list_optional_capability[T](
    *,
    server_name: str,
    capability_name: str,
    list_fn: Callable[[], Awaitable[list[T]]],
) -> list[T]:
    """List an optional MCP capability, downgrading unsupported methods to empty."""
    try:
        return await list_fn()
    except McpError as exc:
        if not is_unsupported_optional_capability(exc):
            raise
        logger.info(
            "MCP capability not supported",
            server_name=server_name,
            capability=capability_name,
            error_code=exc.error.code,
            error_message=exc.error.message,
        )
        return []
