from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit
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
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    Tag,
    TypeAdapter,
    ValidationInfo,
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
    SecretMappingMode,
    SecretResolutionErrorCode,
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
    description: str | None = Field(default=None, min_length=0, max_length=255)
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
    remote_key_mapping: SecretKeyMapping | None = None

    @staticmethod
    def from_database(obj: Secret) -> SecretRead:
        mapping = (
            SecretKeyMapping.model_validate(obj.remote_key_mapping)
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


# === External secret stores ===
SecretKey = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")]

AWS_ROLE_ARN_PATTERN = r"^arn:aws(?:-[a-z]+)*:iam::\d{12}:role/[\w+=,.@/-]+$"
AWS_REGION_PATTERN = r"^[a-z]{2}(?:-[a-z]+)+-\d$"


def aws_partition_for_region(region: str) -> str | None:
    """Return the AWS partition for a region, or None when botocore doesn't know it."""
    try:
        return botocore.session.get_session().get_partition_for_region(region)
    except UnknownRegionError:
        return None  # Region is newer than the pinned botocore; let AWS decide.


def check_aws_partition(role_arn: str, region: str) -> None:
    """Reject a role ARN from a different AWS partition than the region."""
    partition = aws_partition_for_region(region)
    if partition is not None and role_arn.split(":")[1] != partition:
        raise ValueError(
            f"Role ARN partition must be {partition!r} for region {region!r}"
        )


class SecretJsonField(BaseModel):
    """One declared output key sourced from a top-level JSON field."""

    model_config = ConfigDict(frozen=True)

    key: SecretKey = Field(..., min_length=1, max_length=255)
    field: str = Field(..., min_length=1, max_length=255)


class SecretKeyMapping(BaseModel):
    """Declares how a remote secret value maps onto output keys.

    ``whole_string`` maps the entire remote string onto exactly one key.
    ``json`` maps selected top-level string fields onto declared keys.
    """

    model_config = ConfigDict(frozen=True)

    mode: SecretMappingMode
    keys: list[SecretKey] = Field(default_factory=list, max_length=100)
    fields: list[SecretJsonField] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_mapping(self) -> SecretKeyMapping:
        if self.mode == SecretMappingMode.WHOLE_STRING:
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
        """Return the declared output key names without touching the store."""
        if self.mode == SecretMappingMode.WHOLE_STRING:
            return list(self.keys)
        return [entry.key for entry in self.fields]


class AwsSecretsManagerStoreConfig(BaseModel):
    """Persisted provider configuration for an AWS Secrets Manager store."""

    model_config = ConfigDict(frozen=True)

    provider: Literal[SecretStoreProvider.AWS_SECRETS_MANAGER] = (
        SecretStoreProvider.AWS_SECRETS_MANAGER
    )
    # None until the admin creates the role from the generated trust policy.
    role_arn: str | None = Field(
        default=None, pattern=AWS_ROLE_ARN_PATTERN, max_length=2048
    )
    region: str = Field(..., pattern=AWS_REGION_PATTERN, max_length=64)
    external_id: str = Field(..., min_length=1, max_length=255)


class AwsSecretsManagerStoreCreate(BaseModel):
    """Client-supplied fields when creating an AWS Secrets Manager store."""

    provider: Literal[SecretStoreProvider.AWS_SECRETS_MANAGER] = (
        SecretStoreProvider.AWS_SECRETS_MANAGER
    )
    role_arn: str | None = Field(
        default=None, pattern=AWS_ROLE_ARN_PATTERN, max_length=2048
    )
    region: str = Field(..., pattern=AWS_REGION_PATTERN, max_length=64)

    @model_validator(mode="after")
    def validate_partition(self) -> AwsSecretsManagerStoreCreate:
        if self.role_arn is not None:
            check_aws_partition(self.role_arn, self.region)
        return self


class AwsSecretsManagerStoreUpdate(BaseModel):
    """Client-supplied fields when updating an AWS Secrets Manager store."""

    provider: Literal[SecretStoreProvider.AWS_SECRETS_MANAGER] = (
        SecretStoreProvider.AWS_SECRETS_MANAGER
    )
    role_arn: str | None = Field(
        default=None, pattern=AWS_ROLE_ARN_PATTERN, max_length=2048
    )
    region: str | None = Field(default=None, pattern=AWS_REGION_PATTERN, max_length=64)


BITWARDEN_CLOUD_REGIONS = frozenset({"us", "eu"})
"""Bitwarden cloud regions; the provider maps each to its endpoints."""


_BITWARDEN_HOSTNAME = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*"
)


