"""Types for MCP catalog authorization decisions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.integrations.enums import MCPCatalogArtifactType


@dataclass(frozen=True, slots=True)
class AuthorizedMCPCatalogEntry:
    """Caller-authorized MCP catalog entry metadata."""

    id: UUID
    mcp_integration_id: UUID
    workspace_id: WorkspaceID
    organization_id: OrganizationID
    scope_name: str
    artifact_type: MCPCatalogArtifactType
    artifact_key: str
    artifact_ref: str
    display_name: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class MCPCatalogAuthorizationResult:
    """Authorization result for MCP catalog search or batch access."""

    workspace_id: WorkspaceID
    organization_id: OrganizationID
    is_org_admin: bool
    allowed_scope_names: frozenset[str]
    entries: tuple[AuthorizedMCPCatalogEntry, ...]
    agent_metadata: dict[str, str] | None = None

    @property
    def allowed_entry_ids(self) -> frozenset[UUID]:
        return frozenset(entry.id for entry in self.entries)
