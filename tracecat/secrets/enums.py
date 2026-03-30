from enum import StrEnum


class SecretType(StrEnum):
    """The type of a secret."""

    CUSTOM = "custom"
    SSH_KEY = "ssh_key"
    MTLS = "mtls"
    CA_CERT = "ca_cert"
    GITHUB_APP = "github_app"
