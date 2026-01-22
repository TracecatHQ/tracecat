from enum import StrEnum


class OwnerType(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"
    ORGANIZATION = "organization"


class WorkspaceRole(StrEnum):
    # VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class OrgRole(StrEnum):
    """Organization-level roles."""

    MEMBER = "member"  # Basic org member
    ADMIN = "admin"  # Can manage org settings, workspaces, invite users
    OWNER = "owner"  # Full control, billing, can delete org
