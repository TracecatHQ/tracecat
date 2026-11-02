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


class ScimConnectionStatus(StrEnum):
    """Whether the provider's pushes admit users yet."""

    PENDING = "pending"  # Accepting pushes; nothing admitted until reviewed
    ACTIVE = "active"  # Pushes admit members and apply mappings
    DISABLED = "disabled"  # Reserved for future connection disabling
