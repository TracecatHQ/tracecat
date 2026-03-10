from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import HTTPConnection

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
    return config


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
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com"
    )
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
    assert getattr(auth, "_fallback_access_token_expiry_seconds", None) == 3600


def test_create_mcp_auth_raises_when_base_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "")
    with pytest.raises(
        ValueError,
        match="TRACECAT_MCP__BASE_URL must be configured for the MCP server",
    ):
        mcp_auth.create_mcp_auth()


def test_create_mcp_auth_raises_when_oidc_issuer_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com"
    )
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


def test_create_mcp_auth_skips_unconfigured_oidc_in_mixed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_auth.mcp_config,
        "TRACECAT_MCP__AUTH_METHODS",
        frozenset({"oidc", "api_key", "none"}),
    )
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com"
    )
    monkeypatch.setattr(mcp_auth.config, "TRACECAT__SERVICE_KEY", "service-secret")

    with (
        patch(
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
        ),
        patch.object(mcp_auth.logger, "warning") as mock_warning,
    ):
        auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, mcp_auth.TracecatMCPAuthProvider)
    assert auth.supports_api_key is True
    assert auth.supports_none is True
    assert auth.server is None
    assert any(
        "skipping OIDC and continuing with remaining auth methods" in call.args[0]
        for call in mock_warning.call_args_list
    )


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


def test_create_mcp_auth_returns_composite_provider_for_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_auth.mcp_config,
        "TRACECAT_MCP__AUTH_METHODS",
        frozenset({"oidc", "api_key"}),
    )
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com"
    )
    monkeypatch.setattr(mcp_auth.config, "TRACECAT__SERVICE_KEY", "service-secret")
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
        patch.object(mcp_auth.logger, "warning") as mock_warning,
    ):
        auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, mcp_auth.TracecatMCPAuthProvider)
    assert auth.supports_api_key is True
    assert any(
        "MCP api_key auth is enabled" in call.args[0]
        for call in mock_warning.call_args_list
    )


def test_create_mcp_auth_raises_when_api_key_enabled_without_service_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__AUTH_METHODS", frozenset({"api_key"})
    )
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com"
    )
    monkeypatch.setattr(mcp_auth.config, "TRACECAT__SERVICE_KEY", "")

    with pytest.raises(
        ValueError,
        match="TRACECAT__SERVICE_KEY must be configured when MCP api_key auth is enabled",
    ):
        mcp_auth.create_mcp_auth()


def test_create_mcp_auth_warns_when_none_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__AUTH_METHODS", frozenset({"none"})
    )
    monkeypatch.setattr(
        mcp_auth.mcp_config, "TRACECAT_MCP__BASE_URL", "https://mcp.example.com"
    )

    with patch.object(mcp_auth.logger, "warning") as mock_warning:
        auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, mcp_auth.TracecatMCPAuthProvider)
    assert auth.supports_none is True
    assert any(
        "highly unsafe and not recommended" in call.args[0]
        for call in mock_warning.call_args_list
    )


def test_get_token_identity_extracts_auth_source_and_bypass_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "T",
        (),
        {
            "client_id": "tracecat-mcp-api-key",
            "scopes": ["*"],
            "claims": {
                "client_id": "tracecat-mcp-api-key",
                "tracecat_mcp_auth_source": "api_key",
                "tracecat_mcp_superuser_bypass": True,
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.auth_source == "api_key"
    assert identity.is_superuser_bypass is True


def test_get_token_identity_does_not_trust_unexpected_bypass_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "T",
        (),
        {
            "client_id": "oidc-client-id",
            "scopes": [],
            "claims": {
                "client_id": "oidc-client-id",
                "tracecat_mcp_auth_source": "oidc",
                "tracecat_mcp_superuser_bypass": "false",
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.auth_source == "oidc"
    assert identity.is_superuser_bypass is False


@pytest.mark.anyio
async def test_tracecat_multi_auth_provider_accepts_api_key_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth.config, "TRACECAT__SERVICE_KEY", "service-secret")
    provider = mcp_auth.TracecatMCPAuthProvider(
        base_url="https://mcp.example.com",
        auth_methods=frozenset({"api_key"}),
        oidc_provider=None,
    )
    verified = await provider.verify_token("service-secret")

    assert verified is not None
    assert verified.client_id == "tracecat-mcp-api-key"
    assert verified.claims["tracecat_mcp_auth_source"] == "api_key"
    assert verified.claims["tracecat_mcp_superuser_bypass"] is True
    assert isinstance(provider, mcp_auth.MultiAuth)


@pytest.mark.anyio
async def test_tracecat_multi_auth_backend_accepts_none_without_auth_header() -> None:
    provider = mcp_auth.TracecatMCPAuthProvider(
        base_url="https://mcp.example.com",
        auth_methods=frozenset({"none"}),
        oidc_provider=None,
    )
    backend = mcp_auth._TracecatMultiAuthBackend(provider)
    conn = HTTPConnection({"type": "http", "headers": []})

    with patch.object(mcp_auth.logger, "warning") as mock_warning:
        result = await backend.authenticate(conn)

    assert result is not None
    credentials, user = result
    assert "*" in credentials.scopes
    claims = cast(mcp_auth.AccessToken, user.access_token).claims
    assert claims["client_id"] == "tracecat-mcp-none"
    assert claims["tracecat_mcp_superuser_bypass"] is True
    assert any(
        "highly unsafe and not recommended" in call.args[0]
        for call in mock_warning.call_args_list
    )


@pytest.mark.anyio
async def test_list_workspaces_for_request_uses_bypass_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = mcp_auth.MCPTokenIdentity(
        client_id="tracecat-mcp-api-key",
        auth_source="api_key",
        is_superuser_bypass=True,
    )

    async def _list_all_workspaces(
        organization_ids: frozenset[uuid.UUID] | None = None,
    ) -> list[dict[str, str]]:
        assert organization_ids is None
        return [{"id": str(uuid.uuid4()), "name": "All Workspaces"}]

    monkeypatch.setattr(mcp_auth, "get_token_identity", lambda: identity)
    monkeypatch.setattr(mcp_auth, "list_all_workspaces", _list_all_workspaces)

    workspaces = await mcp_auth.list_workspaces_for_request()

    assert workspaces[0]["name"] == "All Workspaces"


@pytest.mark.anyio
async def test_resolve_role_for_request_uses_bypass_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid.uuid4()
    identity = mcp_auth.MCPTokenIdentity(
        client_id="tracecat-mcp-none",
        auth_source="none",
        is_superuser_bypass=True,
    )
    expected_role = mcp_auth.Role(
        type="service",
        user_id=None,
        workspace_id=workspace_id,
        organization_id=uuid.uuid4(),
        service_id="tracecat-mcp",
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
    )

    async def _resolve_bypass_role_for_workspace(_workspace_id: uuid.UUID):
        assert _workspace_id == workspace_id
        return expected_role

    monkeypatch.setattr(mcp_auth, "get_token_identity", lambda: identity)
    monkeypatch.setattr(
        mcp_auth,
        "resolve_bypass_role_for_workspace",
        _resolve_bypass_role_for_workspace,
    )

    role = await mcp_auth.resolve_role_for_request(workspace_id)

    assert role == expected_role
