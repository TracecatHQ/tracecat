"""Bitbucket Data Center instance credentials."""

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator


class BitbucketDataCenterTokenCredentials(BaseModel):
    base_url: str
    token: SecretStr = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(part in {".", ".."} for part in parsed.path.split("/"))
            or "%" in value
            or "\\" in value
            or any(c.isspace() for c in value)
        ):
            raise ValueError(
                "Use an HTTPS instance URL without credentials, query, or fragment"
            )
        _ = parsed.port
        return value

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip() or any(
            c in value.get_secret_value() for c in "\r\n"
        ):
            raise ValueError(
                "HTTP access token is required and must not contain newlines"
            )
        return value
