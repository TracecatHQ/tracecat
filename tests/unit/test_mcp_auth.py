from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

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


def test_create_mcp_auth_uses_oidc_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            return_value=MagicMock(),
        ),
    ):
        auth = mcp_auth.create_mcp_auth()

    assert isinstance(auth, mcp_auth.OIDCProxy)


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
