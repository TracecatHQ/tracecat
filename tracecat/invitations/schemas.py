from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from tracecat.identifiers import InvitationID, OrganizationID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus


class InvitationAccept(BaseModel):
    """Request body for accepting any invitation (org or workspace) via token."""

    token: str


class InvitationReadMinimal(BaseModel):
    """Public token-lookup response for both org and workspace invitations.

    The frontend uses this to render the accept page. When `workspace_id` is
    present the invitation is workspace-scoped; otherwise it is org-scoped.
    """

    invitation_id: InvitationID
    organization_id: OrganizationID
    organization_name: str
    workspace_id: WorkspaceID | None = None
    workspace_name: str | None = None
    inviter_name: str | None = None
    inviter_email: str | None = None
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    expires_at: datetime
    email_matches: bool | None = None
