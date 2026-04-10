"""Unit tests for tracecat.mcp.oidc.endpoints — OIDC issuer HTTP endpoints."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import secrets
import time
import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tracecat.auth.users import optional_current_active_user
from tracecat.db.engine import get_async_session_bypass_rls
from tracecat.db.models import User
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc import signing
from tracecat.mcp.oidc.endpoints import router
from tracecat.mcp.oidc.refresh_tokens import RefreshTokenError
from tracecat.mcp.oidc.schemas import (
    AuthCodeData,
    RefreshTokenContext,
    RefreshTokenMetadata,
    ResumeTransaction,
)
from tracecat.mcp.oidc.session import NeedsAction, SessionNeedsAction, SessionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-endpoint-secret"
_TEST_API_URL = "https://api.example.com"
_TEST_APP_URL = "https://app.example.com"
_TEST_MCP_URL = "https://mcp.example.com"
_ALLOWED_REDIRECT = f"{_TEST_APP_URL}/auth/callback"

# IP hash for Starlette TestClient (client.host = "testclient")
_TESTCLIENT_IP_HASH = hashlib.sha256(b"testclient").hexdigest()


def _pkce_pair() -> tuple[str, str]:
    """Generate a valid PKCE S256 verifier/challenge pair."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _make_auth_code_data(
    *,
    code: str = "test-code",
    verifier: str | None = None,
    challenge: str | None = None,
    created_at: float | None = None,
    bound_ip: str | None = None,
    redirect_uri: str | None = None,
    client_id: str | None = None,
    **kwargs,
) -> AuthCodeData:
    if verifier and not challenge:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not challenge:
        _, challenge = _pkce_pair()

    return AuthCodeData(
        code=code,
        user_id=uuid.uuid4(),
        email="user@example.com",
        organization_id=uuid.uuid4(),
        is_platform_superuser=False,
        client_id=client_id or oidc_config.INTERNAL_CLIENT_ID,
        redirect_uri=redirect_uri or _ALLOWED_REDIRECT,
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="openid profile email",
        resource=f"{_TEST_APP_URL}/mcp",
        nonce=None,
        created_at=created_at or time.time(),
        bound_ip=bound_ip or _TESTCLIENT_IP_HASH,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _setup_config(monkeypatch: pytest.MonkeyPatch):  # pyright: ignore[reportUnusedFunction]
    """Set config values and clear signing caches for each test."""
    monkeypatch.setattr(
        "tracecat.mcp.oidc.signing.get_user_auth_secret",
        lambda: _TEST_SECRET,
    )
    monkeypatch.setattr(oidc_config, "TRACECAT__PUBLIC_API_URL", _TEST_API_URL)
    monkeypatch.setattr(
        "tracecat.mcp.oidc.config.get_user_auth_secret",
        lambda: _TEST_SECRET,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.TRACECAT__PUBLIC_APP_URL", _TEST_APP_URL
    )
    monkeypatch.setattr("tracecat.config.TRACECAT__DB_ENCRYPTION_KEY", "enabled")
    signing.get_signing_key.cache_clear()
    signing.get_public_jwk.cache_clear()
    yield
    signing.get_signing_key.cache_clear()
    signing.get_public_jwk.cache_clear()


@pytest.fixture()
def mock_user():
    """Return a mock User object with required attributes."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="user@example.com",
        is_superuser=False,
    )


class _StubSession:
    """Stand-in for AsyncSession used by tests that mock all DB calls.

    The token endpoint depends on a session for refresh-token persistence,
    but tests that mock ``issue_refresh_token`` / ``rotate_refresh_token``
    never touch the underlying session. This stub satisfies the dependency
    without opening a real DB connection.
    """

    async def execute(self, *_args, **_kwargs):  # pragma: no cover - defensive
        raise AssertionError("_StubSession.execute should not be called in unit tests")

    def add(self, *_args, **_kwargs) -> None:  # pragma: no cover - defensive
        raise AssertionError("_StubSession.add should not be called in unit tests")

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _TrackingSession(_StubSession):
    """Session stub that records transaction calls for endpoint tests."""

    def __init__(self) -> None:
        self.commit_mock = AsyncMock()
        self.rollback_mock = AsyncMock()

    async def commit(self) -> None:
        await self.commit_mock()

    async def rollback(self) -> None:
        await self.rollback_mock()


async def _stub_session_dependency():
    yield _StubSession()


@contextlib.asynccontextmanager
async def _stub_bypass_session_context_manager():
    yield _StubSession()


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch):
    """Create a FastAPI test app with the OIDC router."""
    # Patch the lazy import in _allowed_redirect_uri
    test_app = FastAPI()
    test_app.include_router(router)
    # Override the user dependency to return None by default
    # (individual tests can override to return a mock user).
    test_app.dependency_overrides[optional_current_active_user] = lambda: None
    test_app.dependency_overrides[get_async_session_bypass_rls] = (
        _stub_session_dependency
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.get_async_session_bypass_rls_context_manager",
        _stub_bypass_session_context_manager,
    )
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Discovery endpoints
# ---------------------------------------------------------------------------


def test_openid_configuration_returns_correct_fields(client: TestClient) -> None:
    response = client.get("/.well-known/openid-configuration")

    assert response.status_code == 200
    payload = response.json()
    issuer = f"{_TEST_API_URL}/oauth/mcp"
    assert payload["issuer"] == issuer
    assert payload["authorization_endpoint"] == f"{issuer}/authorize"
    # token_endpoint, userinfo_endpoint, and jwks_uri use the request origin
    # so they are reachable by the caller (server-to-server or browser).
    request_issuer = "http://testserver/oauth/mcp"
    assert payload["token_endpoint"] == f"{request_issuer}/token"
    assert payload["userinfo_endpoint"] == f"{request_issuer}/userinfo"
    assert payload["jwks_uri"] == f"{request_issuer}/.well-known/jwks.json"
    assert payload["response_types_supported"] == ["code"]
    assert payload["scopes_supported"] == [
        "openid",
        "profile",
        "email",
        "offline_access",
    ]
    assert payload["code_challenge_methods_supported"] == ["S256"]
    assert payload["grant_types_supported"] == [
        "authorization_code",
        "refresh_token",
    ]


def test_openid_configuration_includes_root_path_when_proxied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When X-Forwarded-Host is present, endpoint URLs include root_path."""
    monkeypatch.setattr("tracecat.config.TRACECAT__PUBLIC_APP_URL", _TEST_MCP_URL)

    test_app = FastAPI(root_path="/api")
    test_app.include_router(router)
    test_app.dependency_overrides[optional_current_active_user] = lambda: None

    client = TestClient(test_app)
    response = client.get(
        "/.well-known/openid-configuration",
        headers={
            "x-forwarded-host": "example.com",
            "x-forwarded-proto": "https",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    request_issuer = "https://example.com/api/oauth/mcp"
    assert payload["token_endpoint"] == f"{request_issuer}/token"
    assert payload["userinfo_endpoint"] == f"{request_issuer}/userinfo"
    assert payload["jwks_uri"] == f"{request_issuer}/.well-known/jwks.json"


def test_openid_configuration_omits_refresh_support_when_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.config.TRACECAT__DB_ENCRYPTION_KEY", "")

    response = client.get("/.well-known/openid-configuration")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scopes_supported"] == ["openid", "profile", "email"]
    assert payload["grant_types_supported"] == ["authorization_code"]


def test_jwks_returns_valid_ed25519_key(client: TestClient) -> None:
    response = client.get("/.well-known/jwks.json")

    assert response.status_code == 200
    payload = response.json()
    assert "keys" in payload
    assert len(payload["keys"]) == 1

    key = payload["keys"][0]
    assert key["kty"] == "EC"
    assert key["crv"] == "P-256"
    assert key["alg"] == "ES256"
    assert "kid" in key
    assert "x" in key
    assert "y" in key


# ---------------------------------------------------------------------------
# Authorize endpoint — parameter validation
# ---------------------------------------------------------------------------


def _authorize_params(**overrides) -> dict[str, str]:
    """Build valid authorize query params, with optional overrides."""
    defaults = {
        "response_type": "code",
        "client_id": oidc_config.INTERNAL_CLIENT_ID,
        "redirect_uri": _ALLOWED_REDIRECT,
        "code_challenge": "test-challenge",
        "code_challenge_method": "S256",
        "scope": "openid",
        "state": "random-state",
        "resource": f"{_TEST_APP_URL}/mcp",
    }
    return defaults | overrides


def test_authorize_rejects_wrong_response_type(client: TestClient) -> None:
    response = client.get("/authorize", params=_authorize_params(response_type="token"))

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_response_type"


def test_authorize_rejects_wrong_client_id(client: TestClient) -> None:
    response = client.get(
        "/authorize", params=_authorize_params(client_id="wrong-client")
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client"


def test_authorize_rejects_wrong_redirect_uri(client: TestClient) -> None:
    response = client.get(
        "/authorize",
        params=_authorize_params(redirect_uri="https://evil.com/callback"),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_authorize_accepts_redirect_uri_without_explicit_default_http_port(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.TRACECAT__PUBLIC_APP_URL",
        "http://localhost:80",
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(return_value=SessionNeedsAction(action=NeedsAction.LOGIN)),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_resume_transaction", AsyncMock()
    )

    response = client.get(
        "/authorize",
        params=_authorize_params(redirect_uri="http://localhost/auth/callback"),
        follow_redirects=False,
    )

    assert response.status_code == 302


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:abc/auth/callback",
        "http://localhost:70000/auth/callback",
    ],
)
def test_authorize_rejects_redirect_uri_with_invalid_port(
    client: TestClient,
    redirect_uri: str,
) -> None:
    response = client.get(
        "/authorize",
        params=_authorize_params(redirect_uri=redirect_uri),
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "error_description": "redirect_uri does not match the registered callback",
    }


def test_authorize_rejects_missing_code_challenge(client: TestClient) -> None:
    response = client.get("/authorize", params=_authorize_params(code_challenge=""))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.anyio
async def test_authorize_defaults_resource_when_omitted(
    client: TestClient,
    mock_user: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When resource is omitted, it defaults to {MCP_BASE_URL}/mcp."""
    org_id = uuid.uuid4()
    stored_codes: list[AuthCodeData] = []

    async def _store_code(data: AuthCodeData) -> None:
        stored_codes.append(data)

    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(
            return_value=SessionResult(
                user=cast(User, mock_user), organization_id=org_id
            )
        ),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_auth_code", _store_code)

    params = _authorize_params()
    del params["resource"]
    response = client.get("/authorize", params=params, follow_redirects=False)

    assert response.status_code == 302
    assert len(stored_codes) == 1
    assert stored_codes[0].resource == f"{_TEST_APP_URL}/mcp"


# ---------------------------------------------------------------------------
# Authorize endpoint — session handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_authorize_redirects_to_login_when_no_session(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[ResumeTransaction] = []

    async def _store_resume(txn: ResumeTransaction) -> None:
        stored.append(txn)

    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(return_value=SessionNeedsAction(action=NeedsAction.LOGIN)),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_resume_transaction", _store_resume
    )

    response = client.get(
        "/authorize",
        params=_authorize_params(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{_TEST_APP_URL}/oauth/mcp/continue?txn=")
    assert len(stored) == 1


@pytest.mark.anyio
async def test_authorize_redirects_to_org_selection_for_superuser(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(return_value=SessionNeedsAction(action=NeedsAction.ORG_SELECTION)),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_resume_transaction", AsyncMock()
    )

    response = client.get(
        "/authorize",
        params=_authorize_params(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "/oauth/mcp/select-org?txn=" in response.headers["location"]


@pytest.mark.anyio
async def test_authorize_issues_code_on_valid_session(
    client: TestClient,
    mock_user: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stored_codes: list[AuthCodeData] = []

    async def _store_code(data: AuthCodeData) -> None:
        stored_codes.append(data)

    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(
            return_value=SessionResult(
                user=cast(User, mock_user), organization_id=org_id
            )
        ),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_auth_code", _store_code)

    response = client.get(
        "/authorize",
        params=_authorize_params(state="my-state"),
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(_ALLOWED_REDIRECT)
    assert "code=" in location
    assert "state=my-state" in location
    assert len(stored_codes) == 1
    assert stored_codes[0].organization_id == org_id


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


def _token_form(
    *,
    code: str = "test-code",
    verifier: str = "test-verifier",
    grant_type: str = "authorization_code",
    redirect_uri: str = _ALLOWED_REDIRECT,
    client_id: str = "",
    client_secret: str = "",
) -> dict[str, str]:
    return {
        "grant_type": grant_type,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "client_id": client_id,
        "client_secret": client_secret,
    }


def test_token_rejects_wrong_content_type(client: TestClient) -> None:
    # FastAPI form parsing rejects non-form content with a 422 before
    # the endpoint's own Content-Type check can run.
    response = client.post(
        "/token",
        json={"grant_type": "authorization_code"},
        headers={"content-type": "application/json"},
    )

    assert response.status_code in {415, 422}


@pytest.mark.anyio
async def test_token_rate_limits(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=False),
    )

    # Rate limit check runs after client authentication, so valid
    # credentials are required to reach it.
    secret = oidc_config.get_internal_client_secret()
    response = client.post(
        "/token",
        data=_token_form(
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 429


def test_token_rejects_wrong_grant_type(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )

    secret = oidc_config.get_internal_client_secret()
    response = client.post(
        "/token",
        data=_token_form(
            grant_type="client_credentials",
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unsupported_grant_type"
    assert "authorization_code" in body["error_description"]
    assert "refresh_token" in body["error_description"]


def test_token_rejects_invalid_client_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )

    response = client.post(
        "/token", data=_token_form(client_id="wrong-client", client_secret="x")
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_token_rejects_invalid_client_secret(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )

    response = client.post(
        "/token",
        data=_token_form(
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret="wrong-secret",
        ),
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_token_rejects_unknown_auth_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=None),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.anyio
async def test_token_rejects_expired_auth_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired_code = _make_auth_code_data(
        created_at=time.time() - oidc_config.AUTH_CODE_LIFETIME_SECONDS - 10
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=expired_code),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=expired_code.code,
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert "expired" in response.json()["error_description"]


@pytest.mark.anyio
async def test_token_rejects_redirect_uri_mismatch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code_data = _make_auth_code_data(redirect_uri="https://other.com/callback")
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=code_data.code,
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
            redirect_uri=_ALLOWED_REDIRECT,
        ),
    )

    assert response.status_code == 400
    assert "redirect_uri" in response.json()["error_description"]


@pytest.mark.anyio
async def test_token_rejects_failed_pkce(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _verifier, challenge = _pkce_pair()
    code_data = _make_auth_code_data(challenge=challenge)
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=code_data.code,
            verifier="wrong-verifier",
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 400
    assert "PKCE" in response.json()["error_description"]


@pytest.mark.anyio
async def test_token_success_returns_tokens(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy-path: valid PKCE, matching code — returns access_token + id_token."""
    verifier, challenge = _pkce_pair()
    code_data = _make_auth_code_data(challenge=challenge)

    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=code_data.code,
            verifier=verifier,
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS
    assert "id_token" in body
    assert body["scope"] == "openid profile email"

    # Verify the access token is a valid JWT signed by our key.
    decoded = signing.verify_jwt(body["access_token"])
    assert decoded["sub"] == str(code_data.user_id)
    assert decoded["email"] == code_data.email
    assert decoded["organization_id"] == str(code_data.organization_id)


@pytest.mark.anyio
async def test_token_accepts_basic_auth(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client authentication via Authorization: Basic header."""
    verifier, challenge = _pkce_pair()
    code_data = _make_auth_code_data(challenge=challenge)

    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    secret = oidc_config.get_internal_client_secret()
    basic_creds = base64.b64encode(
        f"{oidc_config.INTERNAL_CLIENT_ID}:{secret}".encode()
    ).decode()

    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code_data.code,
            "redirect_uri": _ALLOWED_REDIRECT,
            "code_verifier": verifier,
        },
        headers={"Authorization": f"Basic {basic_creds}"},
    )

    assert response.status_code == 200
    assert "access_token" in response.json()


# ---------------------------------------------------------------------------
# Token endpoint — refresh token issuance on offline_access
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_token_auth_code_grant_omits_refresh_without_offline_access(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier, challenge = _pkce_pair()
    code_data = _make_auth_code_data(challenge=challenge)
    assert "offline_access" not in code_data.scope.split()

    issued = AsyncMock(return_value="should-not-be-called")
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.get_async_session_bypass_rls_context_manager",
        lambda: (_ for _ in ()).throw(
            AssertionError("refresh-token session should not be opened")
        ),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.issue_refresh_token", issued)
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=code_data.code,
            verifier=verifier,
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert "refresh_token" not in body
    assert body["expires_in"] == 3600
    issued.assert_not_awaited()


@pytest.mark.anyio
async def test_token_auth_code_grant_includes_refresh_with_offline_access(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier, challenge = _pkce_pair()
    code_data = _make_auth_code_data(challenge=challenge).model_copy(
        update={"scope": "openid profile email offline_access"}
    )

    tracked_session = _StubSession()

    @contextlib.asynccontextmanager
    async def tracked_bypass_session_context_manager():
        yield tracked_session

    issued = AsyncMock(return_value="opaque-refresh-token-value")
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.get_async_session_bypass_rls_context_manager",
        tracked_bypass_session_context_manager,
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.issue_refresh_token", issued)
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=code_data.code,
            verifier=verifier,
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["refresh_token"] == "opaque-refresh-token-value"
    assert body["expires_in"] == 3600
    assert "id_token" in body
    issued.assert_awaited_once()
    assert issued.await_args is not None
    assert issued.await_args.args[0] is tracked_session
    call_kwargs = issued.await_args.kwargs
    assert call_kwargs["user_id"] == code_data.user_id
    assert call_kwargs["organization_id"] == code_data.organization_id
    assert call_kwargs["client_id"] == oidc_config.INTERNAL_CLIENT_ID
    assert call_kwargs["metadata"].scope == code_data.scope


@pytest.mark.anyio
async def test_token_auth_code_grant_strips_offline_access_when_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier, challenge = _pkce_pair()
    code_data = _make_auth_code_data(challenge=challenge).model_copy(
        update={"scope": "openid profile email offline_access"}
    )

    issued = AsyncMock(return_value="should-not-be-called")
    monkeypatch.setattr("tracecat.config.TRACECAT__DB_ENCRYPTION_KEY", "")
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        AsyncMock(return_value=code_data),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.issue_refresh_token", issued)
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data=_token_form(
            code=code_data.code,
            verifier=verifier,
            client_id=oidc_config.INTERNAL_CLIENT_ID,
            client_secret=secret,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert "refresh_token" not in body
    assert body["scope"] == "openid profile email"
    issued.assert_not_awaited()


# ---------------------------------------------------------------------------
# Token endpoint — refresh_token grant
# ---------------------------------------------------------------------------


def _make_refresh_context(**overrides) -> RefreshTokenContext:
    metadata = RefreshTokenMetadata(
        email="user@example.com",
        is_platform_superuser=False,
        scope="openid profile email offline_access",
        resource=f"{_TEST_APP_URL}/mcp",
    )
    defaults = {
        "family_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "client_id": oidc_config.INTERNAL_CLIENT_ID,
        "metadata": metadata,
    }
    return RefreshTokenContext(**(defaults | overrides))


@pytest.mark.anyio
async def test_token_refresh_grant_rejects_missing_refresh_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.anyio
async def test_token_refresh_grant_returns_new_tokens_without_id_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _make_refresh_context()
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.rotate_refresh_token",
        AsyncMock(return_value=(ctx, "new-rotated-refresh-token")),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh-token",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["refresh_token"] == "new-rotated-refresh-token"
    assert body["scope"] == ctx.metadata.scope
    # Per OIDC spec, id_token is only issued at initial authorization.
    assert "id_token" not in body

    # Access token claims should match the original session context.
    decoded = signing.verify_jwt(body["access_token"])
    assert decoded["sub"] == str(ctx.user_id)
    assert decoded["organization_id"] == str(ctx.organization_id)
    assert decoded["email"] == ctx.metadata.email
    assert decoded["scope"] == ctx.metadata.scope


@pytest.mark.anyio
async def test_token_refresh_grant_passes_token_and_client_to_rotation_helper(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _make_refresh_context()
    rotated = AsyncMock(return_value=(ctx, "rotated-token"))
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.rotate_refresh_token",
        rotated,
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_jti", AsyncMock())
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "any",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )

    assert response.status_code == 200
    rotated.assert_awaited_once()
    assert rotated.await_args is not None
    call_kwargs = rotated.await_args.kwargs
    assert call_kwargs["token"] == "any"
    assert call_kwargs["client_id"] == oidc_config.INTERNAL_CLIENT_ID


@pytest.mark.anyio
async def test_token_refresh_grant_maps_invalid_grant_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.rotate_refresh_token",
        AsyncMock(
            side_effect=RefreshTokenError(
                "invalid_grant", "Refresh token is invalid or expired"
            )
        ),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "stale-token",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_grant"
    assert "invalid or expired" in body["error_description"]


@pytest.mark.anyio
async def test_token_refresh_grant_maps_replay_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.rotate_refresh_token",
        AsyncMock(
            side_effect=RefreshTokenError(
                "invalid_grant",
                "Refresh token replay detected; family revoked",
            )
        ),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "replayed-token",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_grant"
    assert "replay" in body["error_description"]


@pytest.mark.anyio
async def test_token_refresh_grant_rolls_back_if_store_jti_fails_before_commit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked_session = _TrackingSession()

    @contextlib.asynccontextmanager
    async def tracked_bypass_session_context_manager():
        yield tracked_session

    ctx = _make_refresh_context()
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.get_async_session_bypass_rls_context_manager",
        tracked_bypass_session_context_manager,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.rotate_refresh_token",
        AsyncMock(return_value=(ctx, "new-rotated-refresh-token")),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_jti",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )
    secret = oidc_config.get_internal_client_secret()

    with pytest.raises(RuntimeError, match="redis unavailable"):
        client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "old-refresh-token",
                "client_id": oidc_config.INTERNAL_CLIENT_ID,
                "client_secret": secret,
            },
        )

    tracked_session.rollback_mock.assert_awaited_once()
    tracked_session.commit_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_token_refresh_grant_rejects_when_refresh_tokens_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracecat.config.TRACECAT__DB_ENCRYPTION_KEY", "")
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        AsyncMock(return_value=True),
    )
    secret = oidc_config.get_internal_client_secret()

    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh-token",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_grant"
    assert "disabled" in body["error_description"]


# ---------------------------------------------------------------------------
# Userinfo endpoint
# ---------------------------------------------------------------------------


def test_userinfo_requires_bearer_token(client: TestClient) -> None:
    response = client.get("/userinfo")
    assert response.status_code == 401


def test_userinfo_returns_claims_from_valid_access_token(client: TestClient) -> None:
    org_id = str(uuid.uuid4())
    token = signing.mint_jwt(
        {
            "sub": "user-123",
            "iss": oidc_config.get_issuer_url(),
            "aud": f"{_TEST_APP_URL}/mcp",
            "email": "user@example.com",
            "organization_id": org_id,
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        }
    )

    response = client.get("/userinfo", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["sub"] == "user-123"
    assert body["email"] == "user@example.com"
    assert body["email_verified"] is True
    assert body["organization_id"] == org_id


def test_userinfo_rejects_id_token(client: TestClient) -> None:
    """id_tokens have aud=client_id, not the resource URL, so must be rejected."""
    token = signing.mint_jwt(
        {
            "sub": "user-123",
            "iss": oidc_config.get_issuer_url(),
            "aud": oidc_config.INTERNAL_CLIENT_ID,
            "email": "user@example.com",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        }
    )

    response = client.get("/userinfo", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_userinfo_rejects_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/userinfo", headers={"Authorization": "Bearer invalid.jwt.token"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Resume endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_authorize_resume_rejects_unknown_transaction(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_resume_transaction",
        AsyncMock(return_value=None),
    )

    response = client.get("/authorize/resume", params={"txn": "unknown-txn"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.anyio
async def test_authorize_resume_rejects_expired_transaction(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired_txn = ResumeTransaction(
        transaction_id="txn-expired",
        authorize_params=_authorize_params(),
        created_at=time.time() - oidc_config.RESUME_TRANSACTION_LIFETIME_SECONDS - 10,
        bound_ip=_TESTCLIENT_IP_HASH,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_resume_transaction",
        AsyncMock(return_value=expired_txn),
    )

    response = client.get("/authorize/resume", params={"txn": "txn-expired"})

    assert response.status_code == 400
    assert "expired" in response.json()["error_description"].lower()


@pytest.mark.anyio
async def test_authorize_resume_rejects_ip_mismatch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    txn = ResumeTransaction(
        transaction_id="txn-ip",
        authorize_params=_authorize_params(),
        created_at=time.time(),
        bound_ip="different-ip-hash",
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_resume_transaction",
        AsyncMock(return_value=txn),
    )

    response = client.get("/authorize/resume", params={"txn": "txn-ip"})

    assert response.status_code == 400
    assert "ip" in response.json()["error_description"].lower()


@pytest.mark.anyio
async def test_authorize_resume_replays_authorize_on_success(
    client: TestClient,
    mock_user: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    txn = ResumeTransaction(
        transaction_id="txn-ok",
        authorize_params=_authorize_params(state="resumed-state"),
        created_at=time.time(),
        bound_ip=_TESTCLIENT_IP_HASH,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_resume_transaction",
        AsyncMock(return_value=txn),
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(
            return_value=SessionResult(
                user=cast(User, mock_user), organization_id=org_id
            )
        ),
    )
    monkeypatch.setattr("tracecat.mcp.oidc.endpoints.store_auth_code", AsyncMock())

    response = client.get(
        "/authorize/resume",
        params={"txn": "txn-ok"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert "code=" in location
    assert "state=resumed-state" in location


# ---------------------------------------------------------------------------
# get_internal_discovery_url — empty env var fallback
# ---------------------------------------------------------------------------


def test_get_internal_discovery_url_uses_api_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TRACECAT__API_URL is set, it is used as the base."""
    monkeypatch.setenv("TRACECAT__API_URL", "http://api:8000")
    url = oidc_config.get_internal_discovery_url()
    assert url == "http://api:8000/oauth/mcp/.well-known/openid-configuration"


def test_get_internal_discovery_url_falls_back_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TRACECAT__API_URL is not set, falls back to PUBLIC_API_URL."""
    monkeypatch.delenv("TRACECAT__API_URL", raising=False)
    monkeypatch.setattr(oidc_config, "TRACECAT__PUBLIC_API_URL", _TEST_API_URL)
    url = oidc_config.get_internal_discovery_url()
    assert url == f"{_TEST_API_URL}/oauth/mcp/.well-known/openid-configuration"


def test_get_internal_discovery_url_falls_back_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When TRACECAT__API_URL is empty string, falls back to PUBLIC_API_URL."""
    monkeypatch.setenv("TRACECAT__API_URL", "")
    monkeypatch.setattr(oidc_config, "TRACECAT__PUBLIC_API_URL", _TEST_API_URL)
    url = oidc_config.get_internal_discovery_url()
    assert url == f"{_TEST_API_URL}/oauth/mcp/.well-known/openid-configuration"
