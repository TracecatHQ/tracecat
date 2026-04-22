from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.cimd import CIMDDocument
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl, AnyUrl
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tracecat.mcp import auth as mcp_auth

type AsyncLookup = Callable[..., Awaitable[object]]


def _mock_oidc_discovery_config(
    *, scopes_supported: list[str] | None = None
) -> MagicMock:
    config = MagicMock()
    config.issuer = "https://issuer.example.com"
    config.authorization_endpoint = "https://issuer.example.com/oauth2/authorize"
    config.token_endpoint = "https://issuer.example.com/oauth2/token"
    config.jwks_uri = "https://issuer.example.com/.well-known/jwks.json"
    config.userinfo_endpoint = "https://issuer.example.com/oauth2/userinfo"
    config.scopes_supported = scopes_supported
    config.service_documentation = None
    config.revocation_endpoint = None
    return config


def _build_test_auth(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enable_refresh_tokens: bool = True,
) -> mcp_auth.OIDCProxy:
    monkeypatch.setattr(mcp_auth, "TRACECAT__PUBLIC_APP_URL", "https://mcp.example.com")
    # Mock the internal OIDC issuer config used by _create_oidc_auth
    monkeypatch.setattr(mcp_auth, "INTERNAL_CLIENT_ID", "tracecat-mcp-oidc-internal")
    monkeypatch.setattr(
        mcp_auth,
        "get_internal_client_secret",
        lambda: "test-client-secret",
    )
    monkeypatch.setattr(
        mcp_auth,
        "get_internal_discovery_url",
        lambda: "https://issuer.example.com/.well-known/openid-configuration",
    )
    # Use in-memory store instead of Redis for tests
    monkeypatch.setattr(mcp_auth, "REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(
        "tracecat.config.TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode() if enable_refresh_tokens else "",
    )
    monkeypatch.setattr(mcp_auth.AsyncRedis, "from_url", lambda *a, **kw: MagicMock())

    def _patched_create_oidc_auth(
        _orig=mcp_auth._create_oidc_auth,
    ) -> mcp_auth.OIDCProxy:
        """Wrap _create_oidc_auth to inject an in-memory client_storage."""
        with (
            patch.object(
                mcp_auth.OIDCProxy,
                "get_oidc_configuration",
                return_value=_mock_oidc_discovery_config(),
            ),
            patch(
                "tracecat.mcp.auth.RedisStore",
                return_value=MemoryStore(),
            ),
            patch(
                "tracecat.mcp.auth.PrefixCollectionsWrapper",
                side_effect=lambda store, **kw: store,
            ),
        ):
            return _orig()

    auth = _patched_create_oidc_auth()
    assert isinstance(auth, mcp_auth.OIDCProxy)
    return auth


def _build_test_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enable_refresh_tokens: bool = True,
) -> TestClient:
    auth = _build_test_auth(monkeypatch, enable_refresh_tokens=enable_refresh_tokens)
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
    assert getattr(auth, "_fallback_access_token_expiry_seconds", None) == 24 * 60 * 60


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
    assert payload["scopes_supported"] == [
        "openid",
        "profile",
        "email",
        "offline_access",
    ]
    assert "none" in payload["token_endpoint_auth_methods_supported"]


def test_create_mcp_auth_metadata_preserves_upstream_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    upstream_app = Starlette(routes=mcp_auth.OIDCProxy.get_routes(auth))
    upstream_client = TestClient(upstream_app)
    client = TestClient(
        FastMCP("test", auth=auth).http_app(path="/mcp", transport="streamable-http")
    )

    upstream_metadata = upstream_client.get("/.well-known/oauth-authorization-server")
    metadata_response = client.get("/.well-known/oauth-authorization-server")

    assert upstream_metadata.status_code == 200
    assert metadata_response.status_code == 200

    upstream_payload = upstream_metadata.json()
    payload = metadata_response.json()
    assert payload["client_id_metadata_document_supported"] is True
    assert payload == {
        **upstream_payload,
        "token_endpoint_auth_methods_supported": (
            mcp_auth._MCP_TOKEN_ENDPOINT_AUTH_METHODS
        ),
    }


