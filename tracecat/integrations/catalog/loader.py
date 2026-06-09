"""Load the runtime platform MCP catalog from bundled JSON."""

from __future__ import annotations

import importlib.resources as resources
import logging
import re
import uuid
from copy import deepcopy
from functools import lru_cache
from typing import Any

import orjson
from pydantic import TypeAdapter, ValidationError

from tracecat.integrations.catalog.types import (
    PlatformMCPCatalogEntry,
    RawCatalogRow,
    RawConnectionSpec,
    RawHttpConnectionSpec,
)
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_validation import ALLOWED_MCP_COMMANDS
from tracecat.integrations.schemas import (
    MCPConfigField,
    MCPConnectionCredential,
    MCPConnectionOption,
    MCPConnectionSpec,
    MCPConnectionTarget,
    MCPPackageOption,
    PlatformMCPCatalogStatus,
)
from tracecat.integrations.types import MCPServerType

logger = logging.getLogger(__name__)

_CATALOG_PACKAGE = "tracecat.integrations.catalog"
_CATALOG_RESOURCE = "mcp_catalog.json"
_PRIVATE_CATALOG_PACKAGE = "tracecat_ee.mcp.catalog"
_PRIVATE_CATALOG_RESOURCE = "mcp_catalog_private.json"
_CATALOG_ID_NAMESPACE = uuid.UUID("9e8d0f75-6b6d-4b55-9d8f-8dc74e14f10d")
_CONNECTION_SPEC_ADAPTER: TypeAdapter[MCPConnectionSpec] = TypeAdapter(
    MCPConnectionSpec
)
_PLACEHOLDER_RE = re.compile(r"(\{[A-Za-z_][A-Za-z0-9_]*\}|<[A-Za-z_][A-Za-z0-9_-]*>)")


def catalog_id_for_slug(slug: str) -> uuid.UUID:
    """Return the stable runtime UUID for a platform MCP catalog slug."""
    return uuid.uuid5(_CATALOG_ID_NAMESPACE, slug)


def _credential_target(
    *,
    key: str,
    server_type: MCPServerType,
    server_uri: str | None,
    explicit_target: str | None = None,
) -> MCPConnectionTarget:
    match explicit_target:
        case "server_uri" | "oauth_client" | "http_header" | "stdio_env":
            return explicit_target
    if server_type == "stdio":
        return "stdio_env"
    if server_uri and (f"{{{key}}}" in server_uri or f"<{key}>" in server_uri):
        return "server_uri"
    normalized = key.lower()
    if normalized in {
        "url",
        "server_url",
        "server_uri",
        "endpoint",
        "host",
        "hostname",
        "workspace-hostname",
    }:
        return "server_uri"
    if normalized.endswith(("_url", "_uri", "_host", "_hostname")):
        return "server_uri"
    if normalized in {
        "client_id",
        "clientid",
        "client_secret",
        "clientsecret",
        "oauth_client_id",
        "oauthclientid",
        "oauth_client_secret",
        "oauthclientsecret",
    }:
        return "oauth_client"
    return "http_header"


