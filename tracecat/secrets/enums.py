from enum import StrEnum


class SecretType(StrEnum):
    """The type of a secret."""

    CUSTOM = "custom"
    SSH_KEY = "ssh-key"