def test_create_mcp_auth_metadata_omits_refresh_scope_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch, enable_refresh_tokens=False)

    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scopes_supported"] == ["openid", "profile", "email"]


def test_create_mcp_auth_protected_resource_metadata_uses_mcp_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch)

    response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource"] == "https://mcp.example.com/mcp"
    assert payload["authorization_servers"] == ["https://mcp.example.com/"]
    assert payload["scopes_supported"] == [
        "openid",
        "profile",
        "email",
        "offline_access",
    ]


def test_create_mcp_auth_registration_defaults_scope_without_refresh_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch, enable_refresh_tokens=False)

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
    assert registration["scope"] == "openid profile email"


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
    assert registration["scope"] == "openid profile email offline_access"
    assert (
        registration["token_endpoint_auth_method"]
        in metadata["token_endpoint_auth_methods_supported"]
    )


def test_create_mcp_auth_registration_accepts_platform_oidc_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_test_client(monkeypatch)

    registration_response = client.post(
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

    assert registration_response.status_code == 201
    registration = registration_response.json()
    assert registration["scope"] == "openid profile email offline_access"


def test_create_mcp_auth_registration_merges_oidc_scopes_into_partial_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clients that register with a partial scope set are accepted."""
    client = _build_test_client(monkeypatch)

    registration_response = client.post(
        "/register",
        json={
            "client_name": "claude-web",
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid",
        },
    )

    assert registration_response.status_code == 201
    registration = registration_response.json()
    assert registration["scope"] == "openid"


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


def test_merge_scope_string_appends_required_scopes() -> None:
    assert (
        mcp_auth.merge_scope_string("openid", ["profile", "email", "openid"])
        == "openid profile email"
    )


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
    captured: dict[str, object] = {}

    async def _capture_authorize(self, client, params):
        captured["client"] = client
        captured["params"] = params
        return "https://issuer.example.com/oauth2/authorize?state=txn"

    with patch.object(mcp_auth.OIDCProxy, "authorize", _capture_authorize):
        auth = _build_test_auth(monkeypatch)
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
    ]


@pytest.mark.anyio
async def test_create_mcp_auth_authorize_merges_required_scopes_into_custom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorize merges required OIDC scopes even when client only asks for custom."""
    captured: dict[str, object] = {}

    async def _capture_authorize(self, client, params):
        captured["params"] = params
        return "https://issuer.example.com/oauth2/authorize?state=txn"

    with patch.object(mcp_auth.OIDCProxy, "authorize", _capture_authorize):
        auth = _build_test_auth(monkeypatch)
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
    assert forwarded.scopes == ["custom:scope", "openid", "profile", "email"]


@pytest.mark.anyio
async def test_create_mcp_auth_get_client_allows_cimd_loopback_port_variation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    client_id = "https://client.example.com/.well-known/oauth-client.json"
    cimd_client = ProxyDCRClient(
        client_id=client_id,
        client_secret=None,
        redirect_uris=None,
        grant_types=["authorization_code", "refresh_token"],
        scope="openid profile email offline_access",
        token_endpoint_auth_method="none",
        cimd_document=CIMDDocument(
            client_id=AnyHttpUrl(client_id),
            redirect_uris=["http://localhost/callback"],
        ),
    )

    async def _get_client(self, incoming_client_id: str) -> ProxyDCRClient:
        assert incoming_client_id == client_id
        return cimd_client

    monkeypatch.setattr(mcp_auth.OIDCProxy, "get_client", _get_client)

    client = await auth.get_client(client_id)

    assert client is not None
    validated = client.validate_redirect_uri(AnyUrl("http://localhost:52175/callback"))
    assert str(validated) == "http://localhost:52175/callback"


@pytest.mark.anyio
async def test_create_mcp_auth_get_client_merges_required_scopes_for_partial_dcr_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    client_id = "partial-scope-client"
    dcr_client = ProxyDCRClient(
        client_id=client_id,
        client_secret=None,
        redirect_uris=[AnyUrl("http://localhost:3333/callback")],
        grant_types=["authorization_code", "refresh_token"],
        scope="openid",
        token_endpoint_auth_method="none",
    )

    async def _get_client(self, incoming_client_id: str) -> ProxyDCRClient:
        assert incoming_client_id == client_id
        return dcr_client

    monkeypatch.setattr(mcp_auth.OIDCProxy, "get_client", _get_client)

    client = await auth.get_client(client_id)

    assert client is not None
    assert client.scope == "openid profile email"
    assert client.validate_scope("openid") == ["openid"]


@pytest.mark.anyio
async def test_create_mcp_auth_get_client_defaults_cimd_scope_with_offline_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    client_id = "https://client.example.com/.well-known/oauth-client.json"

    async def _fetch(_client_id_url: str) -> CIMDDocument:
        return CIMDDocument(
            client_id=AnyHttpUrl(client_id),
            client_name="Claude Code",
            redirect_uris=["http://localhost/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )

    assert auth._cimd_manager is not None
    monkeypatch.setattr(auth._cimd_manager._fetcher, "fetch", _fetch)

    client = await auth.get_client(client_id)

    assert client is not None
    assert client.scope == "openid profile email offline_access"


@pytest.mark.anyio
async def test_create_mcp_auth_register_client_stores_required_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    captured: dict[str, object] = {}
    client_info = OAuthClientInformationFull(
        client_id="partial-scope-client",
        redirect_uris=[AnyUrl("http://localhost:3333/callback")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="openid",
    )

    async def _register_client(
        self, incoming_client_info: OAuthClientInformationFull
    ) -> None:
        captured["scope"] = incoming_client_info.scope

    monkeypatch.setattr(mcp_auth.OIDCProxy, "register_client", _register_client)

    await auth.register_client(client_info)

    assert captured["scope"] == "openid profile email"


@pytest.mark.anyio
async def test_create_mcp_auth_get_client_keeps_cimd_path_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    client_id = "https://client.example.com/.well-known/oauth-client.json"
    cimd_client = ProxyDCRClient(
        client_id=client_id,
        client_secret=None,
        redirect_uris=None,
        grant_types=["authorization_code", "refresh_token"],
        scope="openid profile email offline_access",
        token_endpoint_auth_method="none",
        cimd_document=CIMDDocument(
            client_id=AnyHttpUrl(client_id),
            redirect_uris=["http://localhost/callback"],
        ),
    )

    async def _get_client(self, incoming_client_id: str) -> ProxyDCRClient:
        assert incoming_client_id == client_id
        return cimd_client

    monkeypatch.setattr(mcp_auth.OIDCProxy, "get_client", _get_client)

    client = await auth.get_client(client_id)

    assert client is not None
    with pytest.raises(
        mcp_auth.InvalidRedirectUriError,
        match="does not match CIMD redirect_uris",
    ):
        client.validate_redirect_uri(AnyUrl("http://localhost:52175/other"))


@pytest.mark.anyio
async def test_extract_upstream_claims_falls_back_to_userinfo_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"sub": "user-123", "email": " user@example.com "}

    class _AsyncClient:
        async def __aenter__(self) -> _AsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> _Response:
            assert url == "https://issuer.example.com/oauth2/userinfo"
            assert headers == {"Authorization": "Bearer upstream-access-token"}
            return _Response()

    async def _resolve_user_by_email(email: str) -> SimpleNamespace:
        assert email == "user@example.com"
        return SimpleNamespace(id=uuid.uuid4(), is_superuser=False)

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(mcp_auth, "resolve_user_by_email", _resolve_user_by_email)

    claims = await auth._extract_upstream_claims(
        {
            "id_token": "header.eyJzdWIiOiAidXNlci0xMjMifQ.signature",
            "access_token": "upstream-access-token",
        }
    )

    assert claims == {"email": "user@example.com"}


@pytest.mark.anyio
async def test_extract_upstream_claims_rejects_mismatched_userinfo_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"sub": "different-user", "email": " user@example.com "}

    class _AsyncClient:
        async def __aenter__(self) -> _AsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> _Response:
            assert url == "https://issuer.example.com/oauth2/userinfo"
            assert headers == {"Authorization": "Bearer upstream-access-token"}
            return _Response()

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", _AsyncClient)

    with pytest.raises(mcp_auth.TokenError) as exc_info:
        await auth._extract_upstream_claims(
            {
                "id_token": "header.eyJzdWIiOiAidXNlci0xMjMifQ.signature",
                "access_token": "upstream-access-token",
            }
        )

    assert exc_info.value.error == "invalid_client"
    assert exc_info.value.error_description is not None
    assert (
        "No email claim in internal issuer tokens" in exc_info.value.error_description
    )


@pytest.mark.anyio
async def test_extract_upstream_claims_maps_userinfo_failure_to_invalid_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)

    class _AsyncClient:
        async def __aenter__(self) -> _AsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> None:
            assert url == "https://issuer.example.com/oauth2/userinfo"
            assert headers == {"Authorization": "Bearer upstream-access-token"}
            raise RuntimeError("userinfo timeout")

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", _AsyncClient)

    with pytest.raises(mcp_auth.TokenError) as exc_info:
        await auth._extract_upstream_claims(
            {
                "id_token": "header.eyJzdWIiOiAidXNlci0xMjMifQ.signature",
                "access_token": "upstream-access-token",
            }
        )

    assert exc_info.value.error == "invalid_grant"
    assert (
        exc_info.value.error_description
        == "Failed to resolve OIDC email claims from internal issuer"
    )


@pytest.mark.anyio
async def test_extract_upstream_claims_allows_missing_id_token_with_userinfo_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"email": " refresh@example.com "}

    class _AsyncClient:
        async def __aenter__(self) -> _AsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> _Response:
            assert url == "https://issuer.example.com/oauth2/userinfo"
            assert headers == {"Authorization": "Bearer refreshed-access-token"}
            return _Response()

    async def _resolve_user_by_email(email: str) -> SimpleNamespace:
        assert email == "refresh@example.com"
        return SimpleNamespace(id=uuid.uuid4(), is_superuser=False)

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(mcp_auth, "resolve_user_by_email", _resolve_user_by_email)

    claims = await auth._extract_upstream_claims(
        {
            "access_token": "refreshed-access-token",
        }
    )

    assert claims == {"email": "refresh@example.com"}


