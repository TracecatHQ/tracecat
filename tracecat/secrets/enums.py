from enum import StrEnum


class SecretType(StrEnum):
    """The type of a secret."""

    CUSTOM = "custom"
    SSH_KEY = "ssh_key"
    MTLS = "mtls"
    CA_CERT = "ca_cert"
    GITHUB_APP = "github_app"


class SecretSource(StrEnum):
    """Where a workspace secret's values live.

    Every external member has the same value as its store's
    :class:`SecretStoreProvider`, so rows, API responses and synced YAML name
    the provider.
    """

    LOCAL = "local"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    BITWARDEN_SECRETS_MANAGER = "bitwarden_secrets_manager"


def is_external_source(source: str | None) -> bool:
    """Whether a ``secret.source`` value names an external store provider."""
    return (source or SecretSource.LOCAL) != SecretSource.LOCAL


class SecretStoreProvider(StrEnum):
    """Supported external secret store providers."""

    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    BITWARDEN_SECRETS_MANAGER = "bitwarden_secrets_manager"


class SecretMappingMode(StrEnum):
    """How an external secret value maps onto declared secret keys."""

    WHOLE_STRING = "whole_string"
    JSON = "json"


class SecretResolutionErrorCode(StrEnum):
    """Sanitized failure classes for external secret resolution.

    Values appear in API responses and the generated client, so existing
    members are never renamed or merged; providers add codes instead.
    """

    STORE_DISABLED = "store_disabled"
    STORE_NOT_AUTHORIZED = "store_not_authorized"
    ASSUME_ROLE_FAILED = "assume_role_failed"
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    DECRYPTION_FAILED = "decryption_failed"
    THROTTLED = "throttled"
    TIMEOUT = "timeout"
    BINARY_VALUE = "binary_value"
    MALFORMED_JSON = "malformed_json"
    MISSING_FIELD = "missing_field"
    NON_STRING_FIELD = "non_string_field"
    INVALID_MAPPING = "invalid_mapping"
    REGION_MISMATCH = "region_mismatch"
    UNKNOWN = "unknown"
    AUTH_FAILED = "auth_failed"
    UNAVAILABLE = "unavailable"
    INVALID_REFERENCE = "invalid_reference"
    UNSUPPORTED_VALUE = "unsupported_value"
    CREDENTIAL_MISSING = "credential_missing"
    PROVIDER_NOT_INSTALLED = "provider_not_installed"
