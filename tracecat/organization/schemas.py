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


type PathSource = Literal["direct", "group", "idp_group"]


class MemberAccessPath(BaseModel):
    """One route by which a member holds a role, with the rows behind it."""

    source: PathSource
    workspace_id: WorkspaceID | None
    role_id: UUID
    role_name: str
    group_id: UUID | None = None
    group_name: str | None = None
    external_group_id: UUID | None = None
    external_group_display_name: str | None = None


class MemberAccessExplain(BaseModel):
    """Every path a member holds, for answering "why does she have this?"."""

    user_id: UserID
    paths: list[MemberAccessPath]


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
