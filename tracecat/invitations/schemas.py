from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, BaseModel, EmailStr, Field

from tracecat.identifiers import InvitationID, OrganizationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus


class WorkspaceAssignment(BaseModel):
    """Workspace + role pair for org invitation fanout."""

    workspace_id: WorkspaceID
    role_id: UUID


class InvitationCreate(BaseModel):
    """Unified request body for org or workspace invitations."""

    email: EmailStr
    role_id: UUID
    workspace_id: WorkspaceID | None = Field(default=None)
    workspace_assignments: list[WorkspaceAssignment] | None = Field(default=None)


class InvitationWorkspaceOptionRead(BaseModel):
    """Workspace invitation row attached to a grouped org invitation."""

    invitation_id: InvitationID
    workspace_id: WorkspaceID
    workspace_name: str | None = None
    role_id: UUID
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None


class InvitationRead(BaseModel):
    """Unified admin/list response for org or standalone workspace invitations."""

    id: InvitationID
    organization_id: OrganizationID
    workspace_id: WorkspaceID | None = None
    workspace_name: str | None = None
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
    workspace_options: list[InvitationWorkspaceOptionRead] = Field(default_factory=list)


class InvitationCreateResponse(BaseModel):
    """Deterministic response envelope for invitation create requests."""

    message: str
    invitation: InvitationRead | None = None


class InvitationAccept(BaseModel):
    """Request body for accepting unified invitations."""

    token: str
    selected_workspace_ids: list[WorkspaceID] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "selected_workspace_ids",
            "selectedWorkspaceIds",
        ),
    )


class InvitationDecline(BaseModel):
    """Request body for declining unified invitations."""

    token: str


class InvitationReadMinimal(BaseModel):
    """Public token lookup response for invitation acceptance UI."""

    invitation_id: InvitationID
    organization_id: OrganizationID
    organization_slug: str
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
    accept_token: str
    workspace_options: list[InvitationWorkspaceOptionRead] = Field(default_factory=list)


class PendingInvitationRead(BaseModel):
    """Pending invitation visible to the invited authenticated user."""

    accept_token: str
    organization_id: OrganizationID
    organization_name: str
    workspace_id: WorkspaceID | None = None
    workspace_name: str | None = None
    inviter_name: str | None = None
    inviter_email: str | None = None
    role_name: str
    role_slug: str | None = None
    expires_at: datetime
    workspace_options: list[InvitationWorkspaceOptionRead] = Field(default_factory=list)
