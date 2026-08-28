"""Pure unit tests for Databricks and Snowflake OAuth providers."""

import pytest

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import PROVIDER_REGISTRY
from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.providers.databricks.oauth import (
    DATABRICKS_SERVICE_SCOPES,
    DATABRICKS_USER_SCOPES,
)
from tracecat.integrations.schemas import ProviderKey

AC = OAuthGrantType.AUTHORIZATION_CODE
CC = OAuthGrantType.CLIENT_CREDENTIALS


@pytest.mark.parametrize("provider_id", ["databricks", "snowflake"])
def test_registered_grant_types(provider_id: str) -> None:
    """Both integrations register user and service OAuth grants."""
    registered = {key.grant_type for key in PROVIDER_REGISTRY if key.id == provider_id}
    assert registered == {AC, CC}


@pytest.mark.parametrize("provider_id", ["databricks", "snowflake"])
def test_provider_ids_match_metadata(provider_id: str) -> None:
    """Each provider registration uses a consistent integration id."""
    for grant_type in (AC, CC):
        cls = PROVIDER_REGISTRY[ProviderKey(id=provider_id, grant_type=grant_type)]
        assert cls.id == cls.metadata.id == provider_id


def test_databricks_default_scopes() -> None:
    """Databricks user and service flows request their documented scopes."""
    user_cls = PROVIDER_REGISTRY[ProviderKey(id="databricks", grant_type=AC)]
    service_cls = PROVIDER_REGISTRY[ProviderKey(id="databricks", grant_type=CC)]

    assert user_cls.scopes.default == DATABRICKS_USER_SCOPES
    assert service_cls.scopes.default == DATABRICKS_SERVICE_SCOPES


def test_snowflake_default_scopes() -> None:
    """Native user OAuth requests refresh; external service scopes are configured."""
    user_cls = PROVIDER_REGISTRY[ProviderKey(id="snowflake", grant_type=AC)]
    service_cls = PROVIDER_REGISTRY[ProviderKey(id="snowflake", grant_type=CC)]

    assert user_cls.scopes.default == ["refresh_token"]
    assert service_cls.scopes.default == []


@pytest.mark.parametrize("provider_id", ["databricks", "snowflake"])
def test_user_oauth_uses_pkce(provider_id: str) -> None:
    """Both user authorization-code providers enable PKCE."""
    cls = PROVIDER_REGISTRY[ProviderKey(id=provider_id, grant_type=AC)]
    assert issubclass(cls, AuthorizationCodeOAuthProvider)
    provider = cls(
        client_id="client-id",
        client_secret="client-secret",
        authorization_endpoint="https://auth.example.com/oauth/authorize",
        token_endpoint="https://auth.example.com/oauth/token",
    )

    assert provider.client.code_challenge_method == "S256"
