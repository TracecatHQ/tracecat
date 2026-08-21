"""Bind MCP connection requests to platform catalog connect recipes.

The platform catalog is repo-owned data; a workspace request references it by
``catalog_slug``. This module owns the binding between such a request and the
one connect recipe it targets, so service code never re-infers recipe identity
from transport/auth/URI heuristics.

The server URI policy is credential-driven. A recipe that declares a
``target: "server_uri"`` credential (or ships no URI at all) delegates the
URI to the user; a recipe with a bare literal URI is a fixed vendor endpoint
and must match its scheme, host, and path. A query string is permitted on a
fixed endpoint when the recipe itself has none, for providers that document
endpoint options such as MCP toolset selection. The host/path match remains a
security boundary: a trusted catalog row must not be bound to an arbitrary
host that would then receive its tokens over the MCP transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from tracecat.integrations.catalog.loader import (
    get_platform_mcp_catalog_entry_by_slug,
)
from tracecat.integrations.catalog.types import PlatformMCPCatalogEntry
from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.schemas import (
    MCPConnectionOption,
    MCPConnectionSpec,
    MCPHTTPCustomConnectionSpec,
    MCPHTTPNoneConnectionSpec,
    MCPHTTPOAuth2ConnectionSpec,
)
from tracecat.integrations.types import MCPServerType

MCPHTTPConnectionSpec = (
    MCPHTTPOAuth2ConnectionSpec
    | MCPHTTPCustomConnectionSpec
    | MCPHTTPNoneConnectionSpec
)


class CatalogConnectionError(ValueError):
    """Requested connection does not bind to a catalog connect recipe."""


@dataclass(slots=True, frozen=True)
class ResolvedCatalogConnection:
    """One catalog connect recipe a request has been bound to."""

    entry: PlatformMCPCatalogEntry
    option: MCPConnectionOption

    @property
    def option_id(self) -> str:
        return self.option.id

    @property
    def spec(self) -> MCPConnectionSpec:
        return self.option.connection_spec


def server_uri_is_user_supplied(spec: MCPHTTPConnectionSpec) -> bool:
    """Whether the recipe delegates the server URI to the user."""
    if any(cred.target == "server_uri" for cred in spec.credentials):
        return True
    return not spec.server_uri


def resolve_available_catalog_entry(slug: str) -> PlatformMCPCatalogEntry:
    """Return the available catalog entry for ``slug`` or raise."""
    entry = get_platform_mcp_catalog_entry_by_slug(slug, include_private=True)
    if entry is None:
        raise CatalogConnectionError("Platform MCP catalog row not found")
    if entry.status != "available":
        raise CatalogConnectionError(f"{entry.name} is not available to connect")
    return entry


def resolve_catalog_connection(
    entry: PlatformMCPCatalogEntry,
    *,
    server_type: MCPServerType,
    auth_type: MCPAuthType | None = None,
    server_uri: str | None = None,
) -> ResolvedCatalogConnection:
    """Bind a requested connection to one of ``entry``'s connect recipes.

    The request must satisfy exactly one recipe; ambiguity is an error rather
    than a guess.
    """
    options = connect_options(entry)
    if not options:
        raise CatalogConnectionError(f"{entry.name} is not connectable yet")

    mismatches = {
        option.id: _spec_mismatch(
            option.connection_spec,
            server_type=server_type,
            auth_type=auth_type,
            server_uri=server_uri,
        )
        for option in options
    }
    matches = [option for option in options if mismatches[option.id] is None]
    if not matches:
        # A single-recipe row can report precisely what failed; multi-recipe
        # rows fall back to the generic message rather than guessing intent.
        if len(options) == 1:
            raise CatalogConnectionError(f"{entry.name}: {mismatches[options[0].id]}")
        raise CatalogConnectionError(
            f"Requested server and auth configuration does not match any "
            f"connection option for {entry.name}"
        )
    if len(matches) > 1:
        raise CatalogConnectionError(
            f"Requested configuration matches multiple connection options for "
            f"{entry.name}"
        )
    return ResolvedCatalogConnection(entry=entry, option=matches[0])


def connect_options(entry: PlatformMCPCatalogEntry) -> list[MCPConnectionOption]:
    """Connect recipes on ``entry``, normalizing a bare ``connection_spec`` row."""
    if entry.connection_options:
        return entry.connection_options
    spec = entry.connection_spec
    if spec is None:
        return []
    return [
        MCPConnectionOption(
            id=f"{spec.server_type}-{spec.auth_type.lower()}",
            label=spec.server_type.upper(),
            docs_url=entry.docs_url,
            connection_spec=spec,
        )
    ]


def _spec_mismatch(
    spec: MCPConnectionSpec,
    *,
    server_type: MCPServerType,
    auth_type: MCPAuthType | None,
    server_uri: str | None,
) -> str | None:
    """Reason the request does not satisfy ``spec``, or ``None`` if it does."""
    if spec.server_type != server_type:
        return f"connection option is a {spec.server_type} server"
    if spec.server_type == "stdio":
        # Stdio requests carry no auth discriminator; credentials ride in
        # stdio_env and are validated separately against the catalog.
        return None
    if auth_type is None or not server_uri:
        return "server URI and auth type are required for http connections"
    if spec.auth_type != auth_type:
        return f"connection option uses {spec.auth_type} authentication"
    return _server_uri_mismatch(spec, server_uri=server_uri)


def _server_uri_mismatch(spec: MCPHTTPConnectionSpec, *, server_uri: str) -> str | None:
    if server_uri_is_user_supplied(spec):
        parsed = urlparse(server_uri)
        if parsed.username is not None or parsed.password is not None:
            return "server URI must not embed credentials"
        return None
    if server_uri != spec.server_uri:
        expected = urlparse(spec.server_uri)
        actual = urlparse(server_uri)
        # Catalog recipes without a query may accept provider-documented MCP
        # options (for example, ``?toolsets=core``). Keep every URL component
        # that controls the remote destination pinned to the catalog value.
        if (
            not expected.query
            and not actual.fragment
            and actual._replace(query="", fragment="").geturl() == spec.server_uri
        ):
            return None
        return f"server URI must be {spec.server_uri}"
    return None
