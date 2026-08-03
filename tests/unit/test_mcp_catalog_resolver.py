"""Tests binding MCP connection requests to shipped catalog recipes."""

from __future__ import annotations

import uuid

import pytest

from tracecat.integrations.catalog.loader import (
    get_platform_mcp_catalog_entry_by_slug,
)
from tracecat.integrations.catalog.resolver import (
    CatalogConnectionError,
    resolve_available_catalog_entry,
    resolve_catalog_connection,
)
from tracecat.integrations.catalog.types import PlatformMCPCatalogEntry
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.schemas import (
    MCPConnectionOption,
    MCPHTTPCustomConnectionSpec,
)


def _entry(slug: str) -> PlatformMCPCatalogEntry:
    entry = get_platform_mcp_catalog_entry_by_slug(slug, include_private=True)
    assert entry is not None, f"catalog entry {slug} not found"
    return entry


# Shipped recipe shapes: fixed vendor URI, null URI with a server_uri
# credential, templated URI with documented path variants, and explicit
# multi-option rows. Each regressed under exact-URI matching before.
REAL_CATALOG_CONNECTIONS = [
    pytest.param(
        "slack-mcp",
        None,
        MCPAuthType.OAUTH2,
        "https://mcp.slack.com/mcp",
        id="slack-fixed-vendor-uri",
    ),
    pytest.param(
        "sumo-logic-mcp",
        None,
        MCPAuthType.CUSTOM,
        "https://api.us2.sumologic.com/mcp",
        id="sumo-logic-null-recipe-uri",
    ),
    pytest.param(
        "snowflake-mcp",
        None,
        MCPAuthType.CUSTOM,
        "https://acme-prod.snowflakecomputing.com/api/v2/databases/db/schemas/core/mcp-servers/main",
        id="snowflake-null-recipe-uri",
    ),
    pytest.param(
        "ansible-mcp",
        None,
        MCPAuthType.CUSTOM,
        "https://aap.example.com:8448",
        id="ansible-null-recipe-uri",
    ),
    pytest.param(
        "scanner-mcp",
        None,
        MCPAuthType.CUSTOM,
        "https://mcp.acme-prod.scanner.dev/v1/mcp",
        id="scanner-user-environment",
    ),
    pytest.param(
        "elastic-mcp",
        None,
        MCPAuthType.CUSTOM,
        "https://my-deployment.kb.us-central1.gcp.cloud.es.io/s/security/api/agent_builder/mcp",
        id="elastic-custom-space-path",
    ),
    pytest.param(
        "databricks-mcp",
        None,
        MCPAuthType.OAUTH2,
        "https://dbc-1234abcd.cloud.databricks.com/api/2.0/mcp/genie",
        id="databricks-documented-path-variant",
    ),
    pytest.param(
        "freshservice-mcp",
        "remote-oauth",
        MCPAuthType.OAUTH2,
        "https://acme.freshservice.com/mcp",
        id="freshservice-oauth-option",
    ),
    pytest.param(
        "freshservice-mcp",
        "api-key",
        MCPAuthType.CUSTOM,
        "https://acme.freshservice.com/mcp",
        id="freshservice-api-key-option",
    ),
]


@pytest.mark.parametrize(
    ("slug", "expected_option_id", "auth_type", "server_uri"), REAL_CATALOG_CONNECTIONS
)
def test_shipped_recipes_resolve(
    slug: str,
    expected_option_id: str | None,
    auth_type: MCPAuthType,
    server_uri: str,
) -> None:
    resolved = resolve_catalog_connection(
        _entry(slug),
        server_type="http",
        auth_type=auth_type,
        server_uri=server_uri,
    )
    assert resolved.spec.server_type == "http"
    assert resolved.spec.auth_type == auth_type
    if expected_option_id is not None:
        assert resolved.option_id == expected_option_id


