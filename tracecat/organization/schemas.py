from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from tracecat.authz.enums import OrgRole
from tracecat.identifiers import (
    OrganizationID,
    ScheduleUUID,
    UserID,
    WorkflowID,
    WorkspaceID,
)
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


class OrgScheduleTemporalStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"


class OrgScheduleTemporalItem(BaseModel):
    schedule_id: ScheduleUUID
    workspace_id: WorkspaceID
    workspace_name: str
    workflow_id: WorkflowID | None = None
    workflow_title: str | None = None
    db_status: Literal["online", "offline"]
    temporal_status: OrgScheduleTemporalStatus
    last_checked_at: datetime
    error: str | None = None


class OrgScheduleTemporalSummary(BaseModel):
    total_schedules: int
    present_count: int
    missing_count: int


class OrgScheduleTemporalSyncRead(BaseModel):
    summary: OrgScheduleTemporalSummary
    items: list[OrgScheduleTemporalItem]


class OrgScheduleRecreateMissingRequest(BaseModel):
    schedule_ids: list[ScheduleUUID] | None = None


class OrgScheduleRecreateAction(StrEnum):
    CREATED = "created"
    SKIPPED_PRESENT = "skipped_present"
    FAILED = "failed"


class OrgScheduleRecreateResult(BaseModel):
    schedule_id: ScheduleUUID
    action: OrgScheduleRecreateAction
    error: str | None = None


class OrgScheduleRecreateResponse(BaseModel):
    processed_count: int
    created_count: int
    already_present_count: int
    failed_count: int
    results: list[OrgScheduleRecreateResult] = Field(default_factory=list)


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

    Excludes sensitive fields like email, invited_by ID, and timestamps
    to reduce information disclosure when querying by token.
    """

    organization_id: OrganizationID
    organization_name: str
    inviter_name: str | None
    inviter_email: str | None
    role: OrgRole
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
