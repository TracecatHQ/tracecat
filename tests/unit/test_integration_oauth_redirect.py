"""Tests for integration OAuth callback redirect targets."""

import uuid

import pytest

from tracecat import config
from tracecat.integrations.providers.github.mcp import GitHubMCPProvider
from tracecat.integrations.providers.github.oauth import GitHubOAuthProvider
from tracecat.integrations.router import _oauth_callback_redirect_url


def test_oauth_callback_redirect_url_uses_integrations_for_oauth_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://tracecat.test")
    workspace_id = uuid.uuid4()

    redirect_url = _oauth_callback_redirect_url(
        provider_impl=GitHubOAuthProvider,
        workspace_id=workspace_id,
    )

    assert (
        redirect_url == f"https://tracecat.test/workspaces/{workspace_id}/integrations"
    )


def test_oauth_callback_redirect_url_uses_mcp_servers_for_mcp_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://tracecat.test")
    workspace_id = uuid.uuid4()

    redirect_url = _oauth_callback_redirect_url(
        provider_impl=GitHubMCPProvider,
        workspace_id=workspace_id,
    )

    assert (
        redirect_url == f"https://tracecat.test/workspaces/{workspace_id}/mcp-servers"
    )
