from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)

from tracecat.db.schemas import BaseSecret
from tracecat.identifiers import OwnerID, SecretID
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.enums import SecretLevel, SecretType

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""


class RevealedSecretKeyValue(BaseModel):
    key: str
    value: str

    def conceal(self) -> SecretKeyValue:
        return SecretKeyValue(key=self.key, value=SecretStr(self.value))


class SecretKeyValue(BaseModel):
    key: str
    value: SecretStr

    @staticmethod
    def from_str(kv: str) -> SecretKeyValue:
        key, value = kv.split("=", 1)
        return SecretKeyValue(key=key, value=SecretStr(value))

    def reveal(self) -> RevealedSecretKeyValue:
        return RevealedSecretKeyValue(key=self.key, value=self.value.get_secret_value())


class SecretBase(BaseModel):
    """Base class for secrets."""

    @classmethod
    def factory(cls, type: str) -> type[SecretBase]:
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
    - `oauth2`: OAuth2 Client Credentials (TBC)"""

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


class SecretUpdate(BaseModel):
    """Update a secret.

    Secret types
    ------------
    - `custom`: Arbitrary user-defined types
    - `token`: A token, e.g. API Key, JWT Token (TBC)
    - `oauth2`: OAuth2 Client Credentials (TBC)"""

    type: SecretType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=0, max_length=1000)
    keys: list[SecretKeyValue] | None = Field(
        default=None, min_length=1, max_length=100
    )
    tags: dict[str, str] | None = Field(default=None, min_length=0, max_length=1000)
    environment: str | None = Field(default=None, min_length=1, max_length=100)
    level: SecretLevel | None = Field(default=None, min_length=1, max_length=100)


class SecretSearch(BaseModel):
    names: set[str] | None = None
    ids: set[SecretID] | None = None
    environment: str
    owner_ids: set[UUID] | None = None
    types: set[SecretType] | None = None
    levels: set[SecretLevel] | None = None


class SecretReadMinimal(BaseModel):
    id: str
    type: SecretType
    name: str
    description: str | None = None
    keys: list[str]
    environment: str


class SecretRead(BaseModel):
    id: str
    type: SecretType
    name: str
    description: str | None = None
    encrypted_keys: bytes
    environment: str
    tags: dict[str, str] | None = None
    owner_id: OwnerID
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_database(obj: BaseSecret) -> SecretRead:
        return SecretRead(
            id=obj.id,
            type=SecretType(obj.type),
            name=obj.name,
            description=obj.description,
            environment=obj.environment,
            owner_id=obj.owner_id,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            encrypted_keys=obj.encrypted_keys,
            tags=obj.tags,
        )
