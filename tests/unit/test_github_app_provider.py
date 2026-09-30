"""Unit tests for the GitHub App integration provider."""

import json
from functools import partial
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from tracecat_registry import RegistryOAuthSecret

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import PROVIDER_REGISTRY
from tracecat.integrations.providers.github import app as github_app
from tracecat.integrations.providers.github.app import (
    GitHubAppProvider,
    parse_github_app_permissions,
)
from tracecat.integrations.schemas import ProviderKey
from tracecat.registry.actions.schemas import TemplateAction

GITHUB_TEMPLATES = sorted(
    (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "tracecat-registry"
        / "tracecat_registry"
        / "templates"
        / "tools"
        / "github"
    ).rglob("*.yml")
)


@pytest.fixture(scope="module")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _mock_github(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
) -> None:
    monkeypatch.setattr(
        github_app.httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=handler),
    )


def test_github_registers_app_and_oauth_grants() -> None:
    registered = {key.grant_type for key in PROVIDER_REGISTRY if key.id == "github"}
    assert registered == {
        OAuthGrantType.AUTHORIZATION_CODE,
        OAuthGrantType.CLIENT_CREDENTIALS,
    }
    cls = PROVIDER_REGISTRY[
        ProviderKey(id="github", grant_type=OAuthGrantType.CLIENT_CREDENTIALS)
    ]
    assert cls is GitHubAppProvider


def test_rejects_invalid_private_key() -> None:
    with pytest.raises(ValueError, match="PEM private key"):
        GitHubAppProvider(client_id="123", client_secret="not-a-key")


def test_normalizes_escaped_private_key(private_key_pem: str) -> None:
    escaped = private_key_pem.strip().replace("\n", "\\n")
    provider = GitHubAppProvider(client_id=" 123 ", client_secret=escaped)
    assert provider.client_id == "123"
    assert provider.client_secret == private_key_pem


def test_parse_permissions() -> None:
    assert parse_github_app_permissions(["issues:write", "contents:read"]) == {
        "issues": "write",
        "contents": "read",
    }
    with pytest.raises(ValueError, match="Invalid GitHub App permission"):
        parse_github_app_permissions(["repo"])


@pytest.mark.anyio
async def test_mints_token_for_single_installation(
    monkeypatch: pytest.MonkeyPatch, private_key_pem: str
) -> None:
    public_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None
    ).public_key()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        token = request.headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, public_key, algorithms=["RS256"])  # pyright: ignore[reportArgumentType]
        assert claims["iss"] == "Iv23liAppClient"
        if request.method == "GET":
            assert request.url.path == "/app/installations"
            return httpx.Response(200, json=[{"id": 42, "account": {"login": "acme"}}])
        assert request.url.path == "/app/installations/42/access_tokens"
        assert json.loads(request.content) == {"permissions": {"issues": "write"}}
        return httpx.Response(
            201,
            json={
                "token": "ghs_installation",
                "expires_at": "2999-01-01T00:00:00Z",
                "permissions": {"issues": "write", "metadata": "read"},
            },
        )

    _mock_github(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubAppProvider(
        client_id="Iv23liAppClient",
        client_secret=private_key_pem,
        scopes=["issues:write"],
    )

    token = await provider.get_client_credentials_token()

    assert token.access_token.get_secret_value() == "ghs_installation"
    assert token.refresh_token is None
    assert token.expires_in is not None and token.expires_in > 0
    assert token.scope == "issues:write metadata:read"
    assert [r.method for r in requests] == ["GET", "POST"]


@pytest.mark.anyio
async def test_uses_explicit_installation_endpoint(
    monkeypatch: pytest.MonkeyPatch, private_key_pem: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"token": "ghs_explicit"})

    _mock_github(monkeypatch, httpx.MockTransport(handler))
    endpoint = "https://ghe.example.com/api/v3/app/installations/7/access_tokens"
    provider = GitHubAppProvider(
        client_id="123",
        client_secret=private_key_pem,
        authorization_endpoint=endpoint,
        token_endpoint=endpoint,
    )

    token = await provider.get_client_credentials_token()

    assert token.access_token.get_secret_value() == "ghs_explicit"
    assert len(requests) == 1
    assert str(requests[0].url) == endpoint
    assert requests[0].content == b""


@pytest.mark.anyio
async def test_multiple_installations_require_explicit_endpoint(
    monkeypatch: pytest.MonkeyPatch, private_key_pem: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": 1, "account": {"login": "acme"}},
                {"id": 2, "account": {"login": "globex"}},
            ],
        )

    _mock_github(monkeypatch, httpx.MockTransport(handler))
    provider = GitHubAppProvider(client_id="123", client_secret=private_key_pem)

    with pytest.raises(ValueError, match=r"2 installations: acme \(1\), globex \(2\)"):
        await provider.get_client_credentials_token()


@pytest.mark.parametrize(
    "path",
    GITHUB_TEMPLATES,
    ids=lambda p: str(p.relative_to(p.parents[1])),
)
def test_github_template_prefers_app_token(path: Path) -> None:
    definition = TemplateAction.from_yaml(path).definition
    secrets = definition.secrets or []
    assert [
        (s.provider_id, s.grant_type, s.optional)
        for s in secrets
        if isinstance(s, RegistryOAuthSecret)
    ] == [
        ("github", "client_credentials", True),
        ("github", "authorization_code", True),
    ]
    assert len(secrets) == 2

    raw = path.read_text()
    assert (
        "Bearer ${{ SECRETS.github_oauth.GITHUB_SERVICE_TOKEN || SECRETS.github_oauth.GITHUB_USER_TOKEN }}"
        in raw
    )
    assert raw.count("SECRETS.") == 2
