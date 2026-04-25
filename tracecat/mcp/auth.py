"""MCP server authentication and user resolution."""

from __future__ import annotations

import html
import json
import re
import time
import uuid
from base64 import urlsafe_b64decode
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.auth.cimd import CIMDDocument
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.redirect_validation import (
    validate_redirect_uri as validate_client_redirect_uri,
)
from fastmcp.server.dependencies import get_access_token
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper
from mcp.server.auth.provider import (
    AuthorizationParams,
    TokenError,
)
from mcp.shared.auth import InvalidRedirectUriError, OAuthClientInformationFull
from pydantic import AnyUrl, BaseModel, Field
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracecat import config
from tracecat.auth.credentials import compute_effective_scopes
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.config import (
    REDIS_URL,
    TRACECAT__PUBLIC_APP_URL,
)
from tracecat.contexts import ctx_role
from tracecat.db.engine import (
    get_async_session_bypass_rls_context_manager,
)
from tracecat.db.models import (
    Membership,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.mcp.oidc.config import (
    INTERNAL_CLIENT_ID,
    get_internal_client_secret,
    get_internal_discovery_url,
)
from tracecat.mcp.oidc.features import (
    OFFLINE_ACCESS_SCOPE,
    get_supported_scopes,
)


class MCPTokenIdentity(BaseModel):
    """Identity extracted from the active MCP access token."""

    client_id: str
    email: str | None = None
    organization_ids: frozenset[uuid.UUID] = Field(default_factory=frozenset)
    workspace_ids: frozenset[uuid.UUID] = Field(default_factory=frozenset)


_UUID_SCOPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "organization": re.compile(
        r"^(?:organization|org|organization_id|org_id):(?P<uuid>[0-9a-fA-F-]{36})$"
    ),
    "workspace": re.compile(r"^(?:workspace|workspace_id):(?P<uuid>[0-9a-fA-F-]{36})$"),
}

_MCP_ACCESS_TOKEN_FALLBACK_EXPIRY_SECONDS = 24 * 60 * 60
_MCP_OAUTH_TRANSACTION_TTL_SECONDS = 15 * 60
_MCP_TOKEN_ENDPOINT_AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class _UserinfoFetchError(RuntimeError):
    """Raised when the upstream userinfo endpoint cannot be queried."""


def append_scope_if_missing(scopes: list[str], scope: str) -> list[str]:
    """Append a scope only if it is not already present."""
    if scope in scopes:
        return scopes
    return [*scopes, scope]


def merge_unique_scopes(scopes: list[str], extra_scopes: Sequence[str]) -> list[str]:
    """Append extra scopes while preserving order and uniqueness."""
    merged = scopes
    for scope in extra_scopes:
        merged = append_scope_if_missing(merged, scope)
    return merged


def merge_scope_string(
    scope: str | None,
    extra_scopes: Sequence[str],
) -> str:
    """Return a space-separated scope string with required scopes appended."""
    merged_scopes = merge_unique_scopes(scope.split() if scope else [], extra_scopes)
    return " ".join(merged_scopes)


def remove_scope(scopes: list[str], scope: str) -> list[str]:
    """Return a scope list with the target scope removed."""
    return [value for value in scopes if value != scope]


def supports_refresh_scope(scopes_supported: Sequence[str] | None) -> bool:
    """Return whether provider metadata supports MCP refresh scope requests."""
    if scopes_supported is None:
        # If provider metadata omits scopes_supported, optimistically request.
        return True
    return OFFLINE_ACCESS_SCOPE in scopes_supported


def _patch_oauth_metadata_route(app: ASGIApp) -> ASGIApp:
    """Patch only the advertised token auth methods on discovery responses."""

    async def patched_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        start_message: Message | None = None
        body_chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            nonlocal start_message

            match message["type"]:
                case "http.response.start":
                    start_message = dict(message)
                case "http.response.body":
                    body_chunks.append(message.get("body", b""))
                case _:
                    await send(message)

        await app(scope, receive, capture)

        if start_message is None:
            return

        body = b"".join(body_chunks)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            payload["token_endpoint_auth_methods_supported"] = (
                _MCP_TOKEN_ENDPOINT_AUTH_METHODS
            )
            body = json.dumps(payload).encode("utf-8")

        headers = MutableHeaders(raw=start_message["headers"])
        headers["content-length"] = str(len(body))
        await send(start_message)
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return patched_app


def _normalize_loopback_path(path: str) -> str:
    normalized = path or "/"
    return normalized.rstrip("/") or "/"


def _parse_loopback_redirect_uri(value: str) -> tuple[str, str, str, str] | None:
    parsed = urlparse(value)
    host = parsed.hostname.lower() if parsed.hostname else None
    if host not in _LOOPBACK_HOSTS:
        return None
    return (
        parsed.scheme.lower(),
        host,
        _normalize_loopback_path(parsed.path),
        parsed.query,
    )


