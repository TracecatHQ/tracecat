from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_ssh_private_key,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from tracecat.db.models import OrganizationSecret, Secret
from tracecat.identifiers import OrganizationID, SecretID, WorkspaceID
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.enums import SecretType

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""

SSHKeyTarget = Literal["registry"]
SSH_PRIVATE_KEY_NAME = "PRIVATE_KEY"
TLS_CERTIFICATE_NAME = "TLS_CERTIFICATE"
TLS_PRIVATE_KEY_NAME = "TLS_PRIVATE_KEY"
CA_CERTIFICATE_NAME = "CA_CERTIFICATE"


class SecretKeyValue(BaseModel):
    key: str
    value: SecretStr

    @staticmethod
    def from_str(kv: str) -> SecretKeyValue:
        key, value = kv.split("=", 1)
        return SecretKeyValue(key=key, value=SecretStr(value))


def _normalize_pem_value(value: str, *, required_message: str) -> str:
    normalized = value.strip().replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        raise ValueError(required_message)
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def validate_ssh_private_key(value: str) -> str:
    normalized = _normalize_pem_value(
        value, required_message="SSH private key is required."
    )
    key_bytes = normalized.encode("utf-8")
    for loader in (load_ssh_private_key, load_pem_private_key):
        try:
            loader(key_bytes, password=None)
            return normalized
        except (ValueError, TypeError, UnsupportedAlgorithm):
            continue

    raise ValueError(
        "Invalid SSH private key format. Expected an unencrypted OpenSSH or PEM key."
    )


def _validate_pem_private_key(value: str, *, label: str) -> str:
    normalized = _normalize_pem_value(value, required_message=f"{label} is required.")
    try:
        load_pem_private_key(normalized.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise ValueError(
            f"Invalid {label} format. Expected an unencrypted PEM key."
        ) from exc
    return normalized


def _validate_pem_certificates(value: str, *, label: str) -> str:
    normalized = _normalize_pem_value(value, required_message=f"{label} is required.")
    cert_bytes = normalized.encode("utf-8")
    try:
        if hasattr(x509, "load_pem_x509_certificates"):
            certs = x509.load_pem_x509_certificates(cert_bytes)
            if not certs:
                raise ValueError("No certificates found")
        else:
            x509.load_pem_x509_certificate(cert_bytes)
    except ValueError as exc:
        raise ValueError(
            f"Invalid {label} format. Expected PEM-encoded certificate(s)."
        ) from exc
    return normalized


def _validate_keyset(
    keys: list[SecretKeyValue],
    required_keys: list[str],
    *,
    count_error: str,
    key_name_error: str,
) -> dict[str, SecretKeyValue]:
    if len(keys) != len(required_keys):
        raise ValueError(count_error)
    key_map = {kv.key: kv for kv in keys}
    if set(key_map) != set(required_keys):
        raise ValueError(key_name_error)
    return key_map


def validate_ssh_key_values(keys: list[SecretKeyValue]) -> None:
    key_map = _validate_keyset(
        keys,
        [SSH_PRIVATE_KEY_NAME],
        count_error="SSH key secrets must contain exactly one key value.",
        key_name_error=f"SSH key secrets must use the {SSH_PRIVATE_KEY_NAME!r} key name.",
    )
    normalized = validate_ssh_private_key(
        key_map[SSH_PRIVATE_KEY_NAME].value.get_secret_value()
    )
    key_map[SSH_PRIVATE_KEY_NAME].value = SecretStr(normalized)


def validate_mtls_key_values(keys: list[SecretKeyValue]) -> None:
    key_map = _validate_keyset(
        keys,
        [TLS_CERTIFICATE_NAME, TLS_PRIVATE_KEY_NAME],
        count_error="mTLS secrets must contain exactly two key values.",
        key_name_error=(
            "mTLS secrets must use the "
            f"{TLS_CERTIFICATE_NAME!r} and {TLS_PRIVATE_KEY_NAME!r} key names."
        ),
    )
    cert_value = _validate_pem_certificates(
        key_map[TLS_CERTIFICATE_NAME].value.get_secret_value(),
        label="TLS certificate",
    )
    key_value = _validate_pem_private_key(
        key_map[TLS_PRIVATE_KEY_NAME].value.get_secret_value(),
        label="TLS private key",
    )
    key_map[TLS_CERTIFICATE_NAME].value = SecretStr(cert_value)
    key_map[TLS_PRIVATE_KEY_NAME].value = SecretStr(key_value)


def validate_ca_cert_values(keys: list[SecretKeyValue]) -> None:
    key_map = _validate_keyset(
        keys,
        [CA_CERTIFICATE_NAME],
        count_error="CA certificate secrets must contain exactly one key value.",
        key_name_error=(
            f"CA certificate secrets must use the {CA_CERTIFICATE_NAME!r} key name."
        ),
    )
    cert_value = _validate_pem_certificates(
        key_map[CA_CERTIFICATE_NAME].value.get_secret_value(),
        label="CA certificate",
    )
    key_map[CA_CERTIFICATE_NAME].value = SecretStr(cert_value)


class SecretBase(BaseModel):
    """Base class for secrets."""

    @classmethod
    def factory(cls, type: SecretType) -> type[SecretBase]:
        if type not in _SECRET_FACTORY:
            raise ValueError(f"Invalid secret type {type!r}")
        return _SECRET_FACTORY[type]


class CustomSecret(SecretBase):
    model_config = ConfigDict(extra="allow")


# class TokenSecret(SecretBase):
#     token: str


# class OAuth2Secret(SecretBase):
#     client_id: str
#     client_secret: str
#     redirect_uri: str


SecretVariant = CustomSecret  # | TokenSecret | OAuth2Secret
_SECRET_FACTORY: dict[SecretType, type[SecretBase]] = {
    SecretType.CUSTOM: CustomSecret,
    # "token": TokenSecret,
    # "oauth2": OAuth2Secret,
}


class SecretCreate(BaseModel):
    """Create a new secret.

    Secret types
    ------------
    - `custom`: Arbitrary user-defined types
    - `token`: A token, e.g. API Key, JWT Token (TBC)
    - `oauth2`: OAuth2 Client Credentials (TBC)
    - `mtls`: TLS client certificate and key
    - `ca-cert`: Certificate authority bundle"""

    type: SecretType = SecretType.CUSTOM
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=0, max_length=1000)
    keys: list[SecretKeyValue] = Field(..., min_length=1, max_length=100)
    tags: dict[str, str] | None = None
    environment: str = DEFAULT_SECRETS_ENVIRONMENT

    @staticmethod
    def from_strings(name: str, keyvalues: list[str]) -> SecretCreate:
        keys = [SecretKeyValue.from_str(kv) for kv in keyvalues]
        return SecretCreate(name=name, keys=keys)

    @field_validator("keys")
    def validate_keys(cls, v, values):
        if not v:
            raise ValueError("Keys cannot be empty")
        # Ensure keys are unique
        if len({kv.key for kv in v}) != len(v):
            raise ValueError("Keys must be unique")
        return v

    @model_validator(mode="after")
    def validate_typed_secret(self) -> SecretCreate:
        if self.type == SecretType.SSH_KEY:
            validate_ssh_key_values(self.keys)
        elif self.type == SecretType.MTLS:
            validate_mtls_key_values(self.keys)
        elif self.type == SecretType.CA_CERT:
            validate_ca_cert_values(self.keys)
        return self