@pytest.mark.anyio
async def test_load_access_token_preserves_fastmcp_upstream_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _build_test_auth(monkeypatch)
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    validated = AccessToken(
        token="upstream-access-token",
        client_id="",
        scopes=["openid", "profile", "email"],
        claims={"sub": "upstream-subject"},
    )

    async def _load_access_token(self, token: str) -> AccessToken:
        assert token == "fastmcp-jwt"
        return validated

    monkeypatch.setattr(mcp_auth.OIDCProxy, "load_access_token", _load_access_token)
    # jwt_issuer is a property backed by _jwt_issuer; use monkeypatch to set it.
    monkeypatch.setattr(
        auth,
        "_jwt_issuer",
        SimpleNamespace(
            verify_token=lambda token: {
                "client_id": "tracecat-client",
                "scope": f"organization:{org_id} workspace:{ws_id}",
                "upstream_claims": {"email": " user@example.com "},
            }
        ),
    )

    merged = cast(AccessToken, await auth.load_access_token("fastmcp-jwt"))
    assert merged.client_id == "tracecat-client"
    assert merged.scopes == [f"organization:{org_id}", f"workspace:{ws_id}"]
    assert merged.claims["email"] == "user@example.com"
    assert merged.claims["upstream_claims"] == {"email": " user@example.com "}


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


