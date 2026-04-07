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
from tracecat.mcp.oidc.session import SessionResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEST_SECRET = "integration-test-secret-key"
_TEST_API_URL = "https://api.test.com"
_TEST_APP_URL = "https://app.test.com"
_REDIRECT_URI = f"{_TEST_APP_URL}/auth/callback"


# ---------------------------------------------------------------------------
# In-memory storage (replaces Redis)
# ---------------------------------------------------------------------------


class _InMemoryRefreshTokenRow:
    """Holds the persisted state for one refresh token in the in-memory store."""

    def __init__(
        self,
        *,
        family_id: uuid.UUID,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        client_id: str,
        metadata: RefreshTokenMetadata,
    ) -> None:
        self.family_id = family_id
        self.user_id = user_id
        self.organization_id = organization_id
        self.client_id = client_id
        self.metadata = metadata
        self.status = "active"


class _InMemoryOIDCStorage:
    """In-memory replacements for the storage module functions."""

    def __init__(self) -> None:
        self.codes: dict[str, AuthCodeData] = {}
        self.resume_txns: dict[str, ResumeTransaction] = {}
        self.jtis: set[str] = set()
        self.rate_counters: dict[str, int] = {}
        # Refresh tokens are keyed by plaintext (test-only — production hashes them).
        self.refresh_tokens: dict[str, _InMemoryRefreshTokenRow] = {}

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

    # --- Refresh token operations ---

    async def issue_refresh_token(
        self,
        _session: Any,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        client_id: str,
        metadata: RefreshTokenMetadata,
        family_id: uuid.UUID | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        self.refresh_tokens[token] = _InMemoryRefreshTokenRow(
            family_id=family_id or uuid.uuid4(),
            user_id=user_id,
            organization_id=organization_id,
            client_id=client_id,
            metadata=metadata,
        )
        return token

    async def consume_refresh_token(
        self,
        _session: Any,
        *,
        token: str,
        client_id: str,
    ) -> RefreshTokenContext:
        row = self.refresh_tokens.get(token)
        if row is None:
            raise RefreshTokenError(
                "invalid_grant", "Refresh token is invalid or expired"
            )
        if row.status == "used":
            # Replay: revoke the entire family.
            for r in self.refresh_tokens.values():
                if r.family_id == row.family_id:
                    r.status = "revoked"
            raise RefreshTokenError(
                "invalid_grant", "Refresh token replay detected; family revoked"
            )
        if row.status != "active":
            raise RefreshTokenError(
                "invalid_grant", "Refresh token is invalid or expired"
            )
        if row.client_id != client_id:
            for r in self.refresh_tokens.values():
                if r.family_id == row.family_id:
                    r.status = "revoked"
            raise RefreshTokenError("invalid_grant", "client_id mismatch")
        row.status = "used"
        return RefreshTokenContext(
            family_id=row.family_id,
            user_id=row.user_id,
            organization_id=row.organization_id,
            client_id=row.client_id,
            metadata=row.metadata,
        )


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


async def _stub_session_dependency():
    """Stand-in for the DB session — refresh tokens are mocked in-memory."""
    yield None


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
    test_app.dependency_overrides[get_async_session_bypass_rls] = (
        _stub_session_dependency
    )

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
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.issue_refresh_token",
        mem_storage.issue_refresh_token,
    )
    monkeypatch.setattr(
        "tracecat.mcp.oidc.endpoints.consume_refresh_token",
        mem_storage.consume_refresh_token,
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
            "resource": f"{_TEST_APP_URL}/mcp",
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
    assert access_claims["aud"] == f"{_TEST_APP_URL}/mcp"
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
            "resource": f"{_TEST_APP_URL}/mcp",
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


@pytest.mark.anyio
async def test_full_flow_without_offline_access_omits_refresh_token(
    client: TestClient,
) -> None:
    """An auth code without offline_access scope must not yield a refresh token."""
    verifier, challenge = _pkce_pair()

    auth_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid profile email",
            "state": "no-offline",
            "resource": f"{_TEST_APP_URL}/mcp",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(auth_response.headers["location"]).query)["code"][0]

    tokens = _exchange_code(client, code, verifier)
    assert "refresh_token" not in tokens
    assert tokens["expires_in"] == 3600


