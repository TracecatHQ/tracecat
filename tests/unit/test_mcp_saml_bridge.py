from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tracecat.mcp import saml_bridge, saml_bridge_state


def test_patch_oauth_metadata_route_strips_refresh_support() -> None:
    async def metadata_endpoint(request):
        return JSONResponse(
            {
                "scopes_supported": [
                    "openid",
                    "profile",
                    "email",
                    "offline_access",
                ],
                "grant_types_supported": [
                    "authorization_code",
                    "refresh_token",
                ],
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            }
        )

    route = Route(
        "/.well-known/oauth-authorization-server",
        endpoint=metadata_endpoint,
        methods=["GET"],
    )
    route.app = saml_bridge._patch_oauth_metadata_route(route.app)
    client = TestClient(Starlette(routes=[route]))

    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    assert response.json() == {
        "scopes_supported": ["openid", "profile", "email"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def test_patch_oauth_register_route_strips_offline_access() -> None:
    async def register_endpoint(request):
        return JSONResponse(
            {
                **await request.json(),
                "grant_types": ["authorization_code", "refresh_token"],
                "scope": "openid profile email offline_access",
            }
        )

    route = Route("/register", endpoint=register_endpoint, methods=["POST"])
    route.app = saml_bridge._patch_oauth_register_route(route.app)
    client = TestClient(Starlette(routes=[route]))

    response = client.post(
        "/register",
        json={
            "client_name": "codex-test",
            "redirect_uris": ["http://localhost:3333/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid profile email offline_access",
        },
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "openid profile email"
    assert response.json()["grant_types"] == ["authorization_code"]


def test_patch_oauth_authorize_route_strips_offline_access_from_query() -> None:
    async def authorize_endpoint(request):
        return JSONResponse({"scope": request.query_params.get("scope")})

    route = Route("/authorize", endpoint=authorize_endpoint, methods=["GET"])
    route.app = saml_bridge._patch_oauth_authorize_route(route.app)
    client = TestClient(Starlette(routes=[route]))

    response = client.get(
        "/authorize?scope=openid+profile+email+offline_access&client_id=test-client"
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "openid profile email"


def test_build_identify_html_escapes_user_controlled_fields() -> None:
    html = saml_bridge._build_identify_html(
        '" onfocus="alert(1)',
        'user@example.com"><script>alert(1)</script>',
    )

    assert 'value="&quot; onfocus=&quot;alert(1)"' in html
    assert "user@example.com&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "only supports the SAML bridge flow" not in html


def test_build_error_html_escapes_message() -> None:
    response = saml_bridge._build_error_html(
        '<img src=x onerror="alert(1)">',
        status_code=418,
    )

    assert response.status_code == 418
    response_body = bytes(response.body).decode()
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in response_body
    assert '<img src=x onerror="alert(1)">' not in response_body


def test_saml_bridge_provider_requires_db_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(saml_bridge.config, "TRACECAT__DB_ENCRYPTION_KEY", "")

    with pytest.raises(
        ValueError,
        match="TRACECAT__DB_ENCRYPTION_KEY must be configured for MCP SAML bridge auth",
    ):
        saml_bridge.TracecatSAMLBridgeAuthProvider(base_url="https://mcp.example.com")


def test_normalize_loopback_browser_callback_rewrites_localhost_to_ipv4() -> None:
    normalized = saml_bridge_state._normalize_loopback_browser_callback(
        "http://localhost:3333/callback?code=test"
    )

    assert normalized == "http://127.0.0.1:3333/callback?code=test"


def test_normalize_loopback_browser_callback_keeps_non_localhost_uris() -> None:
    normalized = saml_bridge_state._normalize_loopback_browser_callback(
        "https://app.tracecat.com/callback?code=test"
    )

    assert normalized == "https://app.tracecat.com/callback?code=test"


@pytest.mark.anyio
async def test_complete_saml_mcp_transaction_is_single_use() -> None:
    transaction_id = "txn-123"
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    stores = Mock()
    stores.transactions.get = AsyncMock(
        return_value=saml_bridge_state.SAMLMCPAuthTransaction(
            id=transaction_id,
            client_id="client-123",
            client_redirect_uri=AnyUrl("http://localhost:3333/callback"),
            client_state="client-state",
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            scopes=["openid", "profile", "email"],
            created_at=1.0,
            expires_at=1000.0,
            email="user@example.com",
            organization_id=organization_id,
            user_id=user_id,
            authenticated_at=2.0,
        )
    )
    stores.transactions.delete = AsyncMock()
    stores.sessions.put = AsyncMock()
    stores.codes.put = AsyncMock()
    stores.redis.set = AsyncMock(side_effect=[True, None])

    with (
        patch.object(
            saml_bridge_state,
            "revoke_prior_saml_mcp_sessions",
            AsyncMock(),
        ),
        patch.object(
            saml_bridge_state.uuid,
            "uuid4",
            return_value=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        ),
        patch.object(
            saml_bridge_state.secrets,
            "token_urlsafe",
            side_effect=["auth-code", "session-jti"],
        ),
        patch.object(saml_bridge_state.time, "time", return_value=100.0),
    ):
        redirect_url = await saml_bridge_state.complete_saml_mcp_transaction(
            stores=stores,
            transaction_id=transaction_id,
            access_token_ttl_seconds=3600,
            auth_code_ttl_seconds=300,
        )
        duplicate_redirect_url = await saml_bridge_state.complete_saml_mcp_transaction(
            stores=stores,
            transaction_id=transaction_id,
            access_token_ttl_seconds=3600,
            auth_code_ttl_seconds=300,
        )

    assert (
        redirect_url
        == "http://127.0.0.1:3333/callback?code=auth-code&state=client-state"
    )
    assert duplicate_redirect_url is None
    stores.sessions.put.assert_awaited_once()
    stores.codes.put.assert_awaited_once()
    stores.transactions.delete.assert_awaited_once_with(transaction_id)
    assert stores.redis.set.await_count == 2
