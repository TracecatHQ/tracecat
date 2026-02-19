from __future__ import annotations

import uuid

import jwt
import pytest

from tracecat.mcp import auth as mcp_auth


def test_mcp_scope_token_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_auth.app_config, "TRACECAT__SERVICE_KEY", "a" * 64)
    org_id = uuid.uuid4()

    token, _ = mcp_auth.mint_mcp_connection_scope_token(
        organization_id=org_id,
        email="alice@example.com",
        ttl_seconds=300,
    )
    claims = mcp_auth.verify_mcp_connection_scope_token(
        token,
        expected_email="alice@example.com",
    )

    assert claims.organization_id == org_id
    assert claims.email == "alice@example.com"


def test_mcp_scope_token_uses_hardcoded_default_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth.app_config, "TRACECAT__SERVICE_KEY", "a" * 64)
    service_key = mcp_auth.app_config.TRACECAT__SERVICE_KEY
    assert service_key is not None
    token, _ = mcp_auth.mint_mcp_connection_scope_token(
        organization_id=uuid.uuid4(),
        email="alice@example.com",
    )
    payload = jwt.decode(
        token,
        service_key,
        algorithms=["HS256"],
        audience=mcp_auth.MCP_SCOPE_TOKEN_AUDIENCE,
        issuer=mcp_auth.MCP_SCOPE_TOKEN_ISSUER,
    )
    assert payload["exp"] - payload["iat"] == mcp_auth.MCP_SCOPE_TOKEN_TTL_SECONDS


def test_mcp_scope_token_email_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_auth.app_config, "TRACECAT__SERVICE_KEY", "a" * 64)
    token, _ = mcp_auth.mint_mcp_connection_scope_token(
        organization_id=uuid.uuid4(),
        email="alice@example.com",
        ttl_seconds=300,
    )

    with pytest.raises(
        ValueError,
        match="does not belong to the authenticated user",
    ):
        mcp_auth.verify_mcp_connection_scope_token(
            token,
            expected_email="bob@example.com",
        )


def test_get_scoped_org_requires_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth, "get_scope_token_from_request", lambda: None)

    with pytest.raises(ValueError, match="missing organization scope"):
        mcp_auth.get_scoped_organization_id_for_request(email="alice@example.com")


def test_create_mcp_auth_uses_jwt_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTH_MODE",
        mcp_auth.MCPAuthMode.OAUTH_CLIENT_CREDENTIALS_JWT,
    )
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    monkeypatch.setenv(
        "FASTMCP_SERVER_AUTH_JWT_JWKS_URI",
        "https://issuer.example.com/.well-known/jwks.json",
    )

    auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, JWTVerifier)


def test_create_mcp_auth_uses_introspection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier

    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTH_MODE",
        mcp_auth.MCPAuthMode.OAUTH_CLIENT_CREDENTIALS_INTROSPECTION,
    )
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    monkeypatch.setenv(
        "FASTMCP_SERVER_AUTH_INTROSPECTION_INTROSPECTION_URL",
        "https://issuer.example.com/oauth/introspect",
    )
    monkeypatch.setenv("FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_ID", "tracecat-mcp")
    monkeypatch.setenv("FASTMCP_SERVER_AUTH_INTROSPECTION_CLIENT_SECRET", "secret")

    auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, IntrospectionTokenVerifier)