@pytest.mark.anyio
async def test_full_flow_with_offline_access_yields_refresh_token(
    client: TestClient,
    mem_storage: _InMemoryOIDCStorage,
) -> None:
    """offline_access scope must yield a refresh token alongside the access token."""
    verifier, challenge = _pkce_pair()

    auth_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid profile email offline_access",
            "state": "with-offline",
            "resource": f"{_TEST_APP_URL}/mcp",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(auth_response.headers["location"]).query)["code"][0]

    tokens = _exchange_code(client, code, verifier)

    assert "refresh_token" in tokens
    assert tokens["expires_in"] == 3600
    assert tokens["scope"] == "openid profile email offline_access"

    # The issued refresh token should be tracked in storage as 'active'.
    refresh_token = tokens["refresh_token"]
    row = mem_storage.refresh_tokens[refresh_token]
    assert row.status == "active"


def _refresh_with_token(client: TestClient, refresh_token: str) -> dict[str, Any]:
    secret = oidc_config.get_internal_client_secret()
    response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


@pytest.mark.anyio
async def test_full_flow_refresh_token_rotation(
    client: TestClient,
    app: FastAPI,
    mock_user: SimpleNamespace,
    mem_storage: _InMemoryOIDCStorage,
) -> None:
    """End-to-end refresh: authorize → tokens → rotate → verify new pair."""
    verifier, challenge = _pkce_pair()

    auth_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid profile email offline_access",
            "state": "rot",
            "resource": f"{_TEST_APP_URL}/mcp",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(auth_response.headers["location"]).query)["code"][0]
    initial = _exchange_code(client, code, verifier)
    refresh_a = initial["refresh_token"]

    # Rotate.
    rotated = _refresh_with_token(client, refresh_a)

    assert rotated["access_token"] != initial["access_token"]
    assert rotated["refresh_token"] != refresh_a
    assert rotated["token_type"] == "Bearer"
    assert rotated["expires_in"] == 3600
    # No id_token on refresh per OIDC spec.
    assert "id_token" not in rotated

    # Access token claims preserved.
    claims = signing.verify_jwt(rotated["access_token"])
    assert claims["sub"] == str(mock_user.id)
    assert claims["organization_id"] == str(app.state.org_id)
    assert claims["email"] == mock_user.email

    # Old token is now consumed; new token is active.
    assert mem_storage.refresh_tokens[refresh_a].status == "used"
    new_row = mem_storage.refresh_tokens[rotated["refresh_token"]]
    assert new_row.status == "active"
    # Same family.
    assert new_row.family_id == mem_storage.refresh_tokens[refresh_a].family_id


@pytest.mark.anyio
async def test_full_flow_refresh_token_replay_revokes_family(
    client: TestClient,
    mem_storage: _InMemoryOIDCStorage,
) -> None:
    """Reusing a consumed refresh token revokes both itself and its successor."""
    verifier, challenge = _pkce_pair()

    auth_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "openid profile email offline_access",
            "state": "replay",
            "resource": f"{_TEST_APP_URL}/mcp",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(auth_response.headers["location"]).query)["code"][0]
    initial = _exchange_code(client, code, verifier)
    refresh_a = initial["refresh_token"]

    # Legitimate rotation A -> B.
    rotated = _refresh_with_token(client, refresh_a)
    refresh_b = rotated["refresh_token"]

    # Attacker presents A again — replay.
    secret = oidc_config.get_internal_client_secret()
    replay_response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_a,
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )
    assert replay_response.status_code == 400
    assert replay_response.json()["error"] == "invalid_grant"
    assert "replay" in replay_response.json()["error_description"].lower()

    # Both A and B are now revoked.
    assert mem_storage.refresh_tokens[refresh_a].status == "revoked"
    assert mem_storage.refresh_tokens[refresh_b].status == "revoked"

    # Legitimate user tries to use B — must also fail.
    legit_response = client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_b,
            "client_id": oidc_config.INTERNAL_CLIENT_ID,
            "client_secret": secret,
        },
    )
    assert legit_response.status_code == 400
    assert legit_response.json()["error"] == "invalid_grant"


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
