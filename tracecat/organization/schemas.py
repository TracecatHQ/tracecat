from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, EmailStr, model_validator

from tracecat.authz.enums import OrgRole
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
    role: OrgRole
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
    role_id: UUID | None
    """The user's org-level role ID, or None if no role assigned."""
    role_slug: str | None
    """The role's slug (e.g., 'admin', 'member', 'owner'), or None if no role."""
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None


# Organization


class OrgRead(BaseModel):
    id: UUID
    name: str


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
    """Request body for creating an organization invitation.

    Either role_id or role_slug must be provided to specify the role to grant.
    If both are provided, role_id takes precedence.
    """

    email: EmailStr
    role_id: UUID | None = None
    """UUID of the role to grant upon acceptance."""
    role_slug: str | None = None
    """Slug of the role to grant (e.g., 'admin', 'member', 'owner')."""

    @model_validator(mode="after")
    def validate_role_specified(self) -> Self:
        """Ensure at least one of role_id or role_slug is provided."""
        if self.role_id is None and self.role_slug is None:
            raise ValueError("Either role_id or role_slug must be provided")
        return self


class OrgInvitationRead(BaseModel):
    """Response model for organization invitation."""

    id: UUID
    organization_id: OrganizationID
    email: EmailStr
    role_id: UUID
    """UUID of the role to be granted upon acceptance."""
    role_slug: str | None
    """Slug of the role (e.g., 'admin', 'member'), or None for custom roles."""
    role_name: str
    """Display name of the role."""
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
    role_slug: str | None
    """Slug of the role (e.g., 'admin', 'member'), or None for custom roles."""
    role_name: str
    """Display name of the role."""
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
    role: OrgRole
    expires_at: datetime


class OrgInvitationAccept(BaseModel):
    """Request body for accepting an organization invitation via token."""

    token: str
