"""API schemas for organization invitations and the grants they confer."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus


class InvitationGrant(BaseModel):
    """One role grant: at org scope when ``workspace_id`` is None."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: WorkspaceID | None = Field(default=None)
    role_id: UUID


class InvitationCreate(BaseModel):
    """Request body for creating an invitation."""

    email: EmailStr
    grants: list[InvitationGrant] = Field(min_length=1)

    @model_validator(mode="after")
    def _reject_duplicate_scopes(self) -> InvitationCreate:
        # One grant per scope; None is the organization scope.
        scopes = [grant.workspace_id for grant in self.grants]
        if len(set(scopes)) != len(scopes):
            raise ValueError(
                "Each workspace, and the organization, may appear at most once in grants"
            )
        return self


class InvitationRead(BaseModel):
    """Response model for an invitation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: OrganizationID
    email: EmailStr
    status: InvitationStatus
    invited_by: UserID | None
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None
    created_by_platform_admin: bool
    grants: list[InvitationGrant]


class InvitationReadMinimal(BaseModel):
    """Minimal public response for token-based lookup on the accept page.

    Excludes email, inviter ID, and timestamps to limit information disclosure.
    """

    organization_id: OrganizationID
    organization_name: str
    organization_slug: str
    inviter_name: str | None
    inviter_email: str | None
    grants: list[InvitationGrant]
    status: InvitationStatus
    expires_at: datetime
    email_matches: bool | None = None
    """Whether the authenticated user's email matches the invitation.

    - None: User is not authenticated
    - True: User's email matches the invitation
    - False: User's email does not match the invitation
    """


class PendingInvitationRead(BaseModel):
    """Pending invitation visible to the invited authenticated user."""

    token: str
    organization_id: OrganizationID
    organization_name: str
    inviter_name: str | None
    inviter_email: str | None
    grants: list[InvitationGrant]
    expires_at: datetime


class InvitationAccept(BaseModel):
    """Request body for accepting an invitation via token."""

    token: str


class InvitationTokenRead(BaseModel):
    """Raw invitation token response."""

    token: str