def test_get_token_identity_reads_email_from_upstream_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = type(
        "T",
        (),
        {
            "client_id": "tracecat-client",
            "scopes": [],
            "claims": {
                "client_id": "tracecat-client",
                "upstream_claims": {"email": " user@example.com "},
            },
        },
    )()
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)

    identity = mcp_auth.get_token_identity()

    assert identity.client_id == "tracecat-client"
    assert identity.email == "user@example.com"


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


def test_get_token_identity_requires_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: None)

    with pytest.raises(ValueError, match="Authentication required"):
        mcp_auth.get_token_identity()


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


@pytest.mark.anyio
async def test_list_workspaces_for_request_reads_email_from_upstream_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    token = type(
        "T",
        (),
        {
            "client_id": "tracecat-client",
            "scopes": [],
            "claims": {
                "client_id": "tracecat-client",
                "upstream_claims": {"email": " user@example.com "},
            },
        },
    )()

    async def _list_user_workspaces(
        email: str,
        organization_ids: frozenset[uuid.UUID] | None = None,
    ) -> list[dict[str, str]]:
        captured["email"] = email
        captured["organization_ids"] = organization_ids
        return []

    monkeypatch.setattr(mcp_auth, "get_access_token", lambda: token)
    monkeypatch.setattr(mcp_auth, "list_user_workspaces", _list_user_workspaces)

    await mcp_auth.list_workspaces_for_request()

    assert captured["email"] == "user@example.com"
    assert captured["organization_ids"] is None