def _matches_cimd_loopback_redirect_uri(
    *,
    redirect_uri: AnyUrl,
    cimd_document: CIMDDocument,
    allowed_redirect_uri_patterns: list[str] | None,
) -> bool:
    requested = _parse_loopback_redirect_uri(str(redirect_uri))
    if requested is None:
        return False

    if allowed_redirect_uri_patterns is not None and not validate_client_redirect_uri(
        redirect_uri=redirect_uri,
        allowed_patterns=allowed_redirect_uri_patterns,
    ):
        return False

    requested_signature = requested
    for registered_uri in cimd_document.redirect_uris:
        if "*" in registered_uri:
            continue
        if _parse_loopback_redirect_uri(registered_uri) == requested_signature:
            return True
    return False


def _coerce_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return uuid.UUID(text)
        except ValueError:
            return None
    return None


def _extract_uuid_set(value: object) -> set[uuid.UUID]:
    if isinstance(value, (list, tuple, set, frozenset)):
        extracted: set[uuid.UUID] = set()
        for item in value:
            uid = _coerce_uuid(item)
            if uid is not None:
                extracted.add(uid)
        return extracted
    if isinstance(value, str):
        direct = _coerce_uuid(value)
        if direct is not None:
            return {direct}
        candidates = re.split(r"[\s,]+", value.strip())
        extracted = set()
        for candidate in candidates:
            uid = _coerce_uuid(candidate)
            if uid is not None:
                extracted.add(uid)
        return extracted
    return set()


def _extract_claimed_uuids(
    claims: dict[str, object], keys: tuple[str, ...]
) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for key in keys:
        if key in claims:
            ids.update(_extract_uuid_set(claims[key]))
    return ids


def _extract_scope_uuids(scopes: list[str], resource: str) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    pattern = _UUID_SCOPE_PATTERNS[resource]
    for scope in scopes:
        match = pattern.match(scope)
        if match is None:
            continue
        uid = _coerce_uuid(match.group("uuid"))
        if uid is not None:
            ids.add(uid)
    return ids


def get_email_claim(claims: Mapping[str, object]) -> str | None:
    """Extract an email claim from FastMCP token claims."""
    match claims:
        case {"email": str(raw_email)} if email := raw_email.strip():
            return email
        case {"upstream_claims": {"email": str(raw_email)}} if (
            email := raw_email.strip()
        ):
            return email
        case _:
            return None


def _decode_unverified_id_token_claims(id_token: str) -> dict[str, object]:
    """Decode a JWT payload without signature verification.

    The upstream token exchange has already validated the token. This helper only
    extracts claims for local attribution logic (email/org/workspace hints).
    """
    payload_b64 = id_token.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims = json.loads(urlsafe_b64decode(padded))
    if not isinstance(claims, dict):
        raise ValueError("id_token payload is not an object")
    return claims


def _normalize_email_claim(value: object) -> str | None:
    if isinstance(value, str) and (email := value.strip()):
        return email
    return None


def _normalize_subject_claim(value: object) -> str | None:
    if isinstance(value, str) and (subject := value.strip()):
        return subject
    return None


def _merge_fastmcp_token_claims(
    *,
    validated_claims: Mapping[str, object],
    fastmcp_claims: Mapping[str, object],
) -> dict[str, object]:
    """Merge proxy JWT claims back into the validated upstream token claims.

    FastMCP's OAuth proxy validates tool requests by swapping its own JWT for the
    upstream provider access token. That means downstream callers often see the
    upstream token claims, which may omit identity data that Tracecat embedded in
    the proxy JWT under ``upstream_claims``. Preserve those proxy claims here so
    request-scoped identity helpers can read a consistent claim set.
    """

    merged = dict(validated_claims)

    proxy_upstream_claims_obj = fastmcp_claims.get("upstream_claims")
    if isinstance(proxy_upstream_claims_obj, Mapping):
        proxy_upstream_claims = dict(
            cast(Mapping[str, object], proxy_upstream_claims_obj)
        )
        merged["upstream_claims"] = proxy_upstream_claims
        if _normalize_email_claim(merged.get("email")) is None and (
            email := _normalize_email_claim(proxy_upstream_claims.get("email"))
        ):
            merged["email"] = email

    if isinstance(fastmcp_claims.get("client_id"), str):
        merged["client_id"] = fastmcp_claims["client_id"]

    return merged


def _extract_fastmcp_scopes(fastmcp_claims: Mapping[str, object]) -> list[str] | None:
    raw_scope = fastmcp_claims.get("scope")
    if isinstance(raw_scope, str):
        return [scope for scope in raw_scope.split() if scope]
    if isinstance(raw_scope, list) and all(
        isinstance(scope, str) for scope in raw_scope
    ):
        return [scope for scope in raw_scope if scope]
    return None


async def _fetch_userinfo_claims(
    *,
    access_token: str,
    userinfo_endpoint: str,
) -> dict[str, object]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("userinfo payload is not an object")
    return payload