def _split_https_url(value: str, *, invalid_message: str) -> tuple[SplitResult, str]:
    """Split an ``https://`` URL, refusing parts that make hosts ambiguous.

    A backslash is part of the host to Python but ends it in other URL
    parsers, so the host checked would not be the one dialled.
    """
    if any(c.isspace() or not c.isprintable() for c in value):
        raise ValueError("URL must not contain whitespace")
    parts = urlsplit(value)
    hostname = parts.hostname
    if parts.scheme != "https" or not hostname:
        raise ValueError(invalid_message)
    if any(c in value for c in "?#\\") or "@" in parts.netloc:
        raise ValueError(
            "URL must not carry credentials, a query, a fragment or a backslash"
        )
    return parts, hostname


def _check_bitwarden_server(value: str) -> str:
    """Accept a cloud region or the HTTPS base URL of a self-hosted server.

    The Bitwarden SDK refuses plain ``http://`` URLs, so self-hosted servers
    must use HTTPS. The host is lowercased, and a default port and a trailing
    slash are dropped, so one server has one spelling.
    """
    if value in BITWARDEN_CLOUD_REGIONS:
        return value
    parts, hostname = _split_https_url(
        value,
        invalid_message="server must be 'us', 'eu', or the https:// base URL of a"
        " self-hosted Bitwarden server",
    )
    if not _BITWARDEN_HOSTNAME.fullmatch(hostname):
        raise ValueError("server URL must name a DNS hostname or an IPv4 address")
    port = parts.port
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), "", ""))


BitwardenServer = Annotated[
    str,
    StringConstraints(min_length=2, max_length=2048),
    AfterValidator(_check_bitwarden_server),
]


class BitwardenSecretsManagerStoreConfig(BaseModel):
    """Persisted provider configuration for a Bitwarden Secrets Manager store.

    The machine-account access token is not part of the config; it is stored
    encrypted in the store's ``encrypted_access_token`` column.
    """

    model_config = ConfigDict(frozen=True)

    provider: Literal[SecretStoreProvider.BITWARDEN_SECRETS_MANAGER] = (
        SecretStoreProvider.BITWARDEN_SECRETS_MANAGER
    )
    server: BitwardenServer
    organization_id: UUID


class BitwardenSecretsManagerStoreCreate(BaseModel):
    """Client-supplied fields when creating a Bitwarden Secrets Manager store."""

    provider: Literal[SecretStoreProvider.BITWARDEN_SECRETS_MANAGER]
    server: BitwardenServer
    organization_id: UUID


class BitwardenSecretsManagerStoreUpdate(BaseModel):
    """Client-supplied fields when updating a Bitwarden Secrets Manager store."""

    provider: Literal[SecretStoreProvider.BITWARDEN_SECRETS_MANAGER]
    server: BitwardenServer | None = None
    organization_id: UUID | None = None


AZURE_KEY_VAULT_AUTHORITY_BY_SUFFIX: dict[str, str] = {
    "vault.azure.net": "login.microsoftonline.com",
    "vault.usgovcloudapi.net": "login.microsoftonline.us",
    "vault.azure.cn": "login.chinacloudapi.cn",
}
"""Entra ID sign-in host for the Key Vault DNS suffix of each Azure cloud."""

# 3-24 characters; starts with a letter, no trailing or consecutive hyphens.
_AZURE_KEY_VAULT_NAME = re.compile(r"(?=.{3,24}$)[a-z](?:-?[a-z0-9])*")


