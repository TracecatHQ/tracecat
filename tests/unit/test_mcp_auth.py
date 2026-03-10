from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.testclient import TestClient

from tracecat.mcp import auth as mcp_auth


def _mock_oidc_discovery_config(
    *, scopes_supported: list[str] | None = None
) -> MagicMock:
    config = MagicMock()
    config.issuer = "https://issuer.example.com"
    config.authorization_endpoint = "https://issuer.example.com/oauth2/authorize"
    config.token_endpoint = "https://issuer.example.com/oauth2/token"
    config.jwks_uri = "https://issuer.example.com/.well-known/jwks.json"
    config.scopes_supported = scopes_supported
    config.service_documentation = None
    config.revocation_endpoint = None
    return config


def _build_test_auth(monkeypatch: pytest.MonkeyPatch) -> mcp_auth.OIDCProxy:
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    with (
        patch(
            "tracecat.mcp.auth.get_platform_oidc_config",
            return_value=type(
                "OIDCConfig",
                (),
                {
                    "issuer": "https://issuer.example.com",
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                },
            )(),
        ),
        patch.object(
            mcp_auth.OIDCProxy,
            "get_oidc_configuration",
            return_value=_mock_oidc_discovery_config(),
        ),
    ):
        auth = mcp_auth.create_mcp_auth()
    assert isinstance(auth, mcp_auth.OIDCProxy)
    return auth


def _build_test_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    auth = _build_test_auth(monkeypatch)
    mcp = FastMCP("test", auth=auth)
    app = mcp.http_app(path="/mcp", transport="streamable-http")
    return TestClient(app)


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


def test_create_mcp_auth_uses_oidc_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)

    assert isinstance(auth, mcp_auth.OIDCProxy)
    assert getattr(auth, "_fallback_access_token_expiry_seconds", None) == 3600


def test_create_mcp_auth_metadata_advertises_public_client_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch)

    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authorization_endpoint"] == "https://mcp.example.com/authorize"
    assert payload["token_endpoint"] == "https://mcp.example.com/token"
    assert payload["registration_endpoint"] == "https://mcp.example.com/register"
    assert "none" in payload["token_endpoint_auth_methods_supported"]


def test_create_mcp_auth_protected_resource_metadata_uses_mcp_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch)

    response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == "https://mcp.example.com/mcp"
    assert payload["authorization_servers"] == ["https://mcp.example.com/"]


