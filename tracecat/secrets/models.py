from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    StringConstraints,
    field_validator,
)

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""


class RevealedSecretKeyValue(BaseModel):
    key: str
    value: str

    def conceal(self) -> SecretKeyValue:
        return SecretKeyValue(key=self.key, value=self.value)


class SecretKeyValue(BaseModel):
    key: str
    value: SecretStr

    @staticmethod
    def from_str(kv: str) -> Self:
        key, value = kv.split("=", 1)
        return SecretKeyValue(key=key, value=value)

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
_SECRET_FACTORY: dict[str, type[SecretBase]] = {
    "custom": CustomSecret,
    # "token": TokenSecret,
    # "oauth2": OAuth2Secret,
}


class CreateSecretParams(BaseModel):
    """Create a new secret.

    Secret types
    ------------
    - `custom`: Arbitrary user-defined types
    - `token`: A token, e.g. API Key, JWT Token (TBC)
    - `oauth2`: OAuth2 Client Credentials (TBC)"""

    type: Literal["custom"] = "custom"  # Support other types later
    name: str
    description: str | None = None
    keys: list[SecretKeyValue]
    tags: dict[str, str] | None = None

    @staticmethod
    def from_strings(name: str, keyvalues: list[str]) -> CreateSecretParams:
        keys = [SecretKeyValue.from_str(kv) for kv in keyvalues]
        return CreateSecretParams(name=name, keys=keys)

    @field_validator("keys")
    def validate_keys(cls, v, values):
        if not v:
            raise ValueError("Keys cannot be empty")
        # Ensure keys are unique
        if len({kv.key for kv in v}) != len(v):
            raise ValueError("Keys must be unique")
        return v


class UpdateSecretParams(BaseModel):
    """Create a new secret.

    Secret types
    ------------
    - `custom`: Arbitrary user-defined types
    - `token`: A token, e.g. API Key, JWT Token (TBC)
    - `oauth2`: OAuth2 Client Credentials (TBC)"""

    type: Literal["custom"] | None = None
    name: str | None = None
    description: str | None = None
    keys: list[SecretKeyValue] | None = None
    tags: dict[str, str] | None = None


class SearchSecretsParams(BaseModel):
    names: list[str]


class SecretResponse(BaseModel):
    id: str
    type: Literal["custom"]  # Support other types later
    name: str
    description: str | None = None
    keys: list[str]
