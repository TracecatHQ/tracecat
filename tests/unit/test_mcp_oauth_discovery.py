import pytest

from tracecat.integrations.service import IntegrationService
from tracecat.integrations.types import OAuthServerMetadata


@pytest.mark.anyio
async def test_discovery_rejects_resource_without_authorization_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP resource metadata must select an authorization server."""
    integration_service = object.__new__(IntegrationService)
    root_metadata_url = "https://mcp.example.com/.well-known/oauth-protected-resource"
    docs = {
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": {
            "resource": "https://mcp.example.com/mcp",
        },
        # A different resource's metadata must not supply the missing issuer.
        root_metadata_url: {
            "resource": "https://mcp.example.com",
            "authorization_servers": ["https://login.example-idp.com"],
        },
    }
    fetched_urls: list[str] = []

    async def fake_fetch(url: str) -> OAuthServerMetadata | None:
        fetched_urls.append(url)
        return OAuthServerMetadata.from_json(docs[url])

    monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

    with pytest.raises(ValueError, match="missing authorization_servers"):
        await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://mcp.example.com/mcp",
        )

    assert root_metadata_url not in fetched_urls
