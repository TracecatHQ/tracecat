"""Bind MCP connection requests to platform catalog connect recipes.

The platform catalog is repo-owned data; a workspace request references it by
``catalog_slug``. This module owns the binding between such a request and the
one connect recipe it targets, so service code never re-infers recipe identity
from transport/auth/URI heuristics.

The server URI policy is credential-driven. A recipe that declares a
``target: "server_uri"`` credential (or ships no URI at all) delegates the
URI to the user; a recipe with a bare literal URI is a fixed vendor endpoint
and must match exactly. Both are a security boundary: a trusted catalog row
must not be bound to an arbitrary host that would then receive its tokens
over the MCP transport, so a templated URI still pins the host shape around
its placeholders while leaving the path unconstrained.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from tracecat.integrations.catalog.loader import (
    _PLACEHOLDER_RE,
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
        host_pattern = _server_uri_host_pattern(
            spec.server_uri,
            placeholder_keys=[
                cred.key for cred in spec.credentials if cred.target == "server_uri"
            ],
        )
        if host_pattern is not None and not host_pattern.fullmatch(
            parsed.hostname or ""
        ):
            return "server URI host does not match the catalog recipe"
        return None
    if server_uri != spec.server_uri:
        return f"server URI must be {spec.server_uri}"
    return None


def _server_uri_host_pattern(
    template: str, *, placeholder_keys: Iterable[str] = ()
) -> re.Pattern[str] | None:
    """Compile the host part of a templated recipe URI into a host matcher.

    ``https://chronicle.{REGION}.rep.googleapis.com/mcp`` yields a pattern
    that accepts ``chronicle.eu.rep.googleapis.com`` but not a host that only
    embeds it. Placeholders are ``{NAME}``, ``<name>``, or a bare
    ``server_uri`` credential key written into the host (Scanner's
    ``mcp.your-env-here.scanner.dev``); each matches one or more host
    characters, while literal text must match exactly, case-insensitively.
    Returns ``None`` when the template has no ``scheme://host`` part, so
    recipes that ship no URI stay unconstrained. Ports, paths and query
    strings are never constrained.
    """
    _, scheme_sep, rest = template.partition("://")
    if not scheme_sep:
        return None
    host_template = re.split(r"[/?#]", rest, maxsplit=1)[0]
    # An explicit port never reaches urlparse().hostname, so drop it here.
    host_template = re.sub(r":\d+$", "", host_template)
    if not host_template:
        return None
    # Longest keys first so one key never splits inside another.
    alternatives = [_PLACEHOLDER_RE.pattern] + [
        re.escape(key) for key in sorted(set(placeholder_keys), key=len, reverse=True)
    ]
    placeholder_re = re.compile("(" + "|".join(alternatives) + ")")
    parts = [
        "[^/@?#]+" if placeholder_re.fullmatch(part) else re.escape(part)
        for part in placeholder_re.split(host_template)
        if part
    ]
    return re.compile("".join(parts), re.IGNORECASE)


def catalog_binding_is_current(
    *, catalog_slug: str, server_type: str, include_private: bool = True
) -> bool:
    """Whether a row's ``catalog_slug`` binding still matches a connect recipe.

    True when the entry exists and either offers no connect options at all
    (coming-soon and unentitled rows keep their binding) or at least one
    option's spec uses ``server_type``. A binding left behind by a retired or
    re-transported recipe, e.g. a stdio row on a now HTTP-only slug created
    during a rolling deploy, is stale and the row is treated as custom.
    """
    entry = get_platform_mcp_catalog_entry_by_slug(
        catalog_slug, include_private=include_private
    )
    if entry is None:
        return False
    options = connect_options(entry)
    if not options:
        return True
    return any(option.connection_spec.server_type == server_type for option in options)
