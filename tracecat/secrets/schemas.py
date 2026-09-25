from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import botocore.session
from botocore.exceptions import UnknownRegionError
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

from tracecat.auth.types import Role
from tracecat.db.models import (
    OrganizationSecret,
    OrganizationSecretStore,
    Secret,
    WorkspaceSecretStoreAuthorization,
)
from tracecat.identifiers import OrganizationID, SecretID, WorkspaceID
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.enums import (
    AwsSecretMappingMode,
    AwsSecretResolutionErrorCode,
    SecretSource,
    SecretStoreProvider,
    SecretType,
)

AWS_SECRET_ARN_PATTERN = (
    r"^arn:aws(?:-[a-z]+)*:secretsmanager:(?P<region>[a-z0-9-]+):\d{12}:secret:[^\s]+$"
)
AWS_SECRET_NAME_PATTERN = r"^[A-Za-z0-9/_+=.@-]{1,512}$"
"""Secrets Manager friendly name, as accepted by ``GetSecretValue.SecretId``."""
AWS_SECRET_ID_PATTERN = (
    r"^(?:arn:aws(?:-[a-z]+)*:secretsmanager:[a-z0-9-]+:\d{12}:secret:[^\s]+"
    r"|[A-Za-z0-9/_+=.@-]{1,512})$"
)
"""Either a full Secrets Manager ARN or a friendly secret name."""
EXPRESSION_SECRET_NAME_PATTERN = r"^[a-z_][a-z0-9_]*$"
"""Snake-case name addressable as ``SECRETS.<name>`` in expressions."""

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

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
    - `ca_cert`: Certificate authority bundle"""

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
    - `ca_cert`: Certificate authority bundle"""

    type: SecretType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=0, max_length=1000)
    keys: list[SecretKeyValue] | None = Field(
        default=None, min_length=1, max_length=100
    )
    tags: dict[str, str] | None = Field(default=None, min_length=0, max_length=1000)
    environment: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("name", "environment")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str | None:
        # Omit the field to leave it unchanged; the columns are NOT NULL.
        if value is None:
            raise ValueError("must not be null")
        return value

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
    source: SecretSource = SecretSource.LOCAL
    store_id: UUID | None = None
    store_name: str | None = None
    remote_reference: str | None = None


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
    source: SecretSource = SecretSource.LOCAL
    store_id: UUID | None = None
    remote_reference: str | None = None
    remote_key_mapping: AwsSecretKeyMapping | None = None

    @staticmethod
    def from_database(obj: Secret) -> SecretRead:
        mapping = (
            AwsSecretKeyMapping.model_validate(obj.remote_key_mapping)
            if obj.remote_key_mapping is not None
            else None
        )
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
            source=SecretSource(obj.source or SecretSource.LOCAL),
            store_id=obj.store_id,
            remote_reference=obj.remote_reference,
            remote_key_mapping=mapping,
        )


# === External secret stores (AWS Secrets Manager) ===
SecretKey = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")]

AWS_ROLE_ARN_PATTERN = r"^arn:aws(?:-[a-z]+)*:iam::\d{12}:role/[\w+=,.@/-]+$"
AWS_REGION_PATTERN = r"^[a-z]{2}(?:-[a-z]+)+-\d$"


def check_aws_partition(role_arn: str, region: str) -> None:
    """Reject a role ARN from a different AWS partition than the region."""
    try:
        partition = botocore.session.get_session().get_partition_for_region(region)
    except UnknownRegionError:
        return  # Region is newer than the pinned botocore; let AWS decide.
    if role_arn.split(":")[1] != partition:
        raise ValueError(
            f"Role ARN partition must be {partition!r} for region {region!r}"
        )


class AwsSecretJsonField(BaseModel):
    """One declared output key sourced from a top-level JSON field."""

    model_config = ConfigDict(frozen=True)

    key: SecretKey = Field(..., min_length=1, max_length=255)
    field: str = Field(..., min_length=1, max_length=255)