class SecretUpdate(BaseModel):
    """Update a secret.

    Secret types
    ------------
    - `custom`: Arbitrary user-defined types
    - `token`: A token, e.g. API Key, JWT Token (TBC)
    - `oauth2`: OAuth2 Client Credentials (TBC)
    - `mtls`: TLS client certificate and key
    - `ca-cert`: Certificate authority bundle"""

    type: SecretType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=0, max_length=1000)
    keys: list[SecretKeyValue] | None = Field(
        default=None, min_length=1, max_length=100
    )
    tags: dict[str, str] | None = Field(default=None, min_length=0, max_length=1000)
    environment: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_typed_secret(self) -> SecretUpdate:
        if self.type == SecretType.SSH_KEY and self.keys is not None:
            validate_ssh_key_values(self.keys)
        elif self.type == SecretType.MTLS and self.keys is not None:
            validate_mtls_key_values(self.keys)
        elif self.type == SecretType.CA_CERT and self.keys is not None:
            validate_ca_cert_values(self.keys)
        return self


class SecretSearch(BaseModel):
    names: set[str] | None = None
    ids: set[SecretID] | None = None
    environment: str
    workspace_ids: set[UUID] | None = None
    types: set[SecretType] | None = None


class SecretReadMinimal(BaseModel):
    id: UUID
    type: SecretType
    name: str
    description: str | None = None
    keys: list[str]
    environment: str
    is_corrupted: bool = False


class SecretReadBase(BaseModel):
    """Base read schema for secrets."""

    id: UUID
    type: SecretType
    name: str
    description: str | None = None
    encrypted_keys: bytes
    environment: str
    tags: dict[str, str] | None = None
    created_at: datetime
    updated_at: datetime


class SecretRead(SecretReadBase):
    """Read schema for workspace-scoped secrets."""

    workspace_id: WorkspaceID

    @staticmethod
    def from_database(obj: Secret) -> SecretRead:
        return SecretRead(
            id=obj.id,
            type=SecretType(obj.type),
            name=obj.name,
            description=obj.description,
            environment=obj.environment,
            workspace_id=obj.workspace_id,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            encrypted_keys=obj.encrypted_keys,
            tags=obj.tags,
        )


class OrganizationSecretRead(SecretReadBase):
    """Read schema for organization-scoped secrets."""

    organization_id: OrganizationID

    @staticmethod
    def from_database(obj: OrganizationSecret) -> OrganizationSecretRead:
        return OrganizationSecretRead(
            id=obj.id,
            type=SecretType(obj.type),
            name=obj.name,
            description=obj.description,
            environment=obj.environment,
            organization_id=obj.organization_id,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            encrypted_keys=obj.encrypted_keys,
            tags=obj.tags,
        )


class SecretDefinition(BaseModel):
    """Aggregated secret definition from registry actions."""

    name: str
    keys: list[str]
    optional_keys: list[str] | None = None
    optional: bool = False
    actions: list[str]
    action_count: int
