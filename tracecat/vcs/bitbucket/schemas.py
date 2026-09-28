"""Bitbucket Cloud API token credentials."""

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator


class BitbucketTokenCredentials(BaseModel):
    email: EmailStr
    token: SecretStr = Field(min_length=1)

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API token is required")
        return value
