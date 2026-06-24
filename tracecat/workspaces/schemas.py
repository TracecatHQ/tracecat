from __future__ import annotations

from datetime import datetime
from typing import NotRequired, TypedDict

from pydantic import EmailStr, Field, computed_field, field_validator

from tracecat import config
from tracecat.core.schemas import Schema
from tracecat.git.constants import GIT_SSH_URL_REGEX
from tracecat.identifiers import InvitationID, OrganizationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.types import MAX_BULK_INVITE_EMAILS, BatchInviteStatus

# === Workspace === #


# DTO
class WorkspaceSettings(TypedDict):
    git_repo_url: NotRequired[str | None]
    workflow_unlimited_timeout_enabled: NotRequired[bool | None]
    workflow_default_timeout_seconds: NotRequired[int | None]
    allowed_attachment_extensions: NotRequired[list[str] | None]
    allowed_attachment_mime_types: NotRequired[list[str] | None]
    validate_attachment_magic_number: NotRequired[bool | None]


# Schema
class WorkspaceSettingsRead(Schema):
    git_repo_url: str | None = None
    workflow_unlimited_timeout_enabled: bool | None = None
    workflow_default_timeout_seconds: int | None = None
    allowed_attachment_extensions: list[str] | None = None
    allowed_attachment_mime_types: list[str] | None = None
    validate_attachment_magic_number: bool | None = None

    @computed_field
    @property
    def effective_allowed_attachment_extensions(self) -> list[str]:
        """Returns workspace-specific extensions if set, otherwise system defaults."""
        if self.allowed_attachment_extensions is not None:
            return self.allowed_attachment_extensions
        return sorted(config.TRACECAT__ALLOWED_ATTACHMENT_EXTENSIONS)

    @computed_field
    @property
    def effective_allowed_attachment_mime_types(self) -> list[str]:
        """Returns workspace-specific MIME types if set, otherwise system defaults."""
        if self.allowed_attachment_mime_types is not None:
            return self.allowed_attachment_mime_types
        return sorted(config.TRACECAT__ALLOWED_ATTACHMENT_MIME_TYPES)


class WorkspaceSettingsUpdate(Schema):
    git_repo_url: str | None = None
    workflow_unlimited_timeout_enabled: bool | None = Field(
        default=None,
        description="Allow workflows to run indefinitely without timeout constraints. When enabled, individual workflow timeout settings are ignored.",
    )
    workflow_default_timeout_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Default timeout in seconds for workflows in this workspace. Must be greater than or equal to 0.",
    )
    allowed_attachment_extensions: list[str] | None = Field(
        default=None,
        description="Allowed file extensions for attachments (e.g., ['.pdf', '.docx']). Overrides global defaults.",
    )
    allowed_attachment_mime_types: list[str] | None = Field(
        default=None,
        description="Allowed MIME types for attachments (e.g., ['application/pdf', 'image/jpeg']). Overrides global defaults.",
    )
    validate_attachment_magic_number: bool | None = Field(
        default=None,
        description="Whether to validate file content matches declared MIME type using magic number detection. Defaults to true for security.",
    )

    @field_validator("git_repo_url", mode="before")
    @classmethod
    def validate_git_repo_url(cls, value: str | None) -> str | None:
        """Ensure workspace git repo URLs use the shared Git SSH format."""
        if value is None:
            return value

        if not GIT_SSH_URL_REGEX.match(value):
            raise ValueError(
                "Must be a valid Git SSH URL (e.g., git+ssh://<user>@github.com/org/repo.git)"
            )

        return value


# Params
class WorkspaceCreate(Schema):
    name: str = Field(..., min_length=1, max_length=100)
    settings: WorkspaceSettingsUpdate | None = None
    organization_id: OrganizationID | None = None


class WorkspaceUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    settings: WorkspaceSettingsUpdate | None = None


class WorkspaceSearch(Schema):
    name: str | None = None


# Responses
class WorkspaceReadMinimal(Schema):
    id: WorkspaceID
    name: str


class WorkspaceMember(Schema):
    """An active workspace member backed by a real user account.

    Pending invitations are a separate concern: the members list endpoint
    returns only active members, and the UI merges in outstanding invitations
    (from the invitations endpoint) client-side for display.
    """

    user_id: UserID
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr
    role_name: str
    # Access reaches the workspace through a group; the UI gates per-row
    # remove/edit actions on this since they're managed via the group.
    via_group: bool = False


class WorkspaceRead(Schema):
    id: WorkspaceID
    name: str
    settings: WorkspaceSettingsRead | None = None
    organization_id: OrganizationID


WorkspaceSettingsRead.model_rebuild()
WorkspaceSettingsUpdate.model_rebuild()


# === Membership === #
class WorkspaceMembershipCreate(Schema):
    user_id: UserID


class WorkspaceMembershipRead(Schema):
    user_id: UserID
    workspace_id: WorkspaceID


# === Invitation === #
class WorkspaceInvitationCreate(Schema):
    """Request schema for creating a workspace invitation."""

    email: EmailStr
    role_id: str  # UUID as string for API compatibility


class WorkspaceInvitationBatchCreate(Schema):
    """Request schema for creating workspace invitations in bulk."""

    emails: list[EmailStr] = Field(min_length=1, max_length=MAX_BULK_INVITE_EMAILS)
    role_id: str  # UUID as string for API compatibility


class WorkspaceBatchInvitationItemResult(Schema):
    """Per-email outcome of a bulk workspace invitation request."""

    email: str
    status: BatchInviteStatus
    reason: str | None = None


class WorkspaceInvitationBatchResult(Schema):
    """Response schema for a bulk workspace invitation request."""

    results: list[WorkspaceBatchInvitationItemResult]
    created_count: int
    skipped_count: int


class WorkspaceInvitationTokenRead(Schema):
    """Response schema for a workspace invitation token (copy-link flow)."""

    token: str


class WorkspaceInvitationReadMinimal(Schema):
    """Minimal response for public token-based workspace invitation lookup.

    Excludes sensitive fields like email, invited_by ID, and timestamps to
    reduce information disclosure when querying by token. Mirrors the
    organization invitation lookup so the shared accept page can render either.
    """

    workspace_id: WorkspaceID
    workspace_name: str
    organization_id: OrganizationID
    organization_slug: str
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


class WorkspacePendingInvitationRead(Schema):
    """Pending workspace invitation visible to the invited authenticated user.

    Mirrors :class:`OrgPendingInvitationRead` so a post-signup invitations
    surface can render organization and workspace invites side by side.
    """

    token: str
    workspace_id: WorkspaceID
    workspace_name: str
    organization_id: OrganizationID
    organization_slug: str
    inviter_name: str | None
    inviter_email: str | None
    role_name: str
    role_slug: str | None = None
    expires_at: datetime


class WorkspaceInvitationAccept(Schema):
    """Request body for accepting a workspace invitation via token."""

    token: str


class WorkspaceInvitationRead(Schema):
    """Response schema for a workspace invitation."""

    id: InvitationID
    workspace_id: WorkspaceID
    email: EmailStr
    role_id: str
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    invited_by: UserID | None
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


class WorkspaceInvitationList(Schema):
    """Query params for listing workspace invitations."""

    status: InvitationStatus | None = None
