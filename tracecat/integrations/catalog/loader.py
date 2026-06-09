"""Load the runtime platform MCP catalog from bundled JSON.

Pipeline: bundled JSON -> ``Raw*`` models (trust boundary) -> runtime entries.

The public catalog (``mcp_catalog.json``) carries display-only rows: slug,
name, description, category, icon. The EE-private overlay
(``mcp_catalog_private.json``) merges connect recipes onto those rows by slug:
connection specs, credentials, OAuth endpoints, docs, and provider ids.
Without the overlay every row is display-only ("coming soon"); with it, a row
becomes "available" once it yields a valid connection spec.

Both JSON files are repo-owned and canonical: specs use the
``connection_spec``/``connection_options`` keys and every credential states an
explicit ``target`` (see ``MCPConnectionTarget``) for where its value is
routed at connect time. A malformed row is skipped rather than fatal — one bad
row must not take down the whole catalog — and a bundled-catalog test asserts
the shipped files never exercise that fallback.
"""

from __future__ import annotations

import importlib.resources as resources
import logging
import re
import uuid
from copy import deepcopy
from functools import lru_cache
from typing import Any, assert_never

import orjson
from pydantic import ValidationError

from tracecat.integrations.catalog.types import (
    PlatformMCPCatalogEntry,
    RawCatalogRow,
    RawConnectionOption,
    RawConnectionSpec,
    RawHttpConnectionSpec,
    RawStdioConnectionSpec,
)
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_validation import ALLOWED_MCP_COMMANDS
from tracecat.integrations.schemas import (
    MCPConnectionCredential,
    MCPConnectionOption,
    MCPConnectionSpec,
    MCPHTTPCustomConnectionSpec,
    MCPHTTPNoneConnectionSpec,
    MCPHTTPOAuth2ConnectionSpec,
    MCPPackageOption,
    MCPStdioCustomConnectionSpec,
    MCPStdioNoneConnectionSpec,
    PlatformMCPCatalogStatus,
)

logger = logging.getLogger(__name__)

_CATALOG_PACKAGE = "tracecat.integrations.catalog"
_CATALOG_RESOURCE = "mcp_catalog.json"
_PRIVATE_CATALOG_PACKAGE = "tracecat_ee.mcp.catalog"
_PRIVATE_CATALOG_RESOURCE = "mcp_catalog_private.json"
_CATALOG_ID_NAMESPACE = uuid.UUID("9e8d0f75-6b6d-4b55-9d8f-8dc74e14f10d")
# {placeholder} or <placeholder> segments in a server URI that the user must
# fill in before the connection is usable.
_PLACEHOLDER_RE = re.compile(r"(\{[A-Za-z_][A-Za-z0-9_]*\}|<[A-Za-z_][A-Za-z0-9_-]*>)")


def catalog_id_for_slug(slug: str) -> uuid.UUID:
    """Return the stable runtime UUID for a platform MCP catalog slug."""
    return uuid.uuid5(_CATALOG_ID_NAMESPACE, slug)


def _normalize_credentials(
    raw_spec: RawConnectionSpec,
) -> list[MCPConnectionCredential]:
    return [
        MCPConnectionCredential(
            key=raw_credential.key,
            label=raw_credential.label or raw_credential.key,
            description=raw_credential.description or "",
            required=raw_credential.required,
            secret=raw_credential.secret,
            target=raw_credential.target,
        )
        for raw_credential in raw_spec.credentials or []
    ]


def _http_spec(raw_spec: RawHttpConnectionSpec) -> MCPConnectionSpec | None:
    auth_type = raw_spec.auth_type
    if auth_type is None:
        return None
    credentials = _normalize_credentials(raw_spec)
    has_server_uri_credential = any(
        credential.target == "server_uri" for credential in credentials
    )
    # No URI and no credential to derive one from: nothing to connect to.
    if not raw_spec.server_uri and not has_server_uri_credential:
        return None
    requires_config = bool(
        any(credential.required for credential in credentials)
        or has_server_uri_credential
        or (raw_spec.server_uri and _PLACEHOLDER_RE.search(raw_spec.server_uri))
    )
    server_uri = raw_spec.server_uri or ""
    match auth_type:
        case MCPAuthType.OAUTH2:
            return MCPHTTPOAuth2ConnectionSpec(
                server_uri=server_uri,
                requires_config=requires_config,
                credentials=credentials,
                scopes=raw_spec.scopes or [],
                oauth_authorization_endpoint=raw_spec.oauth_authorization_endpoint,
                oauth_token_endpoint=raw_spec.oauth_token_endpoint,
            )
        case MCPAuthType.CUSTOM:
            return MCPHTTPCustomConnectionSpec(
                server_uri=server_uri,
                requires_config=requires_config,
                credentials=credentials,
            )
        case MCPAuthType.NONE:
            return MCPHTTPNoneConnectionSpec(
                server_uri=server_uri,
                requires_config=requires_config,
                credentials=credentials,
            )
        case _:
            assert_never(auth_type)


