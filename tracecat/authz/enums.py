from enum import StrEnum


class OwnerType(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"
    ORGANIZATION = "organization"


class WorkspaceRole(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class ScopeSource(StrEnum):
    """Source of a scope definition."""

    SYSTEM = "system"  # Built-in platform scopes (org/workspace/resources/RBAC admin)
    REGISTRY = "registry"  # Derived from registry actions
    CUSTOM = "custom"  # User-created scopes


class OrgRole(StrEnum):
    """Organization-level roles."""

    MEMBER = "member"  # Basic org member
    ADMIN = "admin"  # Can manage org settings, workspaces, invite users
    OWNER = "owner"  # Full control, billing, can delete org
