from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.identifiers import InternalServiceID, OrganizationID, UserID, WorkspaceID


class AccessLevel(StrEnum):
    BASIC = "BASIC"
    ADMIN = "ADMIN"


class Role(BaseModel):
    """The identity and authorization of a user or service.

    Params
    ------
    type : Literal["user", "service"]
        The type of role.
    user_id : UUID | None
        The user's ID, or the service's user_id.
        This can be None for internal services, or when a user hasn't been set for the role.
    service_id : str | None = None
        The service's role name, or None if the role is a user.


    User roles
    ----------
    - User roles are authenticated via JWT.
    - The `user_id` is the user's JWT 'sub' claim.
    - User roles do not have an associated `service_id`, this must be None.

    Service roles
    -------------
    - Service roles are authenticated via API key.
    - Used for internal services to authenticate with the API.
    - A service's `user_id` is the user it's acting on behalf of. This can be None for internal services.
    """

    type: Literal["user", "service"] = Field(frozen=True)
    workspace_id: WorkspaceID | None = Field(default=None, frozen=True)
    organization_id: OrganizationID | None = Field(default=None, frozen=True)
    workspace_role: WorkspaceRole | None = Field(default=None, frozen=True)
    org_role: OrgRole | None = Field(default=None, frozen=True)
    user_id: UserID | None = Field(default=None, frozen=True)
    service_id: InternalServiceID = Field(frozen=True)
    access_level: AccessLevel = Field(default=AccessLevel.BASIC, frozen=True)
    is_platform_superuser: bool = Field(default=False, frozen=True)
    """Whether this role belongs to a platform superuser (User.is_superuser=True)."""
    scopes: frozenset[str] = Field(default=frozenset(), frozen=True)
    """Effective scopes for this role. Computed during authentication."""

    @property
    def is_superuser(self) -> bool:
        """Check if this role has superuser (platform admin) privileges."""
        return self.is_platform_superuser

    @property
    def is_org_admin(self) -> bool:
        """Check if this role has org owner/admin privileges.

        Org owners and admins can access all workspaces in their organization
        without explicit workspace membership.
        """
        return self.org_role in (OrgRole.OWNER, OrgRole.ADMIN)

    @property
    def is_privileged(self) -> bool:
        """Check if this role has elevated privileges (platform admin or org admin).

        Privileged roles bypass workspace membership checks.
        """
        return self.is_platform_superuser or self.is_org_admin

    def to_headers(self) -> dict[str, str]:
        headers = {
            "x-tracecat-role-type": self.type,
            "x-tracecat-role-service-id": self.service_id,
            "x-tracecat-role-access-level": self.access_level.value,
        }
        if self.user_id is not None:
            headers["x-tracecat-role-user-id"] = str(self.user_id)
        if self.workspace_id is not None:
            headers["x-tracecat-role-workspace-id"] = str(self.workspace_id)
        if self.organization_id is not None:
            headers["x-tracecat-role-organization-id"] = str(self.organization_id)
        if self.workspace_role is not None:
            headers["x-tracecat-role-workspace-role"] = self.workspace_role.value
        if self.org_role is not None:
            headers["x-tracecat-role-org-role"] = self.org_role.value
        if self.scopes:
            headers["x-tracecat-role-scopes"] = ",".join(sorted(self.scopes))
        return headers


class PlatformRole(BaseModel):
    """Role for platform admin (superuser) operations.

    Used for admin endpoints that operate at the platform level,
    not scoped to any organization or workspace.

    The user_id is preserved for audit logging purposes.
    """

    type: Literal["user", "service"] = Field(frozen=True)
    user_id: UserID = Field(frozen=True)
    """The superuser's ID - required for audit logging."""
    service_id: InternalServiceID = Field(frozen=True)
    access_level: AccessLevel = Field(default=AccessLevel.ADMIN, frozen=True)

    @property
    def is_platform_superuser(self) -> bool:
        """Platform roles always have superuser privileges."""
        return True


def system_role() -> Role:
    """Role for system actions with platform superuser privileges."""
    return Role(
        type="service",
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )
