from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr, field_validator

from tracecat.secrets.enums import SecretType

SYNCABLE_SECRET_TYPES = frozenset(
    {
        SecretType.CUSTOM,
        SecretType.SSH_KEY,
        SecretType.MTLS,
        SecretType.CA_CERT,
    }
)


class CredentialSyncProvider(StrEnum):
    AWS = "aws"


class CredentialSyncOperation(StrEnum):
    PUSH = "push"
    PULL = "pull"


class AwsCredentialSyncConfig(BaseModel):
    region: str = Field(min_length=1, max_length=255)
    secret_prefix: str = Field(min_length=1, max_length=512)
    access_key_id: str = Field(min_length=1, max_length=512)
    secret_access_key: str = Field(min_length=1, max_length=2048)
    session_token: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("region", "secret_prefix", "access_key_id", "secret_access_key")
    @classmethod
    def validate_required_str(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value cannot be empty")
        return stripped

    @field_validator("session_token")
    @classmethod
    def validate_optional_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AwsCredentialSyncConfigRead(BaseModel):
    region: str | None = None
    secret_prefix: str | None = None
    has_access_key_id: bool = False
    has_secret_access_key: bool = False
    has_session_token: bool = False
    is_configured: bool = False
    is_corrupted: bool = False


class AwsCredentialSyncConfigUpdate(BaseModel):
    region: str | None = Field(default=None, min_length=1, max_length=255)
    secret_prefix: str | None = Field(default=None, min_length=1, max_length=512)
    access_key_id: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    secret_access_key: SecretStr | None = Field(
        default=None, min_length=1, max_length=2048
    )
    session_token: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("region", "secret_prefix", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator(
        "access_key_id", "secret_access_key", "session_token", mode="before"
    )
    @classmethod
    def normalize_optional_secret(
        cls, value: SecretStr | str | None
    ) -> SecretStr | None:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            secret_value = value.get_secret_value().strip()
        else:
            secret_value = value.strip()
        return SecretStr(secret_value) if secret_value else None


class CredentialSyncErrorItem(BaseModel):
    secret_name: str
    environment: str | None = None
    remote_name: str | None = None
    message: str


class CredentialSyncResult(BaseModel):
    provider: CredentialSyncProvider
    operation: CredentialSyncOperation
    success: bool
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[CredentialSyncErrorItem] = Field(default_factory=list)


class SyncedSecretKeyValue(BaseModel):
    key: str = Field(min_length=1, max_length=255)
    value: str


class SyncedSecretPayload(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=100)
    type: SecretType
    description: str | None = Field(default=None, min_length=0, max_length=1000)
    tags: dict[str, str] | None = None
    keys: list[SyncedSecretKeyValue] = Field(min_length=1, max_length=100)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: SecretType) -> SecretType:
        if value not in SYNCABLE_SECRET_TYPES:
            raise ValueError(f"Unsupported secret type for sync: {value}")
        return value