class AwsSecretKeyMapping(BaseModel):
    """Declares how a remote AWS secret value maps onto output keys.

    ``whole_string`` maps the entire ``SecretString`` onto exactly one key.
    ``json`` maps selected top-level string fields onto declared keys.
    """

    model_config = ConfigDict(frozen=True)

    mode: AwsSecretMappingMode
    keys: list[SecretKey] = Field(default_factory=list, max_length=100)
    fields: list[AwsSecretJsonField] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_mapping(self) -> AwsSecretKeyMapping:
        if self.mode == AwsSecretMappingMode.WHOLE_STRING:
            if len(self.keys) != 1 or self.fields:
                raise ValueError(
                    "whole_string mappings declare exactly one output key and no fields"
                )
            return self
        if not self.fields or self.keys:
            raise ValueError("json mappings declare at least one field and no keys")
        output_keys = [entry.key for entry in self.fields]
        if len(set(output_keys)) != len(output_keys):
            raise ValueError("Output keys must be unique")
        return self

    def output_keys(self) -> list[str]:
        """Return the declared output key names without touching AWS."""
        if self.mode == AwsSecretMappingMode.WHOLE_STRING:
            return list(self.keys)
        return [entry.key for entry in self.fields]


class AwsSecretsManagerStoreConfig(BaseModel):
    """Persisted provider configuration for an AWS Secrets Manager store."""

    model_config = ConfigDict(frozen=True)

    provider: Literal[SecretStoreProvider.AWS_SECRETS_MANAGER] = (
        SecretStoreProvider.AWS_SECRETS_MANAGER
    )
    role_arn: str = Field(..., pattern=AWS_ROLE_ARN_PATTERN, max_length=2048)
    region: str = Field(..., pattern=AWS_REGION_PATTERN, max_length=64)
    external_id: str = Field(..., min_length=1, max_length=255)


class AwsSecretsManagerStoreCreate(BaseModel):
    """Client-supplied fields when creating an AWS Secrets Manager store."""

    provider: Literal[SecretStoreProvider.AWS_SECRETS_MANAGER] = (
        SecretStoreProvider.AWS_SECRETS_MANAGER
    )
    role_arn: str = Field(..., pattern=AWS_ROLE_ARN_PATTERN, max_length=2048)
    region: str = Field(..., pattern=AWS_REGION_PATTERN, max_length=64)

    @model_validator(mode="after")
    def validate_partition(self) -> AwsSecretsManagerStoreCreate:
        check_aws_partition(self.role_arn, self.region)
        return self


class AwsSecretsManagerStoreUpdate(BaseModel):
    """Client-supplied fields when updating an AWS Secrets Manager store."""

    role_arn: str | None = Field(
        default=None, pattern=AWS_ROLE_ARN_PATTERN, max_length=2048
    )
    region: str | None = Field(default=None, pattern=AWS_REGION_PATTERN, max_length=64)


# Becomes a discriminated union on `provider` when a second provider lands.
SecretStoreConfig = AwsSecretsManagerStoreConfig
# Becomes a discriminated union on `provider` when a second provider lands.
SecretStoreCreateConfig = AwsSecretsManagerStoreCreate
# Becomes a discriminated union on `provider` when a second provider lands.
SecretStoreUpdateConfig = AwsSecretsManagerStoreUpdate


class SecretStoreCreate(BaseModel):
    """Create an organization-owned external secret store."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    provider: SecretStoreProvider = SecretStoreProvider.AWS_SECRETS_MANAGER
    config: SecretStoreCreateConfig
    enabled: bool = True
    all_workspaces: bool = Field(
        default=False, description="Allow all current and future workspaces."
    )


class SecretStoreUpdate(BaseModel):
    """Update an organization-owned secret store. Server-owned fields are immutable."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    config: SecretStoreUpdateConfig | None = None
    enabled: bool | None = None
    all_workspaces: bool = Field(
        default=False, description="Allow all current and future workspaces."
    )

    @field_validator("name", "enabled")
    @classmethod
    def reject_explicit_null(cls, value: str | bool | None) -> str | bool | None:
        # Omit the field to leave it unchanged; the columns are NOT NULL.
        if value is None:
            raise ValueError("must not be null")
        return value


