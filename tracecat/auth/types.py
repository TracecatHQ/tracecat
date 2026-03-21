import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracecat.identifiers import InternalServiceID, OrganizationID, UserID, WorkspaceID


class Role(BaseModel):
    """The identity, intrinsic bindings, and resolved authorization context.

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

    model_config = ConfigDict(extra="allow")

    type: Literal["user", "service", "service_account"] = Field(frozen=True)
    """The type of role."""
    workspace_id: WorkspaceID | None = Field(default=None, frozen=True)
    """The effective workspace context for this request after auth resolution."""
    bound_workspace_id: WorkspaceID | None = Field(default=None, frozen=True)
    """The intrinsic workspace binding of the actor, if any."""
    organization_id: OrganizationID | None = Field(default=None, frozen=True)
    """The organization this role belongs to."""
    user_id: UserID | None = Field(default=None, frozen=True)
    """The user's ID, or the service's user_id. Can be None for internal services."""
    service_account_id: uuid.UUID | None = Field(default=None, frozen=True)
    """The service account's ID, if this is a service_account role."""
    service_id: InternalServiceID = Field(frozen=True)
    """The service's role name, or None if the role is a user."""
    is_platform_superuser: bool = Field(default=False, frozen=True)
    """Whether this role belongs to a platform superuser (User.is_superuser=True)."""
    scopes: frozenset[str] | None = Field(default=None, frozen=True)
    """Effective scopes for this role. None means unresolved/unset."""

    @model_validator(mode="after")
    def validate_service_account_shape(self) -> Self:
        """Enforce the required identity shape and workspace provenance rules."""
        match self:
            case Role(type="service_account", organization_id=None):
                raise ValueError("service_account roles require organization_id")
            case Role(type="service_account", service_account_id=None):
                raise ValueError("service_account roles require service_account_id")
            case Role(type="service_account", user_id=user_id) if user_id is not None:
                raise ValueError("service_account roles must not set user_id")
            case Role(
                bound_workspace_id=bound_workspace_id,
                workspace_id=workspace_id,
            ) if bound_workspace_id is not None and workspace_id != bound_workspace_id:
                raise ValueError("bound_workspace_id must match workspace_id")
            case _:
                return self

    @property
    def is_superuser(self) -> bool:
        """Check if this role has superuser (platform admin) privileges."""
        return self.is_platform_superuser

    @property
    def is_privileged(self) -> bool:
        """Check if this role has elevated privileges (platform admin).

        Platform superusers and organization owners/admins are considered
        privileged for organization-level operations.
        All other authorization is scope-based via RBAC.
        """
        if self.is_platform_superuser:
            return True
        if not self.scopes:
            return False
        return "org:workspace:read" in self.scopes

    @property
    def actor_id(self) -> UserID | None:
        """Return the auditable actor identifier for this role, if present."""
        if self.type == "service_account":
            if self.service_account_id is not None:
                return self.service_account_id
        return self.user_id

    def to_headers(self) -> dict[str, str]:
        headers = {
            "x-tracecat-role-type": self.type,
            "x-tracecat-role-service-id": self.service_id,
        }
        if self.user_id is not None:
            headers["x-tracecat-role-user-id"] = str(self.user_id)
        if self.workspace_id is not None:
            headers["x-tracecat-role-workspace-id"] = str(self.workspace_id)
        if self.bound_workspace_id is not None:
            headers["x-tracecat-role-bound-workspace-id"] = str(self.bound_workspace_id)
        if self.organization_id is not None:
            headers["x-tracecat-role-organization-id"] = str(self.organization_id)
        if self.scopes:
            headers["x-tracecat-role-scopes"] = ",".join(sorted(self.scopes))
        if self.service_account_id is not None:
            headers["x-tracecat-role-service-account-id"] = str(self.service_account_id)
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

    @property
    def is_platform_superuser(self) -> bool:
        """Platform roles always have superuser privileges."""
        return True

    @property
    def actor_id(self) -> UserID:
        return self.user_id


def system_role() -> Role:
    """Role for system actions with platform superuser privileges."""
    return Role(
        type="service",
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )
