from enum import StrEnum


class SecretType(StrEnum):
    """The type of a secret."""

    CUSTOM = "custom"
    SSH_KEY = "ssh_key"
    MTLS = "mtls"
    CA_CERT = "ca_cert"
    GITHUB_APP = "github_app"


class SecretSource(StrEnum):
    """Where a workspace secret's values live."""

    LOCAL = "local"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"


class SecretStoreProvider(StrEnum):
    """Supported external secret store providers."""

    AWS_SECRETS_MANAGER = "aws_secrets_manager"


class AwsSecretMappingMode(StrEnum):
    """How an AWS Secrets Manager value maps onto declared secret keys."""

    WHOLE_STRING = "whole_string"
    JSON = "json"


class AwsSecretResolutionErrorCode(StrEnum):
    """Sanitized failure classes for AWS Secrets Manager resolution."""

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
