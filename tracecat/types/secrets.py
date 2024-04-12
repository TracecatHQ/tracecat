from pydantic import BaseModel, ConfigDict


class SecretKeyValue(BaseModel):
    key: str
    value: str


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