def _check_azure_key_vault_url(value: str) -> str:
    """Accept ``https://<vault-name>.<key-vault-suffix>`` and normalize it.

    Only Azure Key Vault hosts are accepted, so a store can never point the
    executor at another host. The host is lowercased and a trailing slash is
    dropped, so one vault has one spelling.
    """
    example = "https://my-vault.vault.azure.net"
    invalid = f"vault URL must be the vault's URI, for example {example}"
    parts, host = _split_https_url(value, invalid_message=invalid)
    name, _, suffix = host.partition(".")
    if (
        parts.port not in (None, 443)
        or parts.path not in ("", "/")
        or suffix not in AZURE_KEY_VAULT_AUTHORITY_BY_SUFFIX
        or not _AZURE_KEY_VAULT_NAME.fullmatch(name)
    ):
        raise ValueError(invalid)
    return f"https://{host}"


AzureKeyVaultUrl = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255),
    AfterValidator(_check_azure_key_vault_url),
]


class AzureKeyVaultStoreConfig(BaseModel):
    """Persisted provider configuration for an Azure Key Vault store.

    The service principal's client secret is not part of the config; it is
    stored encrypted in the store's ``encrypted_access_token`` column.
    """

    model_config = ConfigDict(frozen=True)

    provider: Literal[SecretStoreProvider.AZURE_KEY_VAULT] = (
        SecretStoreProvider.AZURE_KEY_VAULT
    )
    vault_url: AzureKeyVaultUrl
    tenant_id: UUID
    client_id: UUID

    @property
    def authority(self) -> str:
        """The Entra ID sign-in host of the vault's Azure cloud."""
        suffix = self.vault_url.removeprefix("https://").partition(".")[2]
        return AZURE_KEY_VAULT_AUTHORITY_BY_SUFFIX[suffix]


class AzureKeyVaultStoreCreate(BaseModel):
    """Client-supplied fields when creating an Azure Key Vault store."""

    provider: Literal[SecretStoreProvider.AZURE_KEY_VAULT]
    vault_url: AzureKeyVaultUrl
    tenant_id: UUID
    client_id: UUID


class AzureKeyVaultStoreUpdate(BaseModel):
    """Client-supplied fields when updating an Azure Key Vault store."""

    provider: Literal[SecretStoreProvider.AZURE_KEY_VAULT]
    vault_url: AzureKeyVaultUrl | None = None
    tenant_id: UUID | None = None
    client_id: UUID | None = None


def _config_provider(value: Any) -> str:
    """Discriminate store configs; configs without ``provider`` predate it (AWS)."""
    if isinstance(value, dict):
        return str(value.get("provider") or SecretStoreProvider.AWS_SECRETS_MANAGER)
    # Pydantic may hand the discriminator an already-built model of any of the
    # union's config types; getattr reads `provider` from whichever it is.
    return str(getattr(value, "provider", SecretStoreProvider.AWS_SECRETS_MANAGER))


SecretStoreConfig = Annotated[
    Annotated[
        AwsSecretsManagerStoreConfig, Tag(SecretStoreProvider.AWS_SECRETS_MANAGER)
    ]
    | Annotated[
        BitwardenSecretsManagerStoreConfig,
        Tag(SecretStoreProvider.BITWARDEN_SECRETS_MANAGER),
    ]
    | Annotated[AzureKeyVaultStoreConfig, Tag(SecretStoreProvider.AZURE_KEY_VAULT)],
    Discriminator(_config_provider),
]
SecretStoreCreateConfig = Annotated[
    Annotated[
        AwsSecretsManagerStoreCreate, Tag(SecretStoreProvider.AWS_SECRETS_MANAGER)
    ]
    | Annotated[
        BitwardenSecretsManagerStoreCreate,
        Tag(SecretStoreProvider.BITWARDEN_SECRETS_MANAGER),
    ]
    | Annotated[AzureKeyVaultStoreCreate, Tag(SecretStoreProvider.AZURE_KEY_VAULT)],
    Discriminator(_config_provider),
]
SecretStoreUpdateConfig = Annotated[
    Annotated[
        AwsSecretsManagerStoreUpdate, Tag(SecretStoreProvider.AWS_SECRETS_MANAGER)
    ]
    | Annotated[
        BitwardenSecretsManagerStoreUpdate,
        Tag(SecretStoreProvider.BITWARDEN_SECRETS_MANAGER),
    ]
    | Annotated[AzureKeyVaultStoreUpdate, Tag(SecretStoreProvider.AZURE_KEY_VAULT)],
    Discriminator(_config_provider),
]

