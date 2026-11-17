"""Platform OIDC/OAuth client configuration utilities."""

from __future__ import annotations

import base64
import functools
from dataclasses import dataclass
from typing import Final

import boto3
import orjson
from httpx_oauth.clients.openid import OpenID
from httpx_oauth.oauth2 import BaseOAuth2

from tracecat import config
from tracecat.auth.enums import AuthType
from tracecat.logger import logger

_DEFAULT_OIDC_SCOPES: Final[tuple[str, ...]] = ("openid", "profile", "email")


@dataclass(frozen=True)
class OIDCProviderConfig:
    """Normalized OIDC provider configuration."""

    issuer: str | None
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]

    @property
    def discovery_endpoint(self) -> str | None:
        if self.issuer is None:
            return None
        return f"{self.issuer}/.well-known/openid-configuration"


def oidc_auth_type_enabled() -> bool:
    """Whether platform auth policy allows OIDC-style login."""
    return AuthType.OIDC in config.TRACECAT__AUTH_TYPES


@dataclass(frozen=True, slots=True)
class OIDCClientCredentials:
    """OIDC client ID and secret for the platform login flow."""

    client_id: str
    client_secret: str


@functools.cache
def _retrieve_client_credentials(arn: str) -> OIDCClientCredentials:
    """Read the OIDC client credentials from AWS Secrets Manager, once per process.

    The secret must be a JSON object with non-empty ``clientId`` and
    ``clientSecret`` strings.
    """
    logger.info("Retrieving OIDC client credentials from AWS Secrets Manager...")
    client = boto3.session.Session().client(service_name="secretsmanager")
    response = client.get_secret_value(SecretId=arn)
    secret_string = response.get("SecretString")
    if not secret_string and response.get("SecretBinary"):
        secret_string = base64.b64decode(response["SecretBinary"]).decode("utf-8")
    try:
        payload = orjson.loads(secret_string or "")
    except orjson.JSONDecodeError as e:
        raise ValueError(
            "OIDC credentials in AWS Secrets Manager must be a JSON object with"
            " clientId and clientSecret"
        ) from e
    match payload:
        case {"clientId": str(client_id), "clientSecret": str(client_secret)} if (
            client_id.strip() and client_secret.strip()
        ):
            return OIDCClientCredentials(
                client_id=client_id.strip(), client_secret=client_secret.strip()
            )
        case _:
            raise ValueError(
                "OIDC credentials in AWS Secrets Manager must have non-empty"
                " clientId and clientSecret"
            )


def get_platform_oidc_client_credentials() -> OIDCClientCredentials:
    """Return the OIDC client credentials, read from their ARN when one is set.

    ``OIDC_CREDENTIALS__ARN`` takes precedence over ``OIDC_CLIENT_ID`` and
    ``OIDC_CLIENT_SECRET``, matching how ``TRACECAT__DB_PASS__ARN`` is read.
    """
    if arn := config.OIDC_CREDENTIALS__ARN:
        return _retrieve_client_credentials(arn)
    return OIDCClientCredentials(
        client_id=config.OIDC_CLIENT_ID, client_secret=config.OIDC_CLIENT_SECRET
    )


def get_platform_oidc_config() -> OIDCProviderConfig:
    """Return normalized OIDC config."""
    issuer = config.OIDC_ISSUER.strip().rstrip("/") or None
    scopes = config.OIDC_SCOPES or _DEFAULT_OIDC_SCOPES
    credentials = get_platform_oidc_client_credentials()
    return OIDCProviderConfig(
        issuer=issuer,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        scopes=scopes,
    )


def create_platform_oauth_client() -> BaseOAuth2:
    """Create the platform OAuth client used for login/callback routes."""
    oidc_config = get_platform_oidc_config()
    scopes = list(oidc_config.scopes)
    discovery_endpoint = oidc_config.discovery_endpoint
    if discovery_endpoint is None:
        raise ValueError("OIDC_ISSUER must be configured for OIDC login.")
    return OpenID(
        client_id=oidc_config.client_id,
        client_secret=oidc_config.client_secret,
        openid_configuration_endpoint=discovery_endpoint,
        name="oidc",
        base_scopes=scopes,
    )