def test_bare_spec_rows_get_a_stable_generated_option_id() -> None:
    resolved = resolve_catalog_connection(
        _entry("slack-mcp"),
        server_type="http",
        auth_type=MCPAuthType.OAUTH2,
        server_uri="https://mcp.slack.com/mcp",
    )
    assert resolved.option_id == "http-oauth2"


def test_fixed_vendor_uri_rejects_other_hosts() -> None:
    with pytest.raises(CatalogConnectionError, match="server URI must be"):
        resolve_catalog_connection(
            _entry("slack-mcp"),
            server_type="http",
            auth_type=MCPAuthType.OAUTH2,
            server_uri="https://mcp.evil.example/mcp",
        )


def test_fixed_vendor_uri_rejects_other_auth() -> None:
    with pytest.raises(CatalogConnectionError, match="authentication"):
        resolve_catalog_connection(
            _entry("slack-mcp"),
            server_type="http",
            auth_type=MCPAuthType.CUSTOM,
            server_uri="https://mcp.slack.com/mcp",
        )


def test_user_supplied_uri_rejects_embedded_credentials() -> None:
    with pytest.raises(CatalogConnectionError, match="embed credentials"):
        resolve_catalog_connection(
            _entry("sumo-logic-mcp"),
            server_type="http",
            auth_type=MCPAuthType.CUSTOM,
            server_uri="https://user:pass@api.us2.sumologic.com/mcp",
        )


def test_mixed_transport_row_disambiguates_by_server_type() -> None:
    """Panther ships both a remote HTTP option and a stdio package option."""
    entry = _entry("panther-mcp")
    http_resolved = resolve_catalog_connection(
        entry,
        server_type="http",
        auth_type=MCPAuthType.OAUTH2,
        server_uri="https://api.acme.runpanther.net/mcp",
    )
    assert http_resolved.spec.server_type == "http"
    stdio_resolved = resolve_catalog_connection(entry, server_type="stdio")
    assert stdio_resolved.spec.server_type == "stdio"


def test_http_request_requires_auth_and_uri() -> None:
    with pytest.raises(CatalogConnectionError, match="required for http"):
        resolve_catalog_connection(_entry("slack-mcp"), server_type="http")


def test_ambiguous_recipes_are_rejected_rather_than_guessed() -> None:
    """No shipped entry has same-shape recipes; a future one must not resolve."""

    def _option(option_id: str) -> MCPConnectionOption:
        return MCPConnectionOption(
            id=option_id,
            label=option_id,
            connection_spec=MCPHTTPCustomConnectionSpec(server_uri=""),
        )

    entry = PlatformMCPCatalogEntry(
        id=uuid.uuid4(),
        slug="ambiguous-mcp",
        name="Ambiguous",
        description="Two equivalent HTTP options",
        category="test",
        status="available",
        sort_key="0000:ambiguous",
        connection_options=[_option("first"), _option("second")],
    )
    with pytest.raises(CatalogConnectionError, match="multiple connection options"):
        resolve_catalog_connection(
            entry,
            server_type="http",
            auth_type=MCPAuthType.CUSTOM,
            server_uri="https://mcp.example.com/mcp",
        )


def test_row_without_options_is_not_connectable() -> None:
    entry = PlatformMCPCatalogEntry(
        id=uuid.uuid4(),
        slug="display-only-mcp",
        name="Display Only",
        description="No connect recipes",
        category="test",
        status="available",
        sort_key="0000:display",
    )
    with pytest.raises(CatalogConnectionError, match="not connectable"):
        resolve_catalog_connection(
            entry,
            server_type="http",
            auth_type=MCPAuthType.NONE,
            server_uri="https://mcp.example.com/mcp",
        )


def test_resolve_available_catalog_entry_guards() -> None:
    assert resolve_available_catalog_entry("slack-mcp").slug == "slack-mcp"
    with pytest.raises(CatalogConnectionError, match="not found"):
        resolve_available_catalog_entry("no-such-mcp")
    with pytest.raises(CatalogConnectionError, match="not available"):
        resolve_available_catalog_entry("splunk-mcp")
