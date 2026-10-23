"""Pure unit tests for the Atlassian OAuth provider."""

from urllib.parse import parse_qs, urlsplit

import pytest

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import PROVIDER_REGISTRY
from tracecat.integrations.providers.atlassian.oauth import (
    ATLASSIAN_SCOPES,
    AtlassianOAuthProvider,
)
from tracecat.integrations.schemas import ProviderKey


def _provider() -> AtlassianOAuthProvider:
    return AtlassianOAuthProvider(
        client_id="client-id",
        client_secret="client-secret",
    )


def test_atlassian_registers_authorization_code_only() -> None:
    registered = {key.grant_type for key in PROVIDER_REGISTRY if key.id == "atlassian"}
    assert registered == {OAuthGrantType.AUTHORIZATION_CODE}
    cls = PROVIDER_REGISTRY[
        ProviderKey(id="atlassian", grant_type=OAuthGrantType.AUTHORIZATION_CODE)
    ]
    assert cls is AtlassianOAuthProvider
    assert cls.id == cls.metadata.id == "atlassian"


def test_atlassian_default_scopes_request_refresh_tokens() -> None:
    assert AtlassianOAuthProvider.scopes.default == ATLASSIAN_SCOPES
    assert "offline_access" in ATLASSIAN_SCOPES


@pytest.mark.anyio
async def test_atlassian_authorization_url_sets_audience_and_consent() -> None:
    url, code_verifier = await _provider().get_authorization_url("state")

    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://auth.atlassian.com/authorize"
    )
    assert query["audience"] == ["api.atlassian.com"]
    assert query["prompt"] == ["consent"]
    assert query["scope"] == [" ".join(ATLASSIAN_SCOPES)]
    assert code_verifier is None


def test_atlassian_sends_client_credentials_in_token_body() -> None:
    assert _provider().client.token_endpoint_auth_method == "client_secret_post"
