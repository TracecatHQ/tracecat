import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from pydantic import SecretStr

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import get_provider_class
from tracecat.integrations.providers.github.oauth import GitHubAppOAuthProvider
from tracecat.integrations.schemas import ProviderConfig, ProviderKey


def github_app_secret(**overrides: object) -> str:
    data: dict[str, object] = {
        "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        "installation_id": "98765",
    }
    data.update(overrides)
    return json.dumps(data)


def test_github_app_provider_is_registered_for_client_credentials():
    provider = get_provider_class(
        ProviderKey(id="github", grant_type=OAuthGrantType.CLIENT_CREDENTIALS)
    )

    assert provider is GitHubAppOAuthProvider


def test_github_app_provider_uses_client_id_as_app_id():
    provider = GitHubAppOAuthProvider(
        client_id="12345",
        client_secret=github_app_secret(),
    )

    assert provider.client_id == "12345"
    assert provider.installation_id == "98765"


def test_github_app_provider_allows_app_id_in_json_credentials():
    provider = GitHubAppOAuthProvider(
        client_secret=github_app_secret(app_id="12345"),
    )

    assert provider.client_id == "12345"


def test_github_app_provider_rejects_missing_installation_id():
    with pytest.raises(ValueError, match="installation_id"):
        GitHubAppOAuthProvider(
            client_id="12345",
            client_secret=github_app_secret(installation_id=""),
        )


def test_github_app_provider_rejects_missing_private_key():
    with pytest.raises(ValueError, match="private_key"):
        GitHubAppOAuthProvider(
            client_id="12345",
            client_secret=github_app_secret(private_key=""),
        )


def test_github_app_provider_can_be_built_from_provider_config():
    provider = GitHubAppOAuthProvider.from_config(
        ProviderConfig(
            client_id="12345",
            client_secret=SecretStr(github_app_secret()),
            token_endpoint="https://api.github.example.com",
        )
    )

    assert provider.client_id == "12345"
    assert provider.token_endpoint == "https://api.github.example.com"


@pytest.mark.anyio
@respx.mock
async def test_github_app_provider_mints_installation_token(monkeypatch):
    captured_payload = {}

    def fake_encode(payload, key, algorithm):
        captured_payload.update(payload)
        assert key == "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"
        assert algorithm == "RS256"
        return "app-jwt"

    monkeypatch.setattr(
        "tracecat.integrations.providers.github.oauth.jwt.encode", fake_encode
    )
    route = respx.post(
        "https://api.github.com/app/installations/98765/access_tokens"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "token": "installation-token",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            },
        )
    )
    provider = GitHubAppOAuthProvider(
        client_id="12345",
        client_secret=github_app_secret(),
    )

    result = await provider.get_client_credentials_token()

    assert result.access_token.get_secret_value() == "installation-token"
    assert result.refresh_token is None
    assert result.token_type == "Bearer"
    assert 0 < result.expires_in <= 1800
    assert captured_payload["iss"] == "12345"
    assert captured_payload["exp"] - captured_payload["iat"] == 600
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer app-jwt"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