@pytest.mark.anyio
async def test_resolve_role_allows_superuser_without_org_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    async def _resolve_user_by_email(_email: str) -> SimpleNamespace:
        return SimpleNamespace(id=user_id, is_superuser=True)

    async def _resolve_workspace_org(_workspace_id: uuid.UUID) -> uuid.UUID:
        assert _workspace_id == workspace_id
        return organization_id

    async def _compute_effective_scopes(_role) -> frozenset[str]:
        return frozenset({"*"})

    async def _resolve_workspace_membership(
        _user_id: uuid.UUID, _workspace_id: uuid.UUID
    ) -> None:
        raise AssertionError("superusers should not require workspace membership")

    monkeypatch.setattr(mcp_auth, "resolve_user_by_email", _resolve_user_by_email)
    monkeypatch.setattr(mcp_auth, "resolve_workspace_org", _resolve_workspace_org)
    monkeypatch.setattr(mcp_auth, "compute_effective_scopes", _compute_effective_scopes)
    monkeypatch.setattr(
        mcp_auth,
        "resolve_workspace_membership",
        _resolve_workspace_membership,
    )

    role = await mcp_auth.resolve_role("user@example.com", workspace_id)

    assert role.user_id == user_id
    assert role.workspace_id == workspace_id
    assert role.organization_id == organization_id
    assert role.scopes == frozenset({"*"})


@pytest.mark.anyio
async def test_resolve_role_allows_direct_workspace_membership_without_org_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def _resolve_user_by_email(_email: str) -> SimpleNamespace:
        return SimpleNamespace(id=user_id, is_superuser=False)

    async def _resolve_workspace_org(_workspace_id: uuid.UUID) -> uuid.UUID:
        return organization_id

    async def _compute_effective_scopes(_role) -> frozenset[str]:
        return frozenset()

    async def _resolve_workspace_membership(
        user_id_arg: uuid.UUID, workspace_id_arg: uuid.UUID
    ) -> None:
        captured["user_id"] = user_id_arg
        captured["workspace_id"] = workspace_id_arg

    monkeypatch.setattr(mcp_auth, "resolve_user_by_email", _resolve_user_by_email)
    monkeypatch.setattr(mcp_auth, "resolve_workspace_org", _resolve_workspace_org)
    monkeypatch.setattr(mcp_auth, "compute_effective_scopes", _compute_effective_scopes)
    monkeypatch.setattr(
        mcp_auth,
        "resolve_workspace_membership",
        _resolve_workspace_membership,
    )

    role = await mcp_auth.resolve_role("user@example.com", workspace_id)

    assert captured == {"user_id": user_id, "workspace_id": workspace_id}
    assert role.organization_id == organization_id
    assert role.workspace_id == workspace_id
    assert role.scopes == frozenset()