class SecretStoreRead(BaseModel):
    """Organization view of a secret store, including trust-policy inputs."""

    id: UUID
    organization_id: OrganizationID
    name: str
    description: str | None = None
    provider: SecretStoreProvider
    config: SecretStoreConfig
    enabled: bool
    all_workspaces: bool
    tracecat_aws_account_id: str | None = None
    tracecat_aws_principal_arn: str | None = None
    authorized_workspace_ids: list[WorkspaceID] = Field(default_factory=list)
    reference_count: int = 0
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_database(
        obj: OrganizationSecretStore,
        *,
        authorized_workspace_ids: list[WorkspaceID],
        reference_count: int,
        tracecat_aws_account_id: str | None,
        tracecat_aws_principal_arn: str | None,
    ) -> SecretStoreRead:
        return SecretStoreRead(
            id=obj.id,
            organization_id=obj.organization_id,
            name=obj.name,
            description=obj.description,
            provider=SecretStoreProvider(obj.provider),
            config=SecretStoreConfig.model_validate(obj.config),
            enabled=obj.enabled,
            all_workspaces=obj.all_workspaces,
            tracecat_aws_account_id=tracecat_aws_account_id,
            tracecat_aws_principal_arn=tracecat_aws_principal_arn,
            authorized_workspace_ids=authorized_workspace_ids,
            reference_count=reference_count,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )


class SecretStoreAuthorizationCreate(BaseModel):
    """Authorize a workspace to reference a store."""

    workspace_id: WorkspaceID


class SecretStoreAuthorizationRead(BaseModel):
    id: UUID
    store_id: UUID
    workspace_id: WorkspaceID
    created_at: datetime

    @staticmethod
    def from_database(
        obj: WorkspaceSecretStoreAuthorization,
    ) -> SecretStoreAuthorizationRead:
        return SecretStoreAuthorizationRead(
            id=obj.id,
            store_id=obj.store_id,
            workspace_id=obj.workspace_id,
            created_at=obj.created_at,
        )


class WorkspaceSecretStoreRead(BaseModel):
    """Workspace view of an authorized store. Never exposes the external ID."""

    id: UUID
    name: str
    description: str | None = None
    provider: SecretStoreProvider
    region: str
    enabled: bool

    @staticmethod
    def from_database(obj: OrganizationSecretStore) -> WorkspaceSecretStoreRead:
        return WorkspaceSecretStoreRead(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            provider=SecretStoreProvider(obj.provider),
            region=SecretStoreConfig.model_validate(obj.config).region,
            enabled=obj.enabled,
        )


class AwsSecretReferenceCreate(BaseModel):
    """Create a workspace custom secret backed by AWS Secrets Manager."""

    name: str = Field(..., max_length=100, pattern=EXPRESSION_SECRET_NAME_PATTERN)
    description: str | None = Field(default=None, min_length=0, max_length=255)
    environment: str = Field(
        default=DEFAULT_SECRETS_ENVIRONMENT, min_length=1, max_length=100
    )
    tags: dict[str, str] | None = None
    store_id: UUID
    remote_reference: str = Field(..., pattern=AWS_SECRET_ID_PATTERN, max_length=2048)
    key_mapping: AwsSecretKeyMapping


class AwsSecretReferenceUpdate(BaseModel):
    """Update an AWS-backed workspace secret. Values are never accepted."""

    name: str | None = Field(
        default=None, max_length=100, pattern=EXPRESSION_SECRET_NAME_PATTERN
    )
    description: str | None = Field(default=None, min_length=0, max_length=255)
    environment: str | None = Field(default=None, min_length=1, max_length=100)
    tags: dict[str, str] | None = None
    store_id: UUID | None = None
    remote_reference: str | None = Field(
        default=None, pattern=AWS_SECRET_ID_PATTERN, max_length=2048
    )
    key_mapping: AwsSecretKeyMapping | None = None

    @field_validator("name", "environment")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str | None:
        # Omit the field to leave it unchanged; the columns are NOT NULL.
        if value is None:
            raise ValueError("must not be null")
        return value


class SecretReferenceCheckRequest(BaseModel):
    """Identifiers and actor context for an executor-side reference check."""

    role: Role
    secret_id: SecretID


class SecretReferenceCheckResult(BaseModel):
    """Outcome of a reference check. Never contains the remote value."""

    ok: bool
    error_code: AwsSecretResolutionErrorCode | None = None
    message: str | None = None
    resolved_keys: list[str] = Field(default_factory=list)


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
    secret_type: SecretType = SecretType.CUSTOM
    actions: list[str]
    action_count: int


class AwsAssumeRoleAccessRead(BaseModel):
    """Workspace-scoped AWS AssumeRole details shown in the credentials UI."""

    tracecat_aws_account_id: str
    tracecat_aws_principal_arn: str
    external_id: str