_STORE_CONFIG_ADAPTER: TypeAdapter[SecretStoreConfig] = TypeAdapter(SecretStoreConfig)


def parse_secret_store_config(
    raw: Any, *, provider: SecretStoreProvider | str | None = None
) -> SecretStoreConfig:
    """Validate a persisted store config blob into its provider's model.

    With ``provider``, also refuse a blob whose own provider differs, so a
    backend never receives another provider's config.
    """
    config = _STORE_CONFIG_ADAPTER.validate_python(raw)
    if provider is not None and config.provider != provider:
        raise ValueError(
            f"Secret store config is for {config.provider}, not {provider}."
        )
    return config


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
    # Its length is checked by the service: a validation error here would
    # echo the token back in the 422 response.
    access_token: SecretStr | None = Field(
        default=None,
        description="Access token, for providers that need one. Stored encrypted and never returned.",
    )

    @field_validator("config")
    @classmethod
    def config_matches_provider(
        cls, config: SecretStoreCreateConfig, info: ValidationInfo
    ) -> SecretStoreCreateConfig:
        # A field error, not a model error: a model error's 422 response
        # carries the whole request body, access token included.
        provider = info.data.get("provider")
        if provider is not None and config.provider != provider:
            raise ValueError("config.provider must match provider")
        return config