@pytest.mark.anyio
async def test_list_user_workspaces_includes_direct_memberships_without_org_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    class _ScalarResult:
        def __init__(self, values: list[uuid.UUID]) -> None:
            self._values = values

        def all(self) -> list[uuid.UUID]:
            return self._values

    class _TupleResult:
        def __init__(self, values: list[tuple[uuid.UUID, str]]) -> None:
            self._values = values

        def all(self) -> list[tuple[uuid.UUID, str]]:
            return self._values

    class _Result:
        def __init__(
            self,
            *,
            scalars: list[uuid.UUID] | None = None,
            tuples: list[tuple[uuid.UUID, str]] | None = None,
        ) -> None:
            self._scalars = scalars or []
            self._tuples = tuples or []

        def scalars(self) -> _ScalarResult:
            return _ScalarResult(self._scalars)

        def tuples(self) -> _TupleResult:
            return _TupleResult(self._tuples)

    class _Session:
        def __init__(self) -> None:
            self._calls = 0

        async def execute(self, _stmt):
            self._calls += 1
            if self._calls == 1:
                return _Result(scalars=[])
            if self._calls == 2:
                return _Result(tuples=[(workspace_id, "Workspace A")])
            raise AssertionError("unexpected extra query")

    class _AsyncContext:
        def __init__(self, session: _Session) -> None:
            self._session = session

        async def __aenter__(self) -> _Session:
            return self._session

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    async def _resolve_user_by_email(_email: str) -> SimpleNamespace:
        return SimpleNamespace(id=user_id, is_superuser=False)

    monkeypatch.setattr(mcp_auth, "resolve_user_by_email", _resolve_user_by_email)
    monkeypatch.setattr(
        mcp_auth,
        "get_async_session_bypass_rls_context_manager",
        lambda: _AsyncContext(_Session()),
    )

    workspaces = await mcp_auth.list_user_workspaces("user@example.com")

    assert workspaces == [{"id": str(workspace_id), "name": "Workspace A"}]


@pytest.mark.anyio
async def test_mcp_pre_role_lookups_use_bypass_session_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BypassSessionUsed(Exception):
        pass

    class _AsyncContext:
        async def __aenter__(self) -> None:
            raise BypassSessionUsed

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    async def _resolve_user_by_email(_email: str) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4(), is_superuser=False)

    cases: list[tuple[str, AsyncLookup, tuple[object, ...], bool]] = [
        (
            "resolve_user_by_email",
            mcp_auth.resolve_user_by_email,
            ("user@example.com",),
            False,
        ),
        (
            "resolve_org_membership",
            mcp_auth.resolve_org_membership,
            (uuid.uuid4(), uuid.uuid4()),
            False,
        ),
        (
            "resolve_workspace_org",
            mcp_auth.resolve_workspace_org,
            (uuid.uuid4(),),
            False,
        ),
        (
            "resolve_workspace_membership",
            mcp_auth.resolve_workspace_membership,
            (uuid.uuid4(), uuid.uuid4()),
            False,
        ),
        (
            "list_user_workspaces",
            mcp_auth.list_user_workspaces,
            ("user@example.com",),
            True,
        ),
    ]

    for _, fn, args, stub_user_lookup in cases:
        call_count = 0

        def _bypass_session_manager() -> _AsyncContext:
            nonlocal call_count
            call_count += 1
            return _AsyncContext()

        with monkeypatch.context() as patch_ctx:
            patch_ctx.setattr(
                mcp_auth,
                "get_async_session_bypass_rls_context_manager",
                _bypass_session_manager,
            )
            if stub_user_lookup:
                patch_ctx.setattr(
                    mcp_auth,
                    "resolve_user_by_email",
                    _resolve_user_by_email,
                )

            with pytest.raises(BypassSessionUsed):
                await fn(*args)

        assert call_count == 1
