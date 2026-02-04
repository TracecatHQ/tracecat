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
    """Source/ownership of a scope definition."""

    PLATFORM = "platform"  # Platform-owned: core permissions + registry-derived
    CUSTOM = "custom"  # Organization-defined scopes


class OrgRole(StrEnum):
    """Organization-level roles."""

    MEMBER = "member"  # Basic org member
    ADMIN = "admin"  # Can manage org settings, workspaces, invite users
    OWNER = "owner"  # Full control, billing, can delete org
