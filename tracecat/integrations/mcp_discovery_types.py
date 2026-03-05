"""Shared types for persisted remote MCP discovery."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from tracecat.auth.types import Role
from tracecat.integrations.enums import MCPCatalogArtifactType, MCPDiscoveryTrigger


class NormalizedMCPArtifact(BaseModel):
    """Normalized MCP artifact persisted into the discovery catalog."""

    artifact_type: MCPCatalogArtifactType
    artifact_ref: str
    display_name: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    raw_payload: dict[str, Any]
    content_hash: str


class MCPDiscoveryWorkflowArgs(BaseModel):
    """Arguments for the remote MCP discovery workflow."""

    role: Role
    mcp_integration_id: uuid.UUID
    trigger: MCPDiscoveryTrigger
    started_at: datetime


class MCPDiscoveryWorkflowResult(BaseModel):
    """Result for a remote MCP discovery workflow run."""

    mcp_integration_id: uuid.UUID
    status: str
    catalog_version: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