def test_create_mcp_auth_metadata_matches_public_client_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch)

    metadata_response = client.get("/.well-known/oauth-authorization-server")
    assert metadata_response.status_code == 200
    metadata = metadata_response.json()

    registration_response = client.post(
        "/register",
        json={
            "client_name": "codex-test",
            "redirect_uris": ["http://localhost:3333/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )

    assert registration_response.status_code == 201
    registration = registration_response.json()
    assert registration["token_endpoint_auth_method"] == "none"
    assert registration.get("client_secret") is None
    assert (
        registration["token_endpoint_auth_method"]
        in metadata["token_endpoint_auth_methods_supported"]
    )


def test_create_mcp_auth_raises_when_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "")
    with pytest.raises(
        ValueError,
        match="TRACECAT_MCP__BASE_URL must be configured for the MCP server",
    ):
        mcp_auth.create_mcp_auth()


def test_create_mcp_auth_raises_when_oidc_issuer_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    with patch(
        "tracecat.mcp.auth.get_platform_oidc_config",
        return_value=type(
            "OIDCConfig",
            (),
            {
                "issuer": "",
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
        )(),
    ):
        with pytest.raises(
            ValueError,
            match="OIDC_ISSUER must be configured for the MCP server",
        ):
            mcp_auth.create_mcp_auth()


def test_append_scope_if_missing_adds_unique_scope() -> None:
    scopes = ["openid", "profile"]
    assert mcp_auth.append_scope_if_missing(scopes, "offline_access") == [
        "openid",
        "profile",
        "offline_access",
    ]


def test_append_scope_if_missing_does_not_duplicate_scope() -> None:
    scopes = ["openid", "offline_access"]
    assert mcp_auth.append_scope_if_missing(scopes, "offline_access") == scopes


def test_merge_unique_scopes_preserves_order_and_uniqueness() -> None:
    scopes = ["scope:a", "scope:b"]
    assert mcp_auth.merge_unique_scopes(scopes, ["scope:b", "scope:c"]) == [
        "scope:a",
        "scope:b",
        "scope:c",
    ]


def test_remove_scope_removes_only_target_scope() -> None:
    scopes = ["openid", "offline_access", "email"]
    assert mcp_auth.remove_scope(scopes, "offline_access") == ["openid", "email"]


def test_supports_refresh_scope_when_provider_metadata_missing() -> None:
    assert mcp_auth.supports_refresh_scope(None) is True


def test_supports_refresh_scope_when_provider_advertises_no_scopes() -> None:
    assert mcp_auth.supports_refresh_scope([]) is False


def test_supports_refresh_scope_when_scope_supported() -> None:
    assert mcp_auth.supports_refresh_scope(["openid", "offline_access"]) is True


def test_supports_refresh_scope_when_scope_not_supported() -> None:
    assert mcp_auth.supports_refresh_scope(["openid", "profile", "email"]) is False


@pytest.mark.anyio
async def test_create_mcp_auth_authorize_includes_platform_oidc_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    captured: dict[str, object] = {}

    async def _capture_authorize(self, client, params):
        captured["client"] = client
        captured["params"] = params
        return "https://issuer.example.com/oauth2/authorize?state=txn"

    with (
        patch(
            "tracecat.mcp.auth.get_platform_oidc_config",
            return_value=type(
                "OIDCConfig",
                (),
                {
                    "issuer": "https://issuer.example.com",
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "scopes": ("openid", "profile", "email"),
                },
            )(),
        ),
        patch.object(
            mcp_auth.OIDCProxy,
            "get_oidc_configuration",
            return_value=_mock_oidc_discovery_config(
                scopes_supported=["openid", "profile", "email", "offline_access"]
            ),
        ),
        patch.object(mcp_auth.OIDCProxy, "authorize", _capture_authorize),
    ):
        auth = mcp_auth.create_mcp_auth()
        assert isinstance(auth, mcp_auth.OIDCProxy)
        client = OAuthClientInformationFull(
            client_id="cursor-client",
            redirect_uris=[AnyUrl("cursor://anysphere.cursor-mcp/oauth/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        params = AuthorizationParams(
            state="txn",
            scopes=["custom:scope"],
            code_challenge="challenge",
            redirect_uri=AnyUrl("cursor://anysphere.cursor-mcp/oauth/callback"),
            redirect_uri_provided_explicitly=True,
            resource="https://mcp.example.com/mcp",
        )

        await auth.authorize(client, params)

    forwarded = captured["params"]
    assert isinstance(forwarded, AuthorizationParams)
    assert forwarded.scopes == [
        "custom:scope",
        "openid",
        "profile",
        "email",
        "offline_access",
    ]


@pytest.mark.anyio
async def test_create_mcp_auth_authorize_strips_offline_access_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com")
    captured: dict[str, object] = {}

    async def _capture_authorize(self, client, params):
        captured["params"] = params
        return "https://issuer.example.com/oauth2/authorize?state=txn"

    with (
        patch(
            "tracecat.mcp.auth.get_platform_oidc_config",
            return_value=type(
                "OIDCConfig",
                (),
                {
                    "issuer": "https://issuer.example.com",
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "scopes": ("openid", "profile", "offline_access"),
                },
            )(),
        ),
        patch.object(
            mcp_auth.OIDCProxy,
            "get_oidc_configuration",
            return_value=_mock_oidc_discovery_config(
                scopes_supported=["openid", "profile"]
            ),
        ),
        patch.object(mcp_auth.OIDCProxy, "authorize", _capture_authorize),
    ):
        auth = mcp_auth.create_mcp_auth()
        assert isinstance(auth, mcp_auth.OIDCProxy)
        client = OAuthClientInformationFull(
            client_id="codex-client",
            redirect_uris=[AnyUrl("http://localhost:3333/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        params = AuthorizationParams(
            state="txn",
            scopes=["custom:scope"],
            code_challenge="challenge",
            redirect_uri=AnyUrl("http://localhost:3333/callback"),
            redirect_uri_provided_explicitly=True,
            resource="https://mcp.example.com/mcp",
        )

        await auth.authorize(client, params)

    forwarded = captured["params"]
    assert isinstance(forwarded, AuthorizationParams)
    assert forwarded.scopes is not None
    assert "offline_access" not in forwarded.scopes
    assert forwarded.scopes == ["custom:scope", "openid", "profile"]


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


def test_get_token_identity_prefers_token_client_id_over_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "T",
        (),
        {
            "client_id": "oauth-client-id",
            "scopes": [],
            "claims": {
                "sub": "user-subject-id",
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.client_id == "oauth-client-id"


def test_get_token_identity_falls_back_to_sub_when_no_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "T",
        (),
        {
            "client_id": "",
            "scopes": [],
            "claims": {
                "sub": "user-subject-id",
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.client_id == "user-subject-id"


def test_get_token_identity_handles_null_token_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "T",
        (),
        {
            "client_id": None,
            "scopes": [],
            "claims": {
                "sub": "user-subject-id",
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.client_id == "user-subject-id"


@pytest.mark.anyio
async def test_list_workspaces_for_request_passes_claimed_org_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id_1 = uuid.uuid4()
    org_id_2 = uuid.uuid4()
    captured: dict[str, object] = {}

    identity = mcp_auth.MCPTokenIdentity(
        client_id="tracecat-client",
        email="user@example.com",
        organization_ids=frozenset({org_id_1, org_id_2}),
    )

    async def _list_user_workspaces(
        email: str,
        organization_ids: frozenset[uuid.UUID] | None = None,
    ) -> list[dict[str, str]]:
        captured["email"] = email
        captured["organization_ids"] = organization_ids
        return []

    monkeypatch.setattr(mcp_auth, "get_token_identity", lambda: identity)
    monkeypatch.setattr(mcp_auth, "list_user_workspaces", _list_user_workspaces)

    await mcp_auth.list_workspaces_for_request()

    assert captured["email"] == "user@example.com"
    assert captured["organization_ids"] == frozenset({org_id_1, org_id_2})


@pytest.mark.anyio
async def test_list_workspaces_for_request_without_claimed_org_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    identity = mcp_auth.MCPTokenIdentity(
        client_id="tracecat-client",
        email="user@example.com",
    )

    async def _list_user_workspaces(
        email: str,
        organization_ids: frozenset[uuid.UUID] | None = None,
    ) -> list[dict[str, str]]:
        captured["email"] = email
        captured["organization_ids"] = organization_ids
        return []

    monkeypatch.setattr(mcp_auth, "get_token_identity", lambda: identity)
    monkeypatch.setattr(mcp_auth, "list_user_workspaces", _list_user_workspaces)

    await mcp_auth.list_workspaces_for_request()

    assert captured["email"] == "user@example.com"
    assert captured["organization_ids"] is None
