from enum import StrEnum


class SecretLevel(StrEnum):
    """The level of a secret."""

    WORKSPACE = "workspace"
    ORGANIZATION = "organization"


class SecretType(StrEnum):
    """The type of a secret."""

    CUSTOM = "custom"
    SSH_KEY = "ssh-key"
