"""Integration tests for the full internal OIDC issuer authorize-code + PKCE flow.

These tests exercise the complete flow without mocking storage or signing:
authorize → extract code → token exchange → verify JWT claims.

Redis storage functions are replaced with an in-memory dict to avoid
requiring a live Redis instance, but all other components (signing, PKCE,
parameter validation) use real implementations.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tracecat.auth.users import optional_current_active_user
from tracecat.db.models import User
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc import signing
from tracecat.mcp.oidc.endpoints import router
from tracecat.mcp.oidc.schemas import AuthCodeData, ResumeTransaction
from tracecat.mcp.oidc.session import SessionResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = "integration-test-secret-key"
_TEST_API_URL = "https://api.test.com"
_TEST_APP_URL = "https://app.test.com"
_TEST_MCP_URL = "https://mcp.test.com"
_REDIRECT_URI = f"{_TEST_MCP_URL}/auth/callback"


# ---------------------------------------------------------------------------
# In-memory storage (replaces Redis)
# ---------------------------------------------------------------------------


class _InMemoryOIDCStorage:
    """In-memory replacements for the storage module functions."""

    def __init__(self) -> None:
        self.codes: dict[str, AuthCodeData] = {}
        self.resume_txns: dict[str, ResumeTransaction] = {}
        self.jtis: set[str] = set()
        self.rate_counters: dict[str, int] = {}

    async def store_auth_code(self, data: AuthCodeData) -> None:
        self.codes[data.code] = data

    async def load_and_delete_auth_code(self, code: str) -> AuthCodeData | None:
        return self.codes.pop(code, None)

    async def store_resume_transaction(self, txn: ResumeTransaction) -> None:
        self.resume_txns[txn.transaction_id] = txn

    async def load_and_delete_resume_transaction(
        self, txn_id: str
    ) -> ResumeTransaction | None:
        return self.resume_txns.pop(txn_id, None)

    async def store_jti(self, jti: str) -> None:
        self.jtis.add(jti)

    async def check_token_rate_limit(self, source_ip: str) -> bool:
        count = self.rate_counters.get(source_ip, 0) + 1
        self.rate_counters[source_ip] = count
        return count <= oidc_config.TOKEN_RATE_LIMIT_PER_SOURCE_PER_MINUTE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE S256 verifier + challenge pair."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _setup_config(monkeypatch: pytest.MonkeyPatch):  # pyright: ignore[reportUnusedFunction]
    """Set config values and clear signing caches."""
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
    monkeypatch.setattr("tracecat.config.TRACECAT__PUBLIC_APP_URL", _TEST_MCP_URL)
    signing.get_signing_key.cache_clear()
    signing.get_public_jwk.cache_clear()
    yield
    signing.get_signing_key.cache_clear()
    signing.get_public_jwk.cache_clear()


@pytest.fixture()
def mem_storage() -> _InMemoryOIDCStorage:
    return _InMemoryOIDCStorage()


@pytest.fixture()
def mock_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="alice@example.com",
        is_superuser=False,
    )


@pytest.fixture()
def app(
    monkeypatch: pytest.MonkeyPatch,
    mem_storage: _InMemoryOIDCStorage,
    mock_user: SimpleNamespace,
) -> FastAPI:
    """Create a fully wired FastAPI test app with in-memory storage."""
    org_id = uuid.uuid4()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[optional_current_active_user] = lambda: mock_user

    # Wire in-memory storage
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_auth_code",
        mem_storage.store_auth_code,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_auth_code",
        mem_storage.load_and_delete_auth_code,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_resume_transaction",
        mem_storage.store_resume_transaction,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.load_and_delete_resume_transaction",
        mem_storage.load_and_delete_resume_transaction,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.store_jti",
        mem_storage.store_jti,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.check_token_rate_limit",
        mem_storage.check_token_rate_limit,
    )

    # Mock session resolution to return the test user + org
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.resolve_authorize_session",
        AsyncMock(
            return_value=SessionResult(
                user=cast(User, mock_user), organization_id=org_id
            )
        ),
    )

    # Store org_id on the app for assertions
    test_app.state.org_id = org_id  # type: ignore[attr-defined]
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Full flow tests
# ---------------------------------------------------------------------------


def _exchange_code(
    client: TestClient,
    code: str,
    verifier: str,
) -> dict[str, Any]:
    """Execute the token exchange step and return the response JSON."""
    secret = oidc_config.get_internal_client_secret()
    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


@pytest.mark.anyio
async def test_full_authorize_code_pkce_flow(
    client: TestClient,
    app: FastAPI,
    mock_user: SimpleNamespace,
) -> None:
    """End-to-end: authorize → code → token exchange → verify JWT claims."""
    verifier, challenge = _pkce_pair()

    # Step 1: Authorize — get redirect with code
    auth_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid profile email",
            "state": "test-state-123",
            "resource": f"{_TEST_MCP_URL}/mcp",
        },
        follow_redirects=False,
    )

    assert auth_response.status_code == 302
    location = auth_response.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert "code" in query
    assert query["state"] == ["test-state-123"]
    code = query["code"][0]

    # Step 2: Exchange code for tokens
    tokens = _exchange_code(client, code, verifier)

    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS
    assert tokens["scope"] == "openid profile email"
    assert "access_token" in tokens
    assert "id_token" in tokens

    # Step 3: Verify access token claims
    access_claims = signing.verify_jwt(tokens["access_token"])
    assert access_claims["sub"] == str(mock_user.id)
    assert access_claims["email"] == mock_user.email
    assert access_claims["organization_id"] == str(app.state.org_id)
    assert access_claims["aud"] == f"{_TEST_MCP_URL}/mcp"
    assert access_claims["iss"] == f"{_TEST_API_URL}/oauth/mcp"
    assert access_claims["is_platform_superuser"] is False

    # Step 4: Verify id_token claims
    id_claims = signing.verify_jwt(
        tokens["id_token"], audience=oidc_config.INTERNAL_CLIENT_ID
    )
    assert id_claims["sub"] == str(mock_user.id)
    assert id_claims["email"] == mock_user.email
    assert id_claims["email_verified"] is True
    assert id_claims["organization_id"] == str(app.state.org_id)


@pytest.mark.anyio
async def test_full_flow_code_reuse_rejected(
    client: TestClient,
    app: FastAPI,
) -> None:
    """Authorization codes are one-time use — second exchange fails."""
    verifier, challenge = _pkce_pair()

    # Authorize + first exchange
    auth_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid",
            "state": "s",
            "resource": f"{_TEST_MCP_URL}/mcp",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(auth_response.headers["location"]).query)["code"][0]
    first = _exchange_code(client, code, verifier)
    assert "access_token" in first

    # Second exchange with same code must fail
    secret = oidc_config.get_internal_client_secret()
    second = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": verifier,
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_discovery_and_jwks_verify_minted_token(client: TestClient) -> None:
    """Discovery → JWKS → verify a token with the advertised public key."""
    # Fetch discovery
    discovery = client.get("/.well-known/openid-configuration").json()
    issuer = discovery["issuer"]
    assert issuer == f"{_TEST_API_URL}/oauth/mcp"

    # Fetch JWKS
    jwks_response = client.get("/.well-known/jwks.json")
    assert jwks_response.status_code == 200
    jwks = jwks_response.json()
    assert len(jwks["keys"]) == 1

    jwk = jwks["keys"][0]
    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"

    # Reconstruct the public key from the JWK x/y values
    x_bytes = base64.urlsafe_b64decode(jwk["x"] + "==")
    y_bytes = base64.urlsafe_b64decode(jwk["y"] + "==")
    from cryptography.hazmat.primitives.asymmetric.ec import (
        SECP256R1,
        EllipticCurvePublicNumbers,
    )

    public_numbers = EllipticCurvePublicNumbers(
        x=int.from_bytes(x_bytes, "big"),
        y=int.from_bytes(y_bytes, "big"),
        curve=SECP256R1(),
    )
    public_key = public_numbers.public_key()

    # Mint a token using the internal signing key
    claims = {
        "sub": "test-user",
        "iss": issuer,
        "aud": "test-audience",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    token = signing.mint_jwt(claims)

    # Verify the token using the public key from JWKS
    decoded = jwt.decode(
        token,
        key=public_key,
        algorithms=["ES256"],
        audience="test-audience",
        issuer=issuer,
    )
    assert decoded["sub"] == "test-user"
    assert decoded["iss"] == issuer
