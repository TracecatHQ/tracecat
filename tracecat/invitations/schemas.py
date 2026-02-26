from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from tracecat.identifiers import (
    InvitationID,
    OrganizationID,
    UserID,
    WorkspaceID,
)
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


# === Unified create/read/list schemas ===


class WorkspaceAssignment(BaseModel):
    """Workspace + role pair for org invitation workspace assignments."""

    workspace_id: WorkspaceID
    role_id: UUID


class InvitationCreate(BaseModel):
    """Unified request body for creating an invitation (org or workspace).

    When ``workspace_id`` is set the invitation targets a workspace and may
    resolve to a direct membership (if the user is already an org member).
    When ``workspace_id`` is ``None`` the invitation is org-level and may
    optionally include ``workspace_assignments`` for pre-assigning workspaces.
    """

    email: EmailStr
    role_id: UUID
    workspace_id: WorkspaceID | None = None
    workspace_assignments: list[WorkspaceAssignment] | None = None


class InvitationRead(BaseModel):
    """Unified response model for both org and workspace invitations."""

    id: InvitationID
    organization_id: OrganizationID
    workspace_id: WorkspaceID | None = None
    email: EmailStr
    role_id: UUID
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    invited_by: UserID | None = None
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None
    token: str | None = None


class PendingInvitationRead(BaseModel):
    """Pending invitation visible to the invited authenticated user."""

    token: str
    organization_id: OrganizationID
    organization_name: str
    workspace_id: WorkspaceID | None = None
    workspace_name: str | None = None
    inviter_name: str | None = None
    inviter_email: str | None = None
    role_name: str
    role_slug: str | None = None
    expires_at: datetime