def get_token_identity() -> MCPTokenIdentity:
    """Extract normalized caller identity from the current access token."""
    access_token = get_access_token()
    if access_token is None:
        raise ValueError("Authentication required")

    claims = access_token.claims
    email = get_email_claim(claims)
    raw_client_ids = [
        claims.get("client_id"),
        claims.get("azp"),
        access_token.client_id,
        claims.get("sub"),
    ]
    client_id = next(
        (c for raw in raw_client_ids if isinstance(raw, str) and (c := raw.strip())),
        "",
    )

    organization_ids = _extract_claimed_uuids(
        claims,
        ("organization_id", "org_id", "organization_ids", "org_ids"),
    )
    workspace_ids = _extract_claimed_uuids(claims, ("workspace_id", "workspace_ids"))
    organization_ids.update(_extract_scope_uuids(access_token.scopes, "organization"))
    workspace_ids.update(_extract_scope_uuids(access_token.scopes, "workspace"))

    return MCPTokenIdentity(
        client_id=client_id,
        email=email,
        organization_ids=frozenset(organization_ids),
        workspace_ids=frozenset(workspace_ids),
    )


_LOGO_SVG_PATH = (
    "M261.456 68.1865C253.628 78.8783 245.659 90.7908 240.215 99.3206L234.056"
    " 108.973L222.846 110.522C123.266 124.283 49.3346 204.676 49.3346 298.91C"
    "49.3346 402.366 138.692 489.349 252.84 489.349C366.987 489.349 456.345"
    " 402.366 456.345 298.91C456.345 272.743 450.717 247.836 440.51 225.141L"
    "485.372 204.259C498.435 233.304 505.68 265.317 505.68 298.91C505.68"
    " 433.526 390.725 539.539 252.84 539.539C114.955 539.539 0 433.526 0"
    " 298.91C0 180.275 89.4713 83.6982 204.954 62.5939C211.414 52.8463"
    " 219.42 41.2854 227.08 31.2619C232.164 24.6104 237.631 17.9264 242.706"
    " 12.8398C245.15 10.3898 248.357 7.43692 252.022 5.07425C253.86 3.88898"
    " 256.633 2.31261 260.123 1.23909C263.537 0.189061 269.401 -0.910787"
    " 276.139 1.21079C284.943 3.98294 289.95 10.3077 292.063 13.3053C294.532"
    " 16.8064 296.304 20.5241 297.527 23.3536C299.427 27.7515 301.309 33.2062"
    " 302.832 37.6211C303.208 38.711 303.563 39.7375 303.89 40.6692C305.279"
    " 44.6261 306.424 47.6275 307.418 49.8493C326.525 54.1155 357.134 61.9477"
    " 377.952 67.2747C379.459 67.6605 380.916 68.0331 382.313 68.3903C388.73"
    " 64.0835 396.285 59.4715 403.848 55.712C409.735 52.785 416.722 49.8186"
    " 423.791 48.2435C429.641 46.94 441.939 45.0794 453.115 52.5971L462.517"
    " 58.9219L463.971 70.2935C471.374 128.204 454.415 194.788 418.555"
    " 238.317C400.323 260.447 376.215 277.729 346.885 283.278C317.261"
    " 288.882 285.571 281.897 253.683 261.533L279.913 219.025C303.413"
    " 234.032 322.656 236.811 337.866 233.934C353.368 231.001 367.992"
    " 221.557 380.744 206.078C401.373 181.037 414.449 143.211 416.16"
    " 106.009C410.774 109.286 405.66 112.825 401.922 115.65L392.58"
    " 122.71L381.284 119.864C376.943 118.771 371.274 117.321 364.838"
    " 115.675C341.296 109.653 307.494 101.007 290.939 97.5985C276.198"
    " 94.5637 268.666 82.3324 265.783 77.1863C264.166 74.2989 262.727"
    " 71.2126 261.456 68.1865ZM434.729 97.1981C434.729 97.1984 434.715"
    " 97.2006 434.687 97.2038C434.715 97.1994 434.729 97.1978 434.729"
    " 97.1981ZM309.4 53.4976C309.396 53.5217 309.257 53.3574 308.995"
    " 52.9324C309.272 53.261 309.404 53.4735 309.4 53.4976Z"
)


def _get_tracecat_logo_markup(fill_color: str = "#1C1C1C") -> str:
    """Return inline SVG markup for the Tracecat logo mark."""
    return (
        '<svg aria-label="Tracecat" width="30" height="30" viewBox="0 0 506 540"'
        f' fill="none" xmlns="http://www.w3.org/2000/svg">'
        f'<path fill-rule="evenodd" clip-rule="evenodd" d="{_LOGO_SVG_PATH}"'
        f' fill="{fill_color}"/></svg>'
    )


