from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, EmailStr

from tracecat.identifiers import OrganizationID, UserID
from tracecat.invitations.enums import InvitationStatus

# Members


class OrgMemberStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    INVITED = "invited"


class OrgMemberRead(BaseModel):
    """Unified member representation — covers active, inactive, and pending (invited) members."""

    user_id: UserID | None = None
    invitation_id: UUID | None = None
    email: EmailStr
    role_name: str
    role_slug: str | None = None
    status: OrgMemberStatus
    first_name: str | None = None
    last_name: str | None = None
    last_login_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None


class OrgMemberDetail(BaseModel):
    """Detailed member info for /me and update endpoints."""

    user_id: UserID
    first_name: str | None
    last_name: str | None
    email: EmailStr
    role: str
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None


# Organization


class OrgRead(BaseModel):
    id: UUID
    name: str


class OrgMCPClientSnippets(BaseModel):
    codex: str
    claude_code: str
    cursor: str


class OrgMCPConnectRead(BaseModel):
    organization_id: OrganizationID
    server_url: str
    scoped_server_url: str
    scope_expires_at: datetime
    snippets: OrgMCPClientSnippets


class OrgDomainRead(BaseModel):
    id: UUID
    organization_id: OrganizationID
    domain: str
    normalized_domain: str
    is_primary: bool
    is_active: bool
    verified_at: datetime | None
    verification_method: str
    created_at: datetime
    updated_at: datetime


# Invitations


class OrgInvitationCreate(BaseModel):
    """Request body for creating an organization invitation."""

    email: EmailStr
    role_id: UUID


class OrgInvitationRead(BaseModel):
    """Response model for organization invitation."""

    id: UUID
    organization_id: OrganizationID
    email: EmailStr
    role_id: UUID
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    invited_by: UserID | None
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None


class OrgInvitationReadMinimal(BaseModel):
    """Minimal response for public token-based invitation lookup.

    Excludes sensitive fields like email, invited_by ID, and timestamps
    to reduce information disclosure when querying by token.
    """

    organization_id: OrganizationID
    organization_name: str
    inviter_name: str | None
    inviter_email: str | None
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    expires_at: datetime
    email_matches: bool | None = None
    """Whether the authenticated user's email matches the invitation.

    - None: User is not authenticated
    - True: User's email matches the invitation
    - False: User's email does not match the invitation
    """


class OrgPendingInvitationRead(BaseModel):
    """Pending invitation visible to the invited authenticated user."""

    token: str
    organization_id: OrganizationID
    organization_name: str
    inviter_name: str | None
    inviter_email: str | None
    role_name: str
    role_slug: str | None = None
    expires_at: datetime


class OrgInvitationAccept(BaseModel):
    """Request body for accepting an organization invitation via token."""

    token: str