def _normalize_connection_spec(
    raw_spec: RawConnectionSpec | None,
) -> MCPConnectionSpec | None:
    if raw_spec is None:
        return None
    auth_type = raw_spec.auth_type
    if auth_type is None:
        return None

    server_uri = (
        raw_spec.server_uri if isinstance(raw_spec, RawHttpConnectionSpec) else None
    )
    credentials = [
        MCPConnectionCredential(
            key=raw_credential.key,
            label=raw_credential.label or raw_credential.key,
            description=raw_credential.description or "",
            required=raw_credential.required,
            secret=raw_credential.secret,
            target=_credential_target(
                key=raw_credential.key,
                server_type=raw_spec.server_type,
                server_uri=server_uri,
                explicit_target=raw_credential.target,
            ),
        )
        for raw_credential in raw_spec.credentials or []
    ]
    config_fields = [
        MCPConfigField(
            key=credential.key,
            label=credential.label,
            description=credential.description,
            target=credential.target,
            required=credential.required,
            secret=credential.secret,
        )
        for credential in credentials
    ]
    has_server_uri_config = any(
        credential.target == "server_uri" for credential in credentials
    )
    requires_config = bool(
        any(credential.required for credential in credentials)
        or has_server_uri_config
        or (server_uri is not None and _PLACEHOLDER_RE.search(server_uri))
    )

    kind = f"{raw_spec.server_type}_{auth_type.lower()}"
    base: dict[str, Any] = {
        "kind": kind,
        "server_type": raw_spec.server_type,
        "auth_type": auth_type,
        "requires_config": requires_config,
        "config_fields": config_fields,
        "credentials": credentials,
    }
    if isinstance(raw_spec, RawHttpConnectionSpec):
        if not raw_spec.server_uri and not has_server_uri_config:
            return None
        base["server_uri"] = raw_spec.server_uri or ""
        if auth_type == MCPAuthType.OAUTH2:
            base["scopes"] = raw_spec.scopes or []
            base["oauth_authorization_endpoint"] = raw_spec.oauth_authorization_endpoint
            base["oauth_token_endpoint"] = raw_spec.oauth_token_endpoint
    else:
        packages = [
            MCPPackageOption(
                manager=raw_package.manager or raw_package.command,
                command=raw_package.command,
                args=raw_package.args,
                package=raw_package.package,
            )
            for raw_package in raw_spec.packages or []
        ]
        base["stdio_command"] = raw_spec.stdio_command
        base["stdio_args"] = raw_spec.stdio_args or []
        base["stdio_env"] = raw_spec.stdio_env or []
        base["packages"] = packages
        has_launchable_command = (
            any(package.command in ALLOWED_MCP_COMMANDS for package in packages)
            or raw_spec.stdio_command in ALLOWED_MCP_COMMANDS
        )
        if not has_launchable_command:
            base["requires_config"] = True
        if auth_type == MCPAuthType.OAUTH2:
            base["scopes"] = raw_spec.scopes or []

    try:
        return _CONNECTION_SPEC_ADAPTER.validate_python(base)
    except ValidationError:
        return None


def _normalize_connection_options(row: RawCatalogRow) -> list[MCPConnectionOption]:
    raw_options = row.connect_options
    options: list[MCPConnectionOption] = []

    if raw_options is not None:
        for index, option in enumerate(raw_options):
            spec = _normalize_connection_spec(option.spec)
            if spec is None:
                continue
            option_id = option.id
            if not option_id or not option_id.strip():
                option_id = f"{spec.server_type}-{spec.auth_type.lower()}-{index}"
            label = option.label
            if not label or not label.strip():
                label = spec.server_type.upper()
            description = option.description
            docs_url = option.docs if option.docs is not None else option.docs_url
            options.append(
                MCPConnectionOption(
                    id=option_id.strip(),
                    label=label.strip(),
                    description=description.strip()
                    if description and description.strip()
                    else None,
                    docs_url=docs_url if docs_url and docs_url.strip() else None,
                    connection_spec=spec,
                )
            )
        return options

    spec = _normalize_connection_spec(row.spec)
    if spec is None:
        return []

    return [
        MCPConnectionOption(
            id=f"{spec.server_type}-{spec.auth_type.lower()}",
            label=spec.server_type.upper(),
            description=None,
            docs_url=row.docs,
            connection_spec=spec,
        )
    ]


def _default_connection_spec(
    row: RawCatalogRow, connection_options: list[MCPConnectionOption]
) -> MCPConnectionSpec | None:
    if not connection_options:
        return _normalize_connection_spec(row.spec)

    raw_default = row.default_connection_option or row.default_option
    if raw_default and raw_default.strip():
        for option in connection_options:
            if option.id == raw_default.strip():
                return option.connection_spec

    return connection_options[0].connection_spec


def _load_catalog_document(
    package: str,
    resource: str,
    *,
    required: bool,
) -> dict[str, Any] | None:
    try:
        raw = resources.files(package).joinpath(resource).read_bytes()
        catalog_data = orjson.loads(raw)
    except (
        FileNotFoundError,
        IsADirectoryError,
        ModuleNotFoundError,
        orjson.JSONDecodeError,
        OSError,
    ):
        if required:
            return {}
        return None

    return catalog_data if isinstance(catalog_data, dict) else {}