def _build_oidc_consent_html(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
) -> str:
    """Render a custom consent page for the OIDC interactive flow."""
    escaped_client_id = html.escape(client_id, quote=True)
    escaped_redirect_uri = html.escape(redirect_uri, quote=True)
    escaped_txn_id = html.escape(txn_id, quote=True)
    escaped_csrf_token = html.escape(csrf_token, quote=True)
    scope_items = (
        "".join(f"<li>{html.escape(scope, quote=True)}</li>" for scope in scopes)
        or "<li>No scopes requested</li>"
    )
    logo_markup = _get_tracecat_logo_markup(fill_color="#FFFFFF")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Authorize MCP client</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: #ffffff;
      color: #111827;
    }}
    .stack {{
      width: min(520px, 100%);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 18px;
    }}
    .logo-badge {{
      width: 64px;
      height: 64px;
      border-radius: 14px;
      background: #111827;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #111827;
    }}
    .card {{
      width: 100%;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      background: #ffffff;
      padding: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: 1.625rem;
      line-height: 1.2;
      letter-spacing: -0.01em;
      font-weight: 600;
    }}
    .subtitle {{
      margin: 8px 0 0;
      color: #6b7280;
      font-size: 0.95rem;
      line-height: 1.5;
    }}
    .panel {{
      margin-top: 16px;
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      background: #f9fafb;
      font-size: 0.92rem;
      line-height: 1.5;
    }}
    .kv-label {{
      color: #6b7280;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-top: 8px;
    }}
    .kv-label:first-child {{
      margin-top: 0;
    }}
    code {{
      display: block;
      margin-top: 4px;
      padding: 6px 8px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #ffffff;
      color: #111827;
      font-size: 0.8rem;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }}
    .scopes-title {{
      margin-top: 10px;
      color: #6b7280;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    ul {{
      margin: 8px 0 0;
      padding-left: 18px;
    }}
    .actions {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
    }}
    .decision {{
      appearance: none;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 9px 14px;
      background: #ffffff;
      color: #111827;
      font-weight: 600;
      font-size: 0.9rem;
      cursor: pointer;
      min-width: 96px;
    }}
    .decision.primary {{
      background: #111827;
      border-color: #111827;
      color: #ffffff;
    }}
    .footnote {{
      margin-top: 10px;
      color: #6b7280;
      font-size: 0.78rem;
    }}
    .footnote code {{
      display: inline;
      margin: 0;
      padding: 0;
      border: 0;
      background: transparent;
      font-size: inherit;
    }}
  </style>
</head>
<body>
  <div class="stack">
    <div class="logo-badge">
      {logo_markup}
    </div>
    <div class="card">
      <h1>Authorize MCP client</h1>
      <p class="subtitle">This client is requesting access to your Tracecat account.</p>
      <div class="panel">
        <div class="kv-label">Client ID</div>
        <code>{escaped_client_id}</code>
        <div class="kv-label">Redirect URI</div>
        <code>{escaped_redirect_uri}</code>
        <div class="scopes-title">Requested scopes</div>
        <ul>{scope_items}</ul>
        <div class="footnote">Transaction: <code>{escaped_txn_id}</code></div>
      </div>
      <form action="/consent" method="post">
        <input type="hidden" name="txn_id" value="{escaped_txn_id}" />
        <input type="hidden" name="csrf_token" value="{escaped_csrf_token}" />
        <div class="actions">
          <button class="decision primary" type="submit" name="action" value="approve">Allow</button>
          <button class="decision" type="submit" name="action" value="deny">Deny</button>
        </div>
      </form>
    </div>
  </div>
