from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr

from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.invitations.schemas import InvitationGrant

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
    # Populated for invited rows only: the grants the invitation will confer.
    grants: list[InvitationGrant] = []
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


type MemberRoleSourceType = Literal["direct", "group", "idp_group"]


class MemberRoleSource(BaseModel):
    """A direct assignment or group through which a member holds a role."""

    type: MemberRoleSourceType
    group_id: UUID | None = None
    group_name: str | None = None
    external_group_id: UUID | None = None
    external_group_display_name: str | None = None


class MemberRoleRead(BaseModel):
    """A member's role in one workspace or organization, with its sources."""

    role_id: UUID
    role_name: str
    workspace_id: WorkspaceID | None
    sources: list[MemberRoleSource]


class MemberAccessTrace(BaseModel):
    """A member's roles and the sources of each role."""

    user_id: UserID
    roles: list[MemberRoleRead]


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