def _catalog_data(*, include_private: bool) -> dict[str, Any]:
    public_data = _load_catalog_document(
        _CATALOG_PACKAGE, _CATALOG_RESOURCE, required=True
    )
    if public_data is None or not include_private:
        return public_data or {}

    private_data = _load_catalog_document(
        _PRIVATE_CATALOG_PACKAGE, _PRIVATE_CATALOG_RESOURCE, required=False
    )
    if not private_data:
        return public_data

    public_servers = public_data.get("servers")
    private_servers = private_data.get("servers")
    if not isinstance(public_servers, list) or not isinstance(private_servers, list):
        return public_data

    private_by_slug: dict[str, dict[str, Any]] = {}
    for row in private_servers:
        if not isinstance(row, dict):
            continue
        slug = row.get("slug")
        if isinstance(slug, str) and slug:
            private_by_slug[slug] = row

    merged_servers: list[Any] = []
    for row in public_servers:
        if not isinstance(row, dict):
            merged_servers.append(row)
            continue
        slug = row.get("slug")
        overlay = private_by_slug.get(slug) if isinstance(slug, str) else None
        merged_servers.append({**row, **overlay} if overlay else row)

    return {**public_data, "servers": merged_servers}


def _load_platform_mcp_catalog_entries(
    *, include_private: bool = False
) -> list[PlatformMCPCatalogEntry]:
    """Load and validate runtime MCP catalog rows."""
    catalog_data = _catalog_data(include_private=include_private)
    if not catalog_data:
        return []

    servers = catalog_data.get("servers")
    if not isinstance(servers, list):
        return []

    entries: list[PlatformMCPCatalogEntry] = []
    for index, raw in enumerate(servers):
        try:
            row = RawCatalogRow.model_validate(raw)
        except ValidationError:
            continue

        connection_options: list[MCPConnectionOption] = []
        connection_spec: MCPConnectionSpec | None = None
        provider_id: str | None = None
        docs_url: str | None = None
        if include_private:
            connection_options = _normalize_connection_options(row)
            connection_spec = _default_connection_spec(row, connection_options)
            if row.has_connection_metadata and connection_spec is None:
                logger.warning(
                    "Skipping invalid MCP catalog connection metadata",
                    extra={"slug": row.slug},
                )
            provider_id = row.provider_id
            docs_url = row.docs

        if include_private and connection_spec is not None:
            status: PlatformMCPCatalogStatus = "available"
        else:
            status = row.status or "coming_soon"

        entry = PlatformMCPCatalogEntry(
            id=catalog_id_for_slug(row.slug),
            slug=row.slug,
            name=row.name,
            description=row.description,
            category=row.category,
            status=status,
            icon_url=row.icon,
            docs_url=docs_url,
            provider_id=provider_id,
            connection_spec=connection_spec,
            connection_options=connection_options or None,
            sort_key=f"{index:04d}:{row.name.lower()}",
        )
        entries.append(entry)
    return entries


@lru_cache(maxsize=2)
def _cached_platform_mcp_catalog_entries(
    include_private: bool,
) -> tuple[PlatformMCPCatalogEntry, ...]:
    return tuple(_load_platform_mcp_catalog_entries(include_private=include_private))


def get_platform_mcp_catalog_entries(
    *, include_private: bool = False
) -> list[PlatformMCPCatalogEntry]:
    """Return cached runtime MCP catalog rows.

    The bundled catalog is static for the process lifetime. Return fresh copies
    so callers can sort or transform results without mutating cached entries.
    """
    return [
        deepcopy(entry)
        for entry in _cached_platform_mcp_catalog_entries(include_private)
    ]


def get_platform_mcp_catalog_entry_by_slug(
    slug: str, *, include_private: bool = False
) -> PlatformMCPCatalogEntry | None:
    """Return one runtime catalog entry by stable slug."""
    for entry in get_platform_mcp_catalog_entries(include_private=include_private):
        if entry.slug == slug:
            return entry
    return None


def get_platform_mcp_catalog_entry_by_provider_id(
    provider_id: str, *, include_private: bool = False
) -> PlatformMCPCatalogEntry | None:
    """Return one runtime catalog entry by provider id."""
    for entry in get_platform_mcp_catalog_entries(include_private=include_private):
        if entry.provider_id == provider_id:
            return entry
    return None
