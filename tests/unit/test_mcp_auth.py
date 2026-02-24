from __future__ import annotations

import uuid

import pytest

from tracecat.mcp import auth as mcp_auth


def test_oidc_consent_html_escapes_values() -> None:
    page = mcp_auth._build_oidc_consent_html(
        client_id='client-"one"',
        redirect_uri="http://localhost:3333/cb?x=<x>",
        scopes=["openid", 'profile"admin"'],
        txn_id='txn-"id"',
        csrf_token='csrf-"token"',
    )

    assert "Authorize MCP client" in page
    assert "client-&quot;one&quot;" in page
    assert "http://localhost:3333/cb?x=&lt;x&gt;" in page
    assert "profile&quot;admin&quot;" in page
    assert 'name="txn_id" value="txn-&quot;id&quot;"' in page
    assert 'name="csrf_token" value="csrf-&quot;token&quot;"' in page
    assert 'aria-label="Tracecat"' in page


def test_create_mcp_auth_uses_jwt_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth import RemoteAuthProvider

    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTH_MODE",
        mcp_auth.MCPAuthMode.OAUTH_CLIENT_CREDENTIALS_JWT,
    )
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTHORIZATION_SERVER_URL",
        "https://issuer.example.com",
    )
    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__JWT_JWKS_URI",
        "https://issuer.example.com/.well-known/jwks.json",
    )

    auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, RemoteAuthProvider)


def test_create_mcp_auth_uses_introspection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.server.auth import RemoteAuthProvider

    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTH_MODE",
        mcp_auth.MCPAuthMode.OAUTH_CLIENT_CREDENTIALS_INTROSPECTION,
    )
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTHORIZATION_SERVER_URL",
        "https://issuer.example.com",
    )
    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__INTROSPECTION_URL",
        "https://issuer.example.com/oauth/introspect",
    )
    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__INTROSPECTION_CLIENT_ID",
        "tracecat-mcp",
    )
    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__INTROSPECTION_CLIENT_SECRET",
        "secret",
    )

    auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, RemoteAuthProvider)


def test_get_token_identity_extracts_ids_from_claims_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    extra_org_id = uuid.uuid4()
    extra_ws_id = uuid.uuid4()

    token = type(
        "T",
        (),
        {
            "client_id": "tracecat-client",
            "scopes": [f"organization:{extra_org_id}", f"workspace:{extra_ws_id}"],
            "claims": {
                "sub": "tracecat-client",
                "organization_id": str(org_id),
                "workspace_id": str(ws_id),
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.client_id == "tracecat-client"
    assert identity.email is None
    assert identity.organization_ids == frozenset({org_id, extra_org_id})
    assert identity.workspace_ids == frozenset({ws_id, extra_ws_id})


@pytest.mark.anyio
async def test_resolve_role_for_request_client_credentials_without_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws_id = uuid.uuid4()
    org_id = uuid.uuid4()

    monkeypatch.setattr(
        mcp_auth,
        "TRACECAT_MCP__AUTH_MODE",
        mcp_auth.MCPAuthMode.OAUTH_CLIENT_CREDENTIALS_JWT,
    )

    async def _resolve_workspace_org(_workspace_id: uuid.UUID) -> uuid.UUID:
        return org_id

    monkeypatch.setattr(mcp_auth, "resolve_workspace_org", _resolve_workspace_org)
    monkeypatch.setattr(
        mcp_auth,
        "get_token_identity",
        lambda: mcp_auth.MCPTokenIdentity(
            client_id="tracecat-client",
            organization_ids=frozenset({org_id}),
            workspace_ids=frozenset({ws_id}),
        ),
    )

    role = await mcp_auth.resolve_role_for_request(ws_id)

    assert role.type == "service"
    assert role.service_id == "tracecat-mcp"
    assert role.user_id is None
    assert role.workspace_id == ws_id
    assert role.organization_id == org_id