def _stdio_spec(raw_spec: RawStdioConnectionSpec) -> MCPConnectionSpec | None:
    auth_type = raw_spec.auth_type
    if auth_type is None:
        return None
    credentials = _normalize_credentials(raw_spec)
    packages = [
        MCPPackageOption(
            manager=raw_package.manager or raw_package.command,
            command=raw_package.command,
            args=raw_package.args,
            package=raw_package.package,
        )
        for raw_package in raw_spec.packages or []
    ]
    # Without an allowlisted launch command the user must supply one.
    has_launchable_command = (
        any(package.command in ALLOWED_MCP_COMMANDS for package in packages)
        or raw_spec.stdio_command in ALLOWED_MCP_COMMANDS
    )
    requires_config = (
        any(credential.required for credential in credentials)
        or not has_launchable_command
    )
    stdio_args = raw_spec.stdio_args or []
    stdio_env = raw_spec.stdio_env or []
    match auth_type:
        case MCPAuthType.CUSTOM:
            return MCPStdioCustomConnectionSpec(
                requires_config=requires_config,
                credentials=credentials,
                stdio_command=raw_spec.stdio_command,
                stdio_args=stdio_args,
                stdio_env=stdio_env,
                packages=packages,
            )
        case MCPAuthType.NONE:
            return MCPStdioNoneConnectionSpec(
                requires_config=requires_config,
                credentials=credentials,
                stdio_command=raw_spec.stdio_command,
                stdio_args=stdio_args,
                stdio_env=stdio_env,
                packages=packages,
            )
        case _:
            assert_never(auth_type)


def _normalize_connection_options(row: RawCatalogRow) -> list[MCPConnectionOption]:
    """Normalize a row's connect options.

    A row either lists explicit ``connection_options`` or carries one bare
    ``connection_spec``; the bare spec is treated as a single unnamed option so
    both shapes flow through the same loop. Options without a usable connect
    recipe (no ``auth_type``, or an HTTP spec with no way to obtain a server
    URI) are dropped; a row left with no options surfaces as "coming soon".
    """
    raw_options = row.connection_options
    if not raw_options:
        raw_options = [RawConnectionOption(connection_spec=row.connection_spec)]

    options: list[MCPConnectionOption] = []
    for index, raw_option in enumerate(raw_options):
        raw_spec = raw_option.connection_spec
        if raw_spec is None:
            continue
        spec = (
            _http_spec(raw_spec)
            if isinstance(raw_spec, RawHttpConnectionSpec)
            else _stdio_spec(raw_spec)
        )
        if spec is None:
            continue
        default_id = f"{spec.server_type}-{spec.auth_type.lower()}"
        options.append(
            MCPConnectionOption(
                id=(raw_option.id or "").strip()
                or (default_id if index == 0 else f"{default_id}-{index}"),
                label=(raw_option.label or "").strip() or spec.server_type.upper(),
                description=(raw_option.description or "").strip() or None,
                docs_url=(raw_option.docs or "").strip() or row.docs,
                connection_spec=spec,
            )
        )
    return options


def _load_catalog_document(package: str, resource: str) -> dict[str, Any] | None:
    """Read one bundled catalog JSON document; ``None`` if absent or invalid."""
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
        return None
    return catalog_data if isinstance(catalog_data, dict) else None


def _catalog_data(*, include_private: bool) -> dict[str, Any]:
    """Public catalog document, with private rows overlaid when requested.

    The overlay runs on raw dicts (pre-validation) so private rows can merge
    field-by-field onto their public counterparts: a private row contributes
    connect recipe keys (``connection_spec``, ``docs``, ...) on top of the
    public row's display keys, matched by slug.
    """
    public_data = _load_catalog_document(_CATALOG_PACKAGE, _CATALOG_RESOURCE) or {}
    if not include_private:
        return public_data

    private_data = _load_catalog_document(
        _PRIVATE_CATALOG_PACKAGE, _PRIVATE_CATALOG_RESOURCE
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
    servers = catalog_data.get("servers")
    if not isinstance(servers, list):
        return []

    entries: list[PlatformMCPCatalogEntry] = []
    for index, raw in enumerate(servers):
        try:
            row = RawCatalogRow.model_validate(raw)
        except ValidationError:
            continue

        # Connect recipes (and the docs/provider metadata that travel with
        # them) only exist on the private overlay; public-only loads keep
        # every row display-only.
        connection_options: list[MCPConnectionOption] = []
        connection_spec: MCPConnectionSpec | None = None
        provider_id: str | None = None
        docs_url: str | None = None
        if include_private:
            connection_options = _normalize_connection_options(row)
            if connection_options:
                # One-click connect uses the named default option, else the first.
                wanted = (row.default_connection_option or "").strip()
                connection_spec = next(
                    (o.connection_spec for o in connection_options if o.id == wanted),
                    connection_options[0].connection_spec,
                )
            if row.has_connection_metadata and connection_spec is None:
                logger.warning(
                    "Skipping invalid MCP catalog connection metadata",
                    extra={"slug": row.slug},
                )
            provider_id = row.provider_id
            docs_url = row.docs

        # A row is connectable only once it yields a valid spec.
        if connection_spec is not None:
            status: PlatformMCPCatalogStatus = "available"
        else:
            status = row.status or "coming_soon"

        entries.append(
            PlatformMCPCatalogEntry(
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
        )
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
