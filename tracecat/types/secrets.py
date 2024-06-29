from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints

SecretName = Annotated[str, StringConstraints(pattern=r"[a-z0-9_]+")]
"""Validator for a secret name. e.g. 'aws_access_key_id'"""

SecretKey = Annotated[str, StringConstraints(pattern=r"[a-zA-Z0-9_]+")]
"""Validator for a secret key. e.g. 'access_key_id'"""


class SecretKeyValue(BaseModel):
    key: str
    value: SecretStr

    @staticmethod
    def from_str(kv: str) -> Self:
        key, value = kv.split("=", 1)
        return SecretKeyValue(key=key, value=value)

    def reveal(self) -> dict[str, str]:
        return {"key": self.key, "value": self.value.get_secret_value()}


class SecretBase(BaseModel):
    pass


class CustomSecret(SecretBase):
    model_config = ConfigDict(extra="allow")


# class TokenSecret(SecretBase):
#     token: str


# class OAuth2Secret(SecretBase):
#     client_id: str
#     client_secret: str
#     redirect_uri: str


SecretVariant = CustomSecret  # | TokenSecret | OAuth2Secret

SECRET_FACTORY: dict[str, type[SecretBase]] = {
    "custom": CustomSecret,
    # "token": TokenSecret,
    # "oauth2": OAuth2Secret,
}
