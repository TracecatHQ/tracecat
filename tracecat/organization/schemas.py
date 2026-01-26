from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from tracecat.auth.schemas import UserRole
from tracecat.authz.enums import OrgRole
from tracecat.identifiers import OrganizationID, UserID
from tracecat.invitations.enums import InvitationStatus

# Members


class OrgMemberRead(BaseModel):
    user_id: UserID
    first_name: str | None
    last_name: str | None
    email: EmailStr
    role: UserRole
    is_active: bool
    is_superuser: bool
    is_verified: bool
    last_login_at: datetime | None


# Organization


class OrgRead(BaseModel):
    id: str
    name: str


# Invitations


class OrgInvitationCreate(BaseModel):
    """Request body for creating an organization invitation."""

    email: EmailStr
    role: OrgRole = OrgRole.MEMBER


class OrgInvitationRead(BaseModel):
    """Response model for organization invitation."""

    id: UUID
    organization_id: OrganizationID
    email: EmailStr
    role: OrgRole
    status: InvitationStatus
    invited_by: UserID | None
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None


class OrgInvitationReadMinimal(BaseModel):
    """Minimal response for public token-based invitation lookup.

    Excludes sensitive fields like email, invited_by, and timestamps
    to reduce information disclosure when querying by token.
    """

    organization_id: OrganizationID
    role: OrgRole
    status: InvitationStatus
    expires_at: datetime


class OrgInvitationAccept(BaseModel):
    """Request body for accepting an organization invitation via token."""

    token: str
