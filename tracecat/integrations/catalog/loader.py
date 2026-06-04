"""Load the runtime platform MCP catalog from bundled JSON."""

from __future__ import annotations

import importlib.resources as resources
import logging
import re
import uuid
from typing import Any, NotRequired, TypedDict

import orjson
from pydantic import TypeAdapter, ValidationError

from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.mcp_validation import ALLOWED_MCP_COMMANDS
from tracecat.integrations.schemas import MCPConnectionSpec, PlatformMCPCatalogStatus

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
_CONFIG_TARGETS = {"server_uri", "oauth_client", "http_header", "stdio_env"}


class PlatformMCPCatalogEntry(TypedDict):
    """Runtime platform MCP catalog row.

    Display metadata is safe to ship in OSS. Connect recipes are populated only
    from the private catalog overlay.
    """

    id: uuid.UUID
    slug: str
    name: str
    description: str
    category: str
    status: PlatformMCPCatalogStatus
    icon_url: NotRequired[str | None]
    docs_url: NotRequired[str | None]
    provider_id: NotRequired[str | None]
    connection_spec: NotRequired[dict[str, Any] | None]
    connection_options: NotRequired[list[dict[str, Any]] | None]
    sort_key: str


def catalog_id_for_slug(slug: str) -> uuid.UUID:
    """Return the stable runtime UUID for a platform MCP catalog slug."""
    return uuid.uuid5(_CATALOG_ID_NAMESPACE, slug)


def _credential_target(
    *,
    key: str,
    server_type: str,
    server_uri: str | None,
    explicit_target: str | None = None,
) -> str:
    if explicit_target in _CONFIG_TARGETS:
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


def _normalize_credentials(
    *,
    raw_credentials: list[Any],
    server_type: str,
    server_uri: str | None,
) -> list[dict[str, Any]]:
    credentials: list[dict[str, Any]] = []
    for raw in raw_credentials:
        if not isinstance(raw, dict):
            continue
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            continue
        credentials.append(
            {
                "key": key,
                "label": raw.get("label") or key,
                "description": raw.get("description") or "",
                "required": bool(raw.get("required", True)),
                "secret": bool(raw.get("secret", True)),
                "target": _credential_target(
                    key=key,
                    server_type=server_type,
                    server_uri=server_uri,
                    explicit_target=raw.get("target")
                    if isinstance(raw.get("target"), str)
                    else None,
                ),
            }
        )
    return credentials


def _normalize_packages(raw_packages: list[Any]) -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for raw in raw_packages:
        if not isinstance(raw, dict):
            continue
        command = raw.get("command")
        if not isinstance(command, str) or not command:
            continue
        args = raw.get("args")
        packages.append(
            {
                "manager": raw.get("manager") or command,
                "command": command,
                "args": args if isinstance(args, list) else [],
                "package": raw.get("package"),
            }
        )
    return packages


def _config_fields_from_credentials(
    credentials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "key": credential["key"],
            "label": credential["label"],
            "description": credential["description"],
            "target": credential["target"],
            "required": credential["required"],
            "secret": credential["secret"],
        }
        for credential in credentials
    ]


def _normalize_connection_spec(row: dict[str, Any]) -> dict[str, Any] | None:
    raw_spec = row.get("connection_spec", row.get("metadata"))
    if not isinstance(raw_spec, dict):
        return None

    server_type = raw_spec.get("server_type")
    auth_type = raw_spec.get("auth_type")
    if not isinstance(server_type, str) or server_type not in {"http", "stdio"}:
        return None
    if not isinstance(auth_type, str) or auth_type not in {
        MCPAuthType.OAUTH2,
        MCPAuthType.CUSTOM,
        MCPAuthType.NONE,
    }:
        return None

    raw_server_uri = raw_spec.get("server_uri")
    server_uri = raw_server_uri if isinstance(raw_server_uri, str) else None
    credentials = _normalize_credentials(
        raw_credentials=raw_spec.get("credentials") or [],
        server_type=server_type,
        server_uri=server_uri,
    )
    config_fields = _config_fields_from_credentials(credentials)
    has_server_uri_config = any(
        credential["target"] == "server_uri" for credential in credentials
    )
    requires_config = bool(
        any(credential["required"] for credential in credentials)
        or has_server_uri_config
        or (server_uri is not None and _PLACEHOLDER_RE.search(server_uri))
    )

    kind = f"{server_type}_{auth_type.lower()}"
    base: dict[str, Any] = {
        "kind": kind,
        "server_type": server_type,
        "auth_type": auth_type,
        "requires_config": requires_config,
        "config_fields": config_fields,
        "credentials": credentials,
    }
    if server_type == "http":
        if not server_uri and not has_server_uri_config:
            return None
        base["server_uri"] = server_uri or ""
        if auth_type == MCPAuthType.OAUTH2:
            base["scopes"] = raw_spec.get("scopes") or []
            base["oauth_authorization_endpoint"] = raw_spec.get(
                "oauth_authorization_endpoint"
            )
            base["oauth_token_endpoint"] = raw_spec.get("oauth_token_endpoint")
    else:
        args = raw_spec.get("stdio_args")
        env = raw_spec.get("stdio_env")
        packages = _normalize_packages(raw_spec.get("packages") or [])
        base["stdio_command"] = raw_spec.get("stdio_command")
        base["stdio_args"] = args if isinstance(args, list) else []
        base["stdio_env"] = env if isinstance(env, list) else []
        base["packages"] = packages
        has_launchable_command = (
            any(package["command"] in ALLOWED_MCP_COMMANDS for package in packages)
            or raw_spec.get("stdio_command") in ALLOWED_MCP_COMMANDS
        )
        if not has_launchable_command:
            base["requires_config"] = True
        if auth_type == MCPAuthType.OAUTH2:
            base["scopes"] = raw_spec.get("scopes") or []

    try:
        return _CONNECTION_SPEC_ADAPTER.validate_python(base).model_dump(mode="json")
    except ValidationError:
        return None