class SecretStoreUpdate(BaseModel):
    """Update an organization-owned secret store. Server-owned fields are immutable."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    config: SecretStoreUpdateConfig | None = None
    enabled: bool | None = None
    all_workspaces: bool = Field(
        default=False, description="Allow all current and future workspaces."
    )
    access_token: SecretStr | None = Field(
        default=None,
        description="Replace the access token. Omit to leave it unchanged. Never returned.",
    )

    @field_validator("name", "enabled", "access_token")
    @classmethod
    def reject_explicit_null(
        cls, value: str | bool | SecretStr | None
    ) -> str | bool | SecretStr | None:
        # Omit the field to leave it unchanged.
        if value is None:
            raise ValueError("must not be null")
        return value


class SecretStoreWorkspaceUsage(BaseModel):
    """Number of secrets in one workspace that reference a store."""

    workspace_id: WorkspaceID
    secret_count: int


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
    access_token_set: bool = Field(
        default=False,
        description="Whether the store has an access token. The token itself is never returned.",
    )
    tracecat_aws_account_id: str | None = None
    tracecat_aws_principal_arn: str | None = None
    aws_partition: str | None = Field(
        default=None,
        description="AWS partition of the store region, when botocore knows it.",
    )
    authorized_workspace_ids: list[WorkspaceID] = Field(default_factory=list)
    reference_count: int = 0
    workspace_usage: list[SecretStoreWorkspaceUsage] = Field(
        default_factory=list,
        description="Referencing secret counts per workspace, most used first.",
    )
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_database(
        obj: OrganizationSecretStore,
        *,
        authorized_workspace_ids: list[WorkspaceID],
        workspace_usage: list[SecretStoreWorkspaceUsage],
        tracecat_aws_account_id: str | None,
        tracecat_aws_principal_arn: str | None,
    ) -> SecretStoreRead:
        config = parse_secret_store_config(obj.config)
        return SecretStoreRead(
            id=obj.id,
            organization_id=obj.organization_id,
            name=obj.name,
            description=obj.description,
            provider=SecretStoreProvider(obj.provider),
            config=config,
            enabled=obj.enabled,
            all_workspaces=obj.all_workspaces,
            access_token_set=obj.encrypted_access_token is not None,
            tracecat_aws_account_id=tracecat_aws_account_id,
            tracecat_aws_principal_arn=tracecat_aws_principal_arn,
            aws_partition=(
                aws_partition_for_region(config.region)
                if isinstance(config, AwsSecretsManagerStoreConfig)
                else None
            ),
            authorized_workspace_ids=authorized_workspace_ids,
            reference_count=sum(u.secret_count for u in workspace_usage),
            workspace_usage=workspace_usage,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
        )


class SecretStoreRequestValidationError(BaseModel):
    """Standard FastAPI request validation fields for the shared 422 response."""

    loc: list[str | int]
    msg: str
    type: str
    input: JsonValue = Field(default=None)
    ctx: dict[str, JsonValue] | None = Field(default=None)


class SecretStoreErrorResponse(BaseModel):
    """Invalid store configuration, or enabling a store before setup finishes."""

    detail: str | list[SecretStoreRequestValidationError]


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
    region: str | None = Field(
        default=None, description="AWS region of the store. Set for AWS stores only."
    )
    enabled: bool

    @staticmethod
    def from_database(obj: OrganizationSecretStore) -> WorkspaceSecretStoreRead:
        config = parse_secret_store_config(obj.config)
        return WorkspaceSecretStoreRead(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            provider=SecretStoreProvider(obj.provider),
            region=(
                config.region
                if isinstance(config, AwsSecretsManagerStoreConfig)
                else None
            ),
            enabled=obj.enabled,
        )


class SecretReferenceCreate(BaseModel):
    """Create a workspace custom secret backed by an external store.

    The store's provider validates ``remote_reference`` when it is saved.
    """

    name: str = Field(..., max_length=100, pattern=EXPRESSION_SECRET_NAME_PATTERN)
    description: str | None = Field(default=None, min_length=0, max_length=255)
    environment: str = Field(
        default=DEFAULT_SECRETS_ENVIRONMENT, min_length=1, max_length=100
    )
    tags: dict[str, str] | None = None
    store_id: UUID
    remote_reference: str = Field(..., min_length=1, max_length=2048)
    key_mapping: SecretKeyMapping


class SecretReferenceUpdate(BaseModel):
    """Update an externally backed workspace secret. Values are never accepted."""

    name: str | None = Field(
        default=None, max_length=100, pattern=EXPRESSION_SECRET_NAME_PATTERN
    )
    description: str | None = Field(default=None, min_length=0, max_length=255)
    environment: str | None = Field(default=None, min_length=1, max_length=100)
    tags: dict[str, str] | None = None
    store_id: UUID | None = None
    remote_reference: str | None = Field(default=None, min_length=1, max_length=2048)
    key_mapping: SecretKeyMapping | None = None

    @field_validator("name", "environment")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str | None:
        # Omit the field to leave it unchanged; the columns are NOT NULL.
        if value is None:
            raise ValueError("must not be null")
        return value


class AwsSecretReferenceCreate(SecretReferenceCreate):
    """Create a workspace custom secret backed by AWS Secrets Manager.

    Deprecated: use :class:`SecretReferenceCreate`.
    """

    remote_reference: str = Field(..., pattern=AWS_SECRET_ID_PATTERN, max_length=2048)


class AwsSecretReferenceUpdate(SecretReferenceUpdate):
    """Update an AWS-backed workspace secret. Values are never accepted.

    Deprecated: use :class:`SecretReferenceUpdate`.
    """

    remote_reference: str | None = Field(
        default=None, pattern=AWS_SECRET_ID_PATTERN, max_length=2048
    )


class SecretReferenceCheckRequest(BaseModel):
    """Identifiers and actor context for an executor-side reference check."""

    role: Role
    secret_id: SecretID


class SecretReferenceCheckResult(BaseModel):
    """Outcome of a reference check. Never contains the remote value."""

    ok: bool
    error_code: SecretResolutionErrorCode | None = None
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