</body>
</html>"""


def _create_oidc_auth() -> OIDCProxy:
    """Build the OIDC auth provider for external MCP."""
    base_url = TRACECAT__PUBLIC_APP_URL.rstrip("/")

    # The internal OIDC issuer lives on the API server. The MCP server
    # uses it as the upstream identity provider instead of an external BYO
    # OIDC IdP. Keep ``offline_access`` out of ``_required_scopes`` so
    # validated tool tokens do not require it. Advertise and default the
    # refresh scope only when refresh-token support is enabled for this
    # deployment.
    _required_scopes = ["openid", "profile", "email"]
    _supported_scopes = get_supported_scopes()

    class TracecatProxyDCRClient(ProxyDCRClient):
        """Relax CIMD loopback callback validation to allow ephemeral local ports."""

        def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
            try:
                return super().validate_redirect_uri(redirect_uri)
            except InvalidRedirectUriError:
                if (
                    redirect_uri is None
                    or self.cimd_document is None
                    or not _matches_cimd_loopback_redirect_uri(
                        redirect_uri=redirect_uri,
                        cimd_document=self.cimd_document,
                        allowed_redirect_uri_patterns=self.allowed_redirect_uri_patterns,
                    )
                ):
                    raise
                return redirect_uri

    class TracecatOIDCProxy(OIDCProxy):
        """OIDC proxy with user-existence validation and a custom consent page."""

        async def register_client(
            self, client_info: OAuthClientInformationFull
        ) -> None:
            stored_client = client_info.model_copy(
                update={
                    "scope": merge_scope_string(client_info.scope, _required_scopes)
                }
            )
            await super().register_client(stored_client)

        async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
            client = await super().get_client(client_id)
            if client is None:
                return None

            client = client.model_copy(
                update={"scope": merge_scope_string(client.scope, _required_scopes)}
            )

            if not isinstance(client, ProxyDCRClient) or client.cimd_document is None:
                return client
            if isinstance(client, TracecatProxyDCRClient):
                return client
            return TracecatProxyDCRClient.model_validate(client.model_dump())

        async def authorize(
            self,
            client: OAuthClientInformationFull,
            params: AuthorizationParams,
        ) -> str:
            """Merge all required OIDC scopes into the authorization request.

            Request ``offline_access`` optimistically even if the upstream OIDC
            metadata omits it — some providers issue refresh tokens despite not
            advertising the scope in discovery metadata.  If the provider truly
            rejects the scope, ``_retry_without_refresh_scope()`` handles a
            single retry without it.
            """
            scopes = merge_unique_scopes(list(params.scopes or []), _required_scopes)
            params_with_scopes = params.model_copy(update={"scopes": scopes})
            return await super().authorize(client, params_with_scopes)

        async def _retry_without_refresh_scope(
            self,
            *,
            request: Request,
        ) -> RedirectResponse | None:
            """Retry OAuth authorization once without refresh scope on invalid_scope."""
            if request.query_params.get("error") != "invalid_scope":
                return None

            txn_id = request.query_params.get("state")
            if not txn_id:
                return None

            txn_model = await self._transaction_store.get(key=txn_id)
            if txn_model is None:
                return None

            scopes = list(txn_model.scopes or [])
            if OFFLINE_ACCESS_SCOPE not in scopes:
                # We already retried (or refresh scope was never requested).
                return None

            updated_scopes = remove_scope(scopes, OFFLINE_ACCESS_SCOPE)
            updated_txn = txn_model.model_copy(update={"scopes": updated_scopes})

            age_seconds = max(0.0, time.time() - float(txn_model.created_at))
            remaining_ttl = max(
                1, int(_MCP_OAUTH_TRANSACTION_TTL_SECONDS - age_seconds)
            )
            await self._transaction_store.put(
                key=txn_id,
                value=updated_txn,
                ttl=remaining_ttl,
            )

            logger.warning(
                "OIDC provider rejected refresh scope; retrying authorization without refresh scope",
                scope=OFFLINE_ACCESS_SCOPE,
                transaction_id=txn_id,
            )

            retry_url = self._build_upstream_authorize_url(
                txn_id,
                updated_txn.model_dump(),
            )
            return RedirectResponse(url=retry_url)

        async def _extract_upstream_claims(
            self, idp_tokens: dict[str, Any]
        ) -> dict[str, Any] | None:
            """Extract claims from the internal OIDC issuer's tokens.

            The internal issuer already validated that the user exists in the
            Tracecat DB and resolved their organization, so we only need to
            decode the id_token to extract claims.
            """
            id_token = idp_tokens.get("id_token")
            if isinstance(id_token, str) and id_token:
                try:
                    claims = _decode_unverified_id_token_claims(id_token)
                except Exception:
                    claims = {}
                if email := _normalize_email_claim(claims.get("email")):
                    return {
                        "email": email,
                        "organization_id": claims.get("organization_id"),
                        "is_platform_superuser": claims.get(
                            "is_platform_superuser", False
                        ),
                    }

            # Fallback to the standard email resolution path.
            try:
                email = await self._resolve_idp_email(idp_tokens)
            except Exception as exc:
                raise TokenError(
                    "invalid_grant",
                    "Failed to resolve OIDC email claims from internal issuer",
                ) from exc

            if email is None:
                raise TokenError(
                    "invalid_client",
                    "No email claim in internal issuer tokens",
                )
            return {"email": email}

        async def load_access_token(self, token: str) -> AccessToken | None:
            """Preserve FastMCP JWT identity claims after upstream token validation."""
            access_token = cast(
                AccessToken | None, await super().load_access_token(token)
            )
            if access_token is None:
                return None

            try:
                fastmcp_claims = self.jwt_issuer.verify_token(token)
            except Exception as exc:
                logger.warning(
                    "Failed to decode FastMCP token claims during MCP auth",
                    error=str(exc),
                )
                return access_token

            merged_claims = _merge_fastmcp_token_claims(
                validated_claims=access_token.claims,
                fastmcp_claims=fastmcp_claims,
            )
            scopes = access_token.scopes
            if (fastmcp_scopes := _extract_fastmcp_scopes(fastmcp_claims)) is not None:
                scopes = fastmcp_scopes
            client_id = access_token.client_id
            if not client_id and isinstance(fastmcp_claims.get("client_id"), str):
                client_id = fastmcp_claims["client_id"].strip()

            return access_token.model_copy(
                update={
                    "claims": merged_claims,
                    "client_id": client_id,
                    "scopes": scopes,
                }
            )

        async def _handle_idp_callback(
            self, request: Request
        ) -> HTMLResponse | RedirectResponse:
            if fallback_response := await self._retry_without_refresh_scope(
                request=request
            ):
                return fallback_response

            response = await super()._handle_idp_callback(request)
            if not isinstance(response, RedirectResponse):
                return response

            if not config.TRACECAT__EE_MULTI_TENANT:
                return response

            location = response.headers.get("location")
            if location is None:
                return response

            parsed = urlparse(location)
            callback_codes = parse_qs(parsed.query).get("code")
            if not callback_codes:
                return response

            auth_code = callback_codes[0]
            if not auth_code:
                return response

            try:
                code_model = await self._code_store.get(key=auth_code)
                if code_model is None:
                    return response

                payload = code_model.model_dump()
                idp_tokens = payload.get("idp_tokens")
                if not isinstance(idp_tokens, dict):
                    return response

                email = await self._resolve_idp_email(idp_tokens)
                if email is None:
                    return response

                from tracecat_ee.watchtower.service import (
                    maybe_create_oauth_provisional_session,
                )

                raw_client_id = payload.get("client_id")
                auth_client_id = (
                    raw_client_id.strip()
                    if isinstance(raw_client_id, str) and raw_client_id.strip()
                    else None
                )

                await maybe_create_oauth_provisional_session(
                    email=email,
                    auth_client_id=auth_client_id,
                    auth_transaction_id=request.query_params.get("state"),
                    user_agent=request.headers.get("user-agent"),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to record Watchtower OAuth callback",
                    error=str(exc),
                )

            return response

        async def _resolve_idp_email(
            self, idp_tokens: Mapping[str, object]
        ) -> str | None:
            id_token_subject: str | None = None
            id_token = idp_tokens.get("id_token")
            if isinstance(id_token, str) and id_token:
                claims = _decode_unverified_id_token_claims(id_token)
                id_token_subject = _normalize_subject_claim(claims.get("sub"))
                if email := _normalize_email_claim(claims.get("email")):
                    return email

            access_token = idp_tokens.get("access_token")
            userinfo_endpoint = self.oidc_config.userinfo_endpoint
            if isinstance(access_token, str) and userinfo_endpoint:
                try:
                    userinfo = await _fetch_userinfo_claims(
                        access_token=access_token,
                        userinfo_endpoint=str(userinfo_endpoint),
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch upstream userinfo for MCP auth",
                        error=str(exc),
                    )
                    raise _UserinfoFetchError from exc
                else:
                    if id_token_subject is not None:
                        userinfo_subject = _normalize_subject_claim(userinfo.get("sub"))
                        if userinfo_subject != id_token_subject:
                            logger.warning(
                                "Rejected upstream userinfo subject mismatch for MCP auth",
                                id_token_subject=id_token_subject,
                                userinfo_subject=userinfo_subject,
                            )
                            return None
                    return _normalize_email_claim(userinfo.get("email"))

            return None

        async def _show_consent_page(
            self, request: Request
        ) -> HTMLResponse | RedirectResponse:
            response = await super()._show_consent_page(request)
            if not isinstance(response, HTMLResponse):
                return response

            txn_id = request.query_params.get("txn_id")
            if txn_id is None:
                return response

            txn_model = await self._transaction_store.get(key=txn_id)
            if txn_model is None:
                return response

            txn = txn_model.model_dump()
            csrf_token = txn.get("csrf_token")
            client_id = txn.get("client_id")
            redirect_uri = txn.get("client_redirect_uri")
            scopes = txn.get("scopes") or []

            if (
                not isinstance(csrf_token, str)
                or not isinstance(client_id, str)
                or not isinstance(redirect_uri, str)
                or not isinstance(scopes, list)
            ):
                return response

            response.body = _build_oidc_consent_html(
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=[str(scope) for scope in scopes],
                txn_id=txn_id,
                csrf_token=csrf_token,
            ).encode("utf-8")
            response.headers["content-length"] = str(len(response.body))
            return response

        def get_routes(self, mcp_path: str | None = None) -> list[Route]:
            """Patch OAuth metadata to advertise public-client auth (``"none"``)."""
            routes = super().get_routes(mcp_path)
            if self.base_url is None:
                return routes

            for route in routes:
                if not (
                    isinstance(route, Route)
                    and route.path.startswith("/.well-known/oauth-authorization-server")
                ):
                    continue

                route.app = _patch_oauth_metadata_route(route.app)

            return routes

    # Build Redis-backed storage for OAuth state (client registrations,
    # auth codes, tokens, transactions) so state survives restarts and
    # is shared across MCP replicas.
    redis_client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
    redis_store = RedisStore(client=redis_client)
    prefixed_store = PrefixCollectionsWrapper(redis_store, prefix="mcp")
    if config.TRACECAT__DB_ENCRYPTION_KEY:
        client_storage = FernetEncryptionWrapper(
            prefixed_store, fernet=Fernet(config.TRACECAT__DB_ENCRYPTION_KEY)
        )
    else:
        logger.warning(
            "TRACECAT__DB_ENCRYPTION_KEY is not set; "
            "MCP OAuth state will be stored unencrypted in Redis"
        )
        client_storage = prefixed_store

    # Use the internal issuer's discovery URL for server-to-server
    # communication (avoids hairpin NAT through the reverse proxy).
    config_url = get_internal_discovery_url()
    auth = TracecatOIDCProxy(
        config_url=config_url,
        client_id=INTERNAL_CLIENT_ID,
        client_secret=get_internal_client_secret(),
        base_url=base_url,
        client_storage=client_storage,
        fallback_access_token_expiry_seconds=_MCP_ACCESS_TOKEN_FALLBACK_EXPIRY_SECONDS,
        algorithm="ES256",
    )
    # Patch client_registration_options so the MCP SDK's registration
    # handler advertises and accepts the full scope set.  Do NOT pass
    # required_scopes to the constructor — it flows into the JWT
    # verifier which then rejects any token missing those scopes.
    if auth.client_registration_options is not None:
        auth.client_registration_options.valid_scopes = _supported_scopes
        auth.client_registration_options.default_scopes = _supported_scopes
    auth._default_scope_str = " ".join(_supported_scopes)
    if auth._cimd_manager is not None:
        auth._cimd_manager.default_scope = auth._default_scope_str
    return auth


def create_mcp_auth() -> AuthProvider:
    """Build the auth provider for external MCP."""
    return _create_oidc_auth()


async def resolve_user_by_email(email: str) -> User:
    """Look up a user by email, raising if not found."""
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(select(User).filter_by(email=email))
        user = result.scalars().first()
        if user is None:
            raise ValueError(f"No user found for email: {email}")
        return user


async def resolve_org_membership(
    user_id: UserID,
    organization_id: OrganizationID,
) -> OrgRole:
    """Check the user belongs to a specific organization.

    The OrganizationMembership model is a simple link table without a role
    column. Membership presence means the user is at least a member.

    Args:
        user_id: The user to look up.
        organization_id: The organization to check membership in.

    Returns:
        OrgRole.MEMBER — the presence of a membership row confirms access.

    Raises:
        ValueError: If the user has no membership in the organization.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
        membership = result.scalars().first()
        if membership is None:
            raise ValueError(
                f"User {user_id} has no membership in organization {organization_id}"
            )
        return OrgRole.MEMBER