def _normalize_connection_options(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw_options = row.get("connection_options", row.get("options"))
    options: list[dict[str, Any]] = []

    if isinstance(raw_options, list):
        for index, raw_option in enumerate(raw_options):
            if not isinstance(raw_option, dict):
                continue
            spec = _normalize_connection_spec(raw_option)
            if spec is None:
                continue
            option_id = raw_option.get("id")
            if not isinstance(option_id, str) or not option_id.strip():
                option_id = f"{spec['server_type']}-{spec['auth_type'].lower()}-{index}"
            label = raw_option.get("label")
            if not isinstance(label, str) or not label.strip():
                label = spec["server_type"].upper()
            description = raw_option.get("description")
            docs_url = raw_option.get("docs", raw_option.get("docs_url"))
            options.append(
                {
                    "id": option_id.strip(),
                    "label": label.strip(),
                    "description": description.strip()
                    if isinstance(description, str) and description.strip()
                    else None,
                    "docs_url": docs_url
                    if isinstance(docs_url, str) and docs_url.strip()
                    else None,
                    "connection_spec": spec,
                }
            )
        return options

    spec = _normalize_connection_spec(row)
    if spec is None:
        return []

    return [
        {
            "id": f"{spec['server_type']}-{spec['auth_type'].lower()}",
            "label": spec["server_type"].upper(),
            "description": None,
            "docs_url": row.get("docs") if isinstance(row.get("docs"), str) else None,
            "connection_spec": spec,
        }
    ]


def _default_connection_spec(
    row: dict[str, Any], connection_options: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if not connection_options:
        return _normalize_connection_spec(row)

    raw_default = row.get("default_connection_option", row.get("default_option"))
    if isinstance(raw_default, str) and raw_default.strip():
        for option in connection_options:
            if option["id"] == raw_default.strip():
                return option["connection_spec"]

    return connection_options[0]["connection_spec"]


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


def get_platform_mcp_catalog_entries(
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
    for index, row in enumerate(servers):
        if not isinstance(row, dict):
            continue
        raw_slug = row.get("slug")
        raw_name = row.get("name")
        raw_description = row.get("description")
        raw_category = row.get("category")
        if not (
            isinstance(raw_slug, str)
            and raw_slug
            and isinstance(raw_name, str)
            and raw_name
            and isinstance(raw_description, str)
            and raw_description
            and isinstance(raw_category, str)
            and raw_category
        ):
            continue

        connection_options: list[dict[str, Any]] = []
        connection_spec: dict[str, Any] | None = None
        provider_id: str | None = None
        docs_url: str | None = None
        if include_private:
            raw_spec = row.get("connection_spec", row.get("metadata"))
            raw_options = row.get("connection_options", row.get("options"))
            has_connection_spec = isinstance(raw_spec, dict) or isinstance(
                raw_options, list
            )
            connection_options = _normalize_connection_options(row)
            connection_spec = _default_connection_spec(row, connection_options)
            if has_connection_spec and connection_spec is None:
                logger.warning(
                    "Skipping invalid MCP catalog connection metadata",
                    extra={"slug": raw_slug},
                )
            raw_provider_id = row.get("provider_id")
            provider_id = raw_provider_id if isinstance(raw_provider_id, str) else None
            raw_docs_url = row.get("docs")
            docs_url = raw_docs_url if isinstance(raw_docs_url, str) else None

        raw_status = row.get("status")
        if include_private and connection_spec is not None:
            status: PlatformMCPCatalogStatus = "available"
        elif raw_status in {"available", "coming_soon", "deprecated", "hidden"}:
            status = raw_status
        else:
            status = "coming_soon"

        icon_url = row.get("icon")
        entry: PlatformMCPCatalogEntry = {
            "id": catalog_id_for_slug(raw_slug),
            "slug": raw_slug,
            "name": raw_name,
            "description": raw_description,
            "category": raw_category,
            "status": status,
            "icon_url": icon_url if isinstance(icon_url, str) else None,
            "docs_url": docs_url,
            "provider_id": provider_id,
            "connection_spec": connection_spec,
            "connection_options": connection_options or None,
            "sort_key": f"{index:04d}:{raw_name.lower()}",
        }
        entries.append(entry)
    return entries


def get_platform_mcp_catalog_entry_by_slug(
    slug: str, *, include_private: bool = False
) -> PlatformMCPCatalogEntry | None:
    """Return one runtime catalog entry by stable slug."""
    for entry in get_platform_mcp_catalog_entries(include_private=include_private):
        if entry["slug"] == slug:
            return entry
    return None


def get_platform_mcp_catalog_entry_by_provider_id(
    provider_id: str, *, include_private: bool = False
) -> PlatformMCPCatalogEntry | None:
    """Return one runtime catalog entry by provider id."""
    for entry in get_platform_mcp_catalog_entries(include_private=include_private):
        if entry.get("provider_id") == provider_id:
            return entry
    return None
