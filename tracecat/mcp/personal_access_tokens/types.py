"""Internal types for MCP personal access tokens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tracecat.identifiers import OrganizationID, UserID, WorkspaceID


@dataclass(frozen=True, slots=True)
class MCPPATIdentity:
    """Resolved identity for a verified MCP personal access token."""

    key_id: str
    user_id: UserID
    email: str
    organization_id: OrganizationID
    workspace_id: WorkspaceID
    expires_at: datetime | None