async def resolve_workspace_org(workspace_id: WorkspaceID) -> OrganizationID:
    """Look up which organization a workspace belongs to.

    Raises:
        ValueError: If the workspace does not exist.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        org_id = result.scalar_one_or_none()
        if org_id is None:
            raise ValueError(f"Workspace {workspace_id} not found")
        return org_id


async def resolve_workspace_membership(
    user_id: UserID,
    workspace_id: WorkspaceID,
) -> WorkspaceRole:
    """Verify user has access to workspace.

    The Membership model is a simple link table without a role column.
    Membership presence grants editor-level access.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        )
        membership = result.scalars().first()
        if membership is None:
            raise ValueError(
                f"User {user_id} does not have access to workspace {workspace_id}"
            )
        return WorkspaceRole.EDITOR


def _raise_if_multi_tenant_superuser(user: User) -> None:
    """Block platform superusers from tenant MCP context in multi-tenant mode."""
    if config.TRACECAT__EE_MULTI_TENANT and user.is_superuser:
        raise ValueError(
            "Platform superusers cannot access tenant MCP context in multi-tenant mode"
        )


async def resolve_role(email: str, workspace_id: WorkspaceID) -> Role:
    """Resolve a user's Role for a given workspace from their OAuth email.

    Pipeline: email -> User -> Workspace.organization_id -> scopes/membership -> Role

    Org admins/owners (users with ``org:workspace:read`` scope) bypass the
    workspace-level membership check, matching the behaviour of the main API.
    Single-tenant platform superusers also bypass direct membership checks.
    """
    user = await resolve_user_by_email(email)
    _raise_if_multi_tenant_superuser(user)

    org_id = await resolve_workspace_org(workspace_id)

    # Compute scopes early so we can check for org-level workspace access
    role = Role(
        type="user",
        user_id=user.id,
        workspace_id=workspace_id,
        organization_id=org_id,
        service_id="tracecat-mcp",
        is_platform_superuser=user.is_superuser,
    )
    scopes = await compute_effective_scopes(role)

    # Platform superusers and org admins/owners can access any workspace.
    if not user.is_superuser and not has_scope(scopes, "org:workspace:read"):
        await resolve_workspace_membership(user.id, workspace_id)

    role = role.model_copy(update={"scopes": scopes})
    # Set context variable so downstream services that rely on ctx_role
    # (e.g. SecretsService.with_session()) can resolve the role automatically.
    ctx_role.set(role)
    return role


