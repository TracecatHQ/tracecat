from enum import StrEnum


class OwnerType(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"
    ORGANIZATION = "organization"


class WorkspaceRole(StrEnum):
    # VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"
