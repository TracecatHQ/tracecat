"""Shared MCP scope derivation helpers."""

from __future__ import annotations

from tracecat.integrations.enums import MCPCatalogArtifactType


def build_mcp_scope_name(
    *,
    scope_namespace: str,
    artifact_type: MCPCatalogArtifactType,
    artifact_key: str,
) -> tuple[str, str, str]:
    """Build the scope name plus resource/action columns for an MCP artifact."""
    match artifact_type:
        case MCPCatalogArtifactType.TOOL:
            resource = "mcp-tool"
            action = "execute"
        case MCPCatalogArtifactType.RESOURCE:
            resource = "mcp-resource"
            action = "read"
        case MCPCatalogArtifactType.PROMPT:
            resource = "mcp-prompt"
            action = "use"
    return (
        f"{resource}:{scope_namespace}.{artifact_key}:{action}",
        resource,
        action,
    )