async def list_user_workspaces(
    email: str,
    organization_ids: frozenset[OrganizationID] | None = None,
) -> list[dict[str, str]]:
    """List workspaces accessible to the user.

    Users with ``org:workspace:read`` scope (org admins/owners) or platform
    superusers in single-tenant mode see every workspace in their
    organization(s). Other users see only workspaces where they have an explicit
    Membership row.
    """
    user = await resolve_user_by_email(email)
    _raise_if_multi_tenant_superuser(user)

    async with get_async_session_bypass_rls_context_manager() as session:

        async def _list_direct_membership_rows(
            scoped_org_ids: set[OrganizationID] | None = None,
        ) -> list[tuple[uuid.UUID, str]]:
            member_stmt = (
                select(Workspace.id, Workspace.name)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(Membership.user_id == user.id)
            )
            if scoped_org_ids:
                member_stmt = member_stmt.where(
                    Workspace.organization_id.in_(scoped_org_ids)
                )
            member_result = await session.execute(member_stmt)
            return sorted(
                member_result.tuples().all(),
                key=lambda item: (item[1], str(item[0])),
            )

        if user.is_superuser:
            stmt = select(Workspace.id, Workspace.name)
            if organization_ids:
                stmt = stmt.where(Workspace.organization_id.in_(organization_ids))
            stmt = stmt.order_by(Workspace.name.asc(), Workspace.id.asc())
            result = await session.execute(stmt)
            return [
                {"id": str(workspace_id), "name": workspace_name}
                for workspace_id, workspace_name in result.tuples().all()
            ]

        # Resolve the user's organization(s)
        org_stmt = select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == user.id
        )
        org_result = await session.execute(org_stmt)
        org_ids = set(org_result.scalars().all())

        if organization_ids:
            org_ids &= set(organization_ids)
            if not org_ids:
                return [
                    {"id": str(workspace_id), "name": name}
                    for workspace_id, name in await _list_direct_membership_rows(
                        set(organization_ids)
                    )
                ]

        # Determine org-admin visibility on a per-organization basis.
        org_admin_ids: set[OrganizationID] = set()
        for oid in org_ids:
            role = Role(
                type="user",
                user_id=user.id,
                organization_id=oid,
                workspace_id=None,
                service_id="tracecat-mcp",
                is_platform_superuser=user.is_superuser,
            )
            scopes = await compute_effective_scopes(role)
            if has_scope(scopes, "org:workspace:read"):
                org_admin_ids.add(oid)

        workspace_map: dict[uuid.UUID, str] = {}

        # For orgs where the user is org-admin/owner, list all workspaces in that org.
        if org_admin_ids:
            admin_stmt = select(Workspace.id, Workspace.name).where(
                Workspace.organization_id.in_(org_admin_ids)
            )
            admin_result = await session.execute(admin_stmt)
            for workspace_id, workspace_name in admin_result.tuples().all():
                workspace_map[workspace_id] = workspace_name

        # For other orgs, list only direct workspace memberships.
        member_org_ids = org_ids - org_admin_ids
        if member_org_ids:
            member_stmt = (
                select(Workspace.id, Workspace.name)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(
                    Membership.user_id == user.id,
                    Workspace.organization_id.in_(member_org_ids),
                )
            )
            member_result = await session.execute(member_stmt)
            for workspace_id, workspace_name in member_result.tuples().all():
                workspace_map[workspace_id] = workspace_name

        if not org_ids:
            for workspace_id, workspace_name in await _list_direct_membership_rows():
                workspace_map[workspace_id] = workspace_name

        ordered = sorted(
            workspace_map.items(), key=lambda item: (item[1], str(item[0]))
        )
        return [
            {"id": str(workspace_id), "name": name} for workspace_id, name in ordered
        ]


