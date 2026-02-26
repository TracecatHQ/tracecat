from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, EmailStr

from tracecat.identifiers import OrganizationID, UserID, WorkspaceID

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
    token: str | None = None


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


# Memberships


class UserWorkspaceMembership(BaseModel):
    """A user's workspace membership with role info."""

    workspace_id: WorkspaceID
    workspace_name: str
    role_name: str
