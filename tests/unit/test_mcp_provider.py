import pytest
from pydantic import SecretStr

from tracecat.integrations.providers.base import (
    MCPAuthProvider,
    OAuthDiscoveryResult,
)
from tracecat.integrations.schemas import (
    ProviderConfig,
    ProviderMetadata,
    ProviderScopes,
)


class DummyMCPProvider(MCPAuthProvider):
    id = "dummy_mcp"
    mcp_server_uri = "https://dummy.example/mcp"
    scopes: ProviderScopes = ProviderScopes(default=[])
    metadata: ProviderMetadata = ProviderMetadata(
        id="dummy_mcp",
        name="Dummy MCP",
        description="Dummy MCP provider for tests",
        setup_steps=[],
        requires_config=False,
        enabled=True,
    )


@pytest.mark.anyio
async def test_mcp_provider_preserves_token_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")

    discovery = OAuthDiscoveryResult(
        authorization_endpoint="https://dummy.example/oauth/authorize",
        token_endpoint="https://dummy.example/oauth/token",
        token_methods=["client_secret_post"],
        registration_endpoint=None,
    )

    async def fake_discover(
        cls,
        logger_instance,
        *,
        discovered_auth_endpoint=None,
        discovered_token_endpoint=None,
    ) -> OAuthDiscoveryResult:
        return discovery

    monkeypatch.setattr(
        DummyMCPProvider,
        "_discover_oauth_endpoints_async",
        classmethod(fake_discover),
    )

    provider_config = ProviderConfig(
        client_id="dummy-client",
        client_secret=SecretStr("dummy-secret"),
        authorization_endpoint=discovery.authorization_endpoint,
        token_endpoint=discovery.token_endpoint,
        scopes=[],
    )

    provider = await DummyMCPProvider.instantiate(config=provider_config)

    assert provider._token_endpoint_auth_methods_supported == ["client_secret_post"]
    assert (
        getattr(provider.client, "token_endpoint_auth_method", None)
        == "client_secret_post"
    )
