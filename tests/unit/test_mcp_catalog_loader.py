"""Tests for platform MCP catalog loading resilience."""

from __future__ import annotations

import uuid

import orjson
import pytest

from tracecat.db.models import MCPIntegration
from tracecat.integrations.catalog import loader
from tracecat.integrations.catalog.service import PlatformMCPCatalogService
from tracecat.integrations.catalog.types import RawCatalogRow
from tracecat.integrations.enums import MCPAuthType


class _CatalogResource:
    def __init__(self, payload: bytes, resource_name: str) -> None:
        self._payload = payload
        self._resource_name = resource_name

    def joinpath(self, name: str) -> _CatalogResource:
        assert name == self._resource_name
        return self

    def read_bytes(self) -> bytes:
        return self._payload


def _clear_catalog_cache() -> None:
    loader._cached_platform_mcp_catalog_entries.cache_clear()


def _stub_catalog_resource(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    _clear_catalog_cache()

    def _files(package: str) -> _CatalogResource:
        if package == loader._CATALOG_PACKAGE:
            return _CatalogResource(payload, "mcp_catalog.json")
        raise ModuleNotFoundError(package)

    monkeypatch.setattr(
        loader.resources,
        "files",
        _files,
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"servers": {"slug": "elastic-mcp"}}',
        b'{"servers": ["not-a-server", {"slug": "elastic-mcp"}]}',
        b'{"servers": [{"slug": "", "name": "Elastic", "description": "x", "category": "SIEM"}]}',
        # Credentials must carry an explicit target; inference was removed.
        b'{"servers": [{"slug": "x-mcp", "name": "X", "description": "x", "category": "SIEM",'
        b' "connection_spec": {"server_type": "http", "auth_type": "CUSTOM",'
        b' "server_uri": "https://x.example.com/mcp", "credentials": [{"key": "Authorization"}]}}]}',
    ],
)
def test_get_platform_mcp_catalog_entries_ignores_malformed_shapes(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    _stub_catalog_resource(monkeypatch, payload)

    assert loader.get_platform_mcp_catalog_entries() == []


def test_get_platform_mcp_catalog_entries_normalizes_specs_and_drops_malformed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_catalog_resource(
        monkeypatch,
        orjson.dumps(
            {
                "servers": [
                    {
                        "slug": "elastic-mcp",
                        "name": "Elastic",
                        "description": "Search telemetry",
                        "category": "SIEM / Datalake",
                        "icon": "https://example.com/elastic.png",
                        "docs": "https://example.com/docs",
                        "status": "coming_soon",
                        "connection_spec": {
                            "server_type": "http",
                            "auth_type": "CUSTOM",
                            "server_uri": "https://{KIBANA_URL}/api/mcp",
                            "credentials": [
                                {
                                    "key": "KIBANA_URL",
                                    "secret": False,
                                    "target": "server_uri",
                                },
                                {"key": "Authorization", "target": "http_header"},
                            ],
                        },
                    },
                    {
                        "slug": "future-mcp",
                        "name": "Future",
                        "description": "Future server",
                        "category": "Cloud",
                        "status": "coming_soon",
                    },
                    {
                        "slug": "generic-oauth-mcp",
                        "name": "Generic OAuth",
                        "description": "OAuth server without a provider",
                        "category": "Cloud",
                        "connection_spec": {
                            "server_type": "http",
                            "auth_type": "OAUTH2",
                            "server_uri": "https://mcp.example.com/mcp",
                        },
                    },
                    {
                        "slug": "provider-oauth-mcp",
                        "name": "Provider OAuth",
                        "description": "OAuth server with a provider",
                        "category": "Cloud",
                        "provider_id": "runreveal_mcp",
                        "connection_spec": {
                            "server_type": "http",
                            "auth_type": "OAUTH2",
                            "server_uri": "https://api.runreveal.com/mcp",
                        },
                    },
                    {
                        "slug": "bad-mcp",
                        "name": "Bad",
                        "description": "Bad server",
                        "category": "Cloud",
                        "status": "available",
                        "connection_spec": {
                            "server_type": "grpc",
                            "auth_type": "CUSTOM",
                        },
                    },
                    {
                        "slug": "user-url-mcp",
                        "name": "User URL",
                        "description": "Server URI supplied by user",
                        "category": "Cloud",
                        "connection_spec": {
                            "server_type": "http",
                            "auth_type": "CUSTOM",
                            "server_uri": None,
                            "credentials": [
                                {
                                    "key": "SNOWFLAKE_MCP_URL",
                                    "label": "Snowflake MCP URL",
                                    "secret": False,
                                    "target": "server_uri",
                                },
                                {"key": "Authorization", "target": "http_header"},
                            ],
                        },
                    },
                    {
                        "slug": "local-only-mcp",
                        "name": "Local Only",
                        "description": "Needs user command",
                        "category": "IaC",
                        "connection_spec": {
                            "server_type": "stdio",
                            "auth_type": "CUSTOM",
                            "stdio_command": None,
                            "stdio_args": ["run", "local-only"],
                            "credentials": [],
                            "packages": [],
                        },
                    },
                ]
            }
        ),
    )

    entries = loader.get_platform_mcp_catalog_entries(include_private=True)

    # bad-mcp has an unknown server_type, so the whole row fails validation.
    assert [entry.slug for entry in entries] == [
        "elastic-mcp",
        "future-mcp",
        "generic-oauth-mcp",
        "provider-oauth-mcp",
        "user-url-mcp",
        "local-only-mcp",
    ]
    assert entries[0].status == "available"
    assert entries[0].docs_url == "https://example.com/docs"
    elastic_spec = entries[0].connection_spec
    assert elastic_spec is not None
    assert elastic_spec.model_dump(mode="json") == {
        "kind": "http_custom",
        "server_type": "http",
        "auth_type": "CUSTOM",
        "server_uri": "https://{KIBANA_URL}/api/mcp",
        "requires_config": True,
        "config_fields": [
            {
                "key": "KIBANA_URL",
                "label": "KIBANA_URL",
                "description": "",
                "target": "server_uri",
                "required": True,
                "secret": False,
            },
            {
                "key": "Authorization",
                "label": "Authorization",
                "description": "",
                "target": "http_header",
                "required": True,
                "secret": True,
            },
        ],
        "credentials": [
            {
                "key": "KIBANA_URL",
                "label": "KIBANA_URL",
                "description": "",
                "required": True,
                "secret": False,
                "target": "server_uri",
            },
            {
                "key": "Authorization",
                "label": "Authorization",
                "description": "",
                "required": True,
                "secret": True,
                "target": "http_header",
            },
        ],
    }
    assert entries[1].status == "coming_soon"
    assert entries[1].connection_spec is None
    assert entries[2].status == "available"
    generic_oauth_spec = entries[2].connection_spec
    assert generic_oauth_spec is not None
    assert generic_oauth_spec.auth_type == MCPAuthType.OAUTH2
    assert entries[3].status == "available"
    assert entries[3].provider_id == "runreveal_mcp"
    provider_oauth_spec = entries[3].connection_spec
    assert provider_oauth_spec is not None
    assert provider_oauth_spec.auth_type == MCPAuthType.OAUTH2
    user_url_spec = entries[4].connection_spec
    assert user_url_spec is not None
    assert user_url_spec.server_type == "http"
    assert user_url_spec.server_uri == ""
    assert user_url_spec.requires_config is True
    assert user_url_spec.config_fields[0].target == "server_uri"
    local_only_spec = entries[5].connection_spec
    assert local_only_spec is not None
    assert local_only_spec.server_type == "stdio"
    assert local_only_spec.requires_config is True


def test_get_platform_mcp_catalog_entries_normalizes_connection_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_catalog_resource(
        monkeypatch,
        orjson.dumps(
            {
                "servers": [
                    {
                        "slug": "panther-mcp",
                        "name": "Panther",
                        "description": "Investigate alerts",
                        "category": "SIEM / Datalake",
                        "default_connection_option": "remote-http",
                        "connection_options": [
                            {
                                "id": "remote-http",
                                "label": "Remote OAuth",
                                "connection_spec": {
                                    "server_type": "http",
                                    "auth_type": "OAUTH2",
                                    "server_uri": "https://api.<host>/mcp",
                                    "credentials": [
                                        {
                                            "key": "host",
                                            "target": "server_uri",
                                            "secret": False,
                                        }
                                    ],
                                },
                            },
                            {
                                "id": "local-stdio",
                                "label": "Local stdio",
                                "connection_spec": {
                                    "server_type": "stdio",
                                    "auth_type": "CUSTOM",
                                    "stdio_command": "uvx",
                                    "stdio_args": ["mcp-panther"],
                                    "stdio_env": ["PANTHER_API_TOKEN"],
                                    "credentials": [
                                        {
                                            "key": "PANTHER_API_TOKEN",
                                            "target": "stdio_env",
                                        }
                                    ],
                                },
                            },
                        ],
                    }
                ]
            }
        ),
    )

    entries = loader.get_platform_mcp_catalog_entries(include_private=True)

    assert len(entries) == 1
    assert entries[0].status == "available"
    connection_spec = entries[0].connection_spec
    assert connection_spec is not None
    assert connection_spec.kind == "http_oauth2"
    options = entries[0].connection_options
    assert options is not None
    assert [option.id for option in options] == [
        "remote-http",
        "local-stdio",
    ]
    assert [option.connection_spec.server_type for option in options] == [
        "http",
        "stdio",
    ]


def test_get_platform_mcp_catalog_entries_merges_private_mcp_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_catalog_cache()
    public = orjson.dumps(
        {
            "servers": [
                {
                    "slug": "scanner-mcp",
                    "name": "Scanner",
                    "description": "Search security data",
                    "category": "SIEM / Datalake",
                    "icon": "https://example.com/scanner.png",
                    "status": "available",
                },
                {
                    "slug": "sumo-logic-mcp",
                    "name": "Sumo Logic",
                    "description": "Search telemetry",
                    "category": "SIEM / Datalake",
                    "status": "coming_soon",
                },
            ]
        }
    )
    private = orjson.dumps(
        {
            "servers": [
                {
                    "slug": "scanner-mcp",
                    "docs": "https://docs.scanner.dev/mcp",
                    "connection_spec": {
                        "server_type": "http",
                        "auth_type": "CUSTOM",
                        "server_uri": "https://mcp.example.scanner.dev/v1/mcp",
                        "credentials": [
                            {
                                "key": "Authorization",
                                "label": "Scanner API key",
                                "target": "http_header",
                            }
                        ],
                    },
                },
                {
                    "slug": "sumo-logic-mcp",
                    "docs": "https://www.sumologic.com/demo/mcp-server",
                    "connection_spec": {
                        "server_type": "http",
                        "auth_type": None,
                        "server_uri": None,
                    },
                },
            ]
        }
    )

    def _files(package: str) -> _CatalogResource:
        if package == loader._CATALOG_PACKAGE:
            return _CatalogResource(public, "mcp_catalog.json")
        if package == loader._PRIVATE_CATALOG_PACKAGE:
            return _CatalogResource(private, "mcp_catalog_private.json")
        raise ModuleNotFoundError(package)

    monkeypatch.setattr(loader.resources, "files", _files)

    public_entries = loader.get_platform_mcp_catalog_entries()

    assert len(public_entries) == 2
    assert public_entries[0].slug == "scanner-mcp"
    assert public_entries[0].docs_url is None
    assert public_entries[0].connection_spec is None

    private_entries = loader.get_platform_mcp_catalog_entries(include_private=True)

    assert len(private_entries) == 2
    assert private_entries[0].slug == "scanner-mcp"
    assert private_entries[0].docs_url == "https://docs.scanner.dev/mcp"
    assert private_entries[0].connection_spec is not None
    assert private_entries[1].slug == "sumo-logic-mcp"
    assert private_entries[1].status == "coming_soon"
    assert private_entries[1].docs_url == "https://www.sumologic.com/demo/mcp-server"
    assert private_entries[1].connection_spec is None


def test_get_platform_mcp_catalog_entries_caches_static_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_catalog_cache()
    public = orjson.dumps(
        {
            "servers": [
                {
                    "slug": "scanner-mcp",
                    "name": "Scanner",
                    "description": "Search security data",
                    "category": "SIEM / Datalake",
                    "status": "available",
                }
            ]
        }
    )
    private = orjson.dumps(
        {
            "servers": [
                {
                    "slug": "scanner-mcp",
                    "docs": "https://docs.scanner.dev/mcp",
                    "connection_spec": {
                        "server_type": "http",
                        "auth_type": "CUSTOM",
                        "server_uri": "https://mcp.example.scanner.dev/v1/mcp",
                        "credentials": [
                            {
                                "key": "Authorization",
                                "label": "Scanner API key",
                                "target": "http_header",
                            }
                        ],
                    },
                }
            ]
        }
    )
    calls: dict[str, int] = {}

    def _files(package: str) -> _CatalogResource:
        calls[package] = calls.get(package, 0) + 1
        if package == loader._CATALOG_PACKAGE:
            return _CatalogResource(public, "mcp_catalog.json")
        if package == loader._PRIVATE_CATALOG_PACKAGE:
            return _CatalogResource(private, "mcp_catalog_private.json")
        raise ModuleNotFoundError(package)

    monkeypatch.setattr(loader.resources, "files", _files)

    first_entries = loader.get_platform_mcp_catalog_entries(include_private=True)
    first_spec = first_entries[0].connection_spec
    assert first_spec is not None
    assert first_spec.server_type == "http"
    first_entries[0].name = "Mutated"
    first_spec.server_uri = "https://mutated.example/mcp"

    second_entries = loader.get_platform_mcp_catalog_entries(include_private=True)

    assert calls == {
        loader._CATALOG_PACKAGE: 1,
        loader._PRIVATE_CATALOG_PACKAGE: 1,
    }
    assert second_entries[0].name == "Scanner"
    second_spec = second_entries[0].connection_spec
    assert second_spec is not None
    assert second_spec.server_type == "http"
    assert second_spec.server_uri == "https://mcp.example.scanner.dev/v1/mcp"


def test_bundled_catalog_rows_all_validate() -> None:
    """Every bundled catalog row must validate strictly.

    A failure here means the bundled JSON itself is broken and must be fixed
    in the catalog, not papered over in the loader.
    """
    _clear_catalog_cache()
    for include_private in (False, True):
        catalog_data = loader._catalog_data(include_private=include_private)
        servers = catalog_data.get("servers")
        assert isinstance(servers, list)
        assert servers
        for raw in servers:
            RawCatalogRow.model_validate(raw)
        entries = loader.get_platform_mcp_catalog_entries(
            include_private=include_private
        )
        assert len(entries) == len(servers)


def test_private_catalog_overlay_does_not_drop_public_rows() -> None:
    _clear_catalog_cache()
    public_entries = loader.get_platform_mcp_catalog_entries()
    private_entries = loader.get_platform_mcp_catalog_entries(include_private=True)
    private_by_slug = {entry.slug: entry for entry in private_entries}

    assert len(private_entries) >= len(public_entries)
    for entry in public_entries:
        assert entry.slug in private_by_slug

    terraform_spec = private_by_slug["terraform-mcp"].connection_spec
    if terraform_spec is not None:
        assert terraform_spec.server_type == "stdio"
        assert terraform_spec.requires_config is True

    # jamf-mcp defaults to Jamf's hosted no-auth HTTP server; the local
    # stdio (device management) option survives alongside it.
    jamf = private_by_slug["jamf-mcp"]
    if jamf.connection_spec is not None:
        assert jamf.connection_spec.server_type == "http"
        assert jamf.connection_spec.requires_config is False
        assert jamf.connection_options is not None
        stdio_option = next(
            option for option in jamf.connection_options if option.id == "local-stdio"
        )
        assert stdio_option.connection_spec.server_type == "stdio"
        assert stdio_option.connection_spec.requires_config is True


def test_catalog_state_marks_non_oauth_mcp_rows_connected() -> None:
    state = PlatformMCPCatalogService._catalog_state(
        mcp_integration=MCPIntegration(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            name="No Auth MCP",
            slug="no-auth-mcp",
            auth_type=MCPAuthType.NONE,
        ),
        encrypted_access_token=None,
    )

    assert state == "connected"