async def resolve_role_for_request(workspace_id: WorkspaceID) -> Role:
    """Resolve caller role for a workspace."""
    email = get_email_from_token()
    return await resolve_role(email, workspace_id)


async def resolve_org_role_for_request() -> Role:
    """Resolve a role with organization context from the current MCP token.

    Mirrors the HTTP API's ``_resolve_org_for_regular_user`` pattern: looks up
    the caller's organization memberships directly and rejects ambiguous
    multi-org cases. Used by org-scoped tools (e.g. registry sync) that should
    not require a workspace selector.
    """
    email = get_email_from_token()
    user = await resolve_user_by_email(email)
    _raise_if_multi_tenant_superuser(user)

    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(OrganizationMembership.organization_id).where(
                OrganizationMembership.user_id == user.id
            )
        )
        org_ids = {row[0] for row in result.all()}

    if not org_ids:
        raise ValueError(f"User {email} has no organization memberships")
    if len(org_ids) > 1:
        raise ValueError(
            "Multiple organizations found for caller. "
            "Org-scoped tools require a single-org token."
        )

    organization_id = next(iter(org_ids))
    role = Role(
        type="user",
        user_id=user.id,
        workspace_id=None,
        organization_id=organization_id,
        service_id="tracecat-mcp",
        is_platform_superuser=user.is_superuser,
    )
    scopes = await compute_effective_scopes(role)
    role = role.model_copy(update={"scopes": scopes})
    ctx_role.set(role)
    return role


async def list_workspaces_for_request() -> list[dict[str, str]]:
    """List workspaces accessible to the current MCP caller."""
    identity = get_token_identity()
    if identity.email is None:
        raise ValueError("Token does not contain an email claim")
    scoped_org_ids = identity.organization_ids or None
    return await list_user_workspaces(
        identity.email,
        organization_ids=scoped_org_ids,
    )


def get_email_from_token() -> str:
    """Extract user email from the current MCP access token."""
    identity = get_token_identity()
    if identity.email is None:
        raise ValueError("Token does not contain an email claim")
    return identity.email
