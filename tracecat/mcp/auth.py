"""MCP server authentication and user resolution."""

from __future__ import annotations

import html
import json
import re
import uuid
from base64 import urlsafe_b64decode
from typing import Any

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import get_access_token
from mcp.server.auth.provider import TokenError
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from tracecat.auth.credentials import compute_effective_scopes
from tracecat.auth.oidc import get_platform_oidc_config
from tracecat.auth.types import Role
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import (
    Membership,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.mcp.config import (
    TRACECAT_MCP__BASE_URL,
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


def get_token_identity() -> MCPTokenIdentity:
    """Extract normalized caller identity from the current access token."""
    access_token = get_access_token()
    if access_token is None:
        raise ValueError("Authentication required")

    claims = access_token.claims
    raw_email = claims.get("email")
    email = raw_email.strip() if isinstance(raw_email, str) else None
    client_id_claim = (
        claims.get("client_id")
        or claims.get("azp")
        or claims.get("sub")
        or access_token.client_id
    )
    client_id = str(client_id_claim).strip() if client_id_claim else ""
    if not client_id:
        client_id = access_token.client_id

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


def create_mcp_auth() -> AuthProvider:
    """Build auth provider for external MCP."""
    base_url = TRACECAT_MCP__BASE_URL.strip().rstrip("/")
    if not base_url:
        raise ValueError(
            "TRACECAT_MCP__BASE_URL must be configured for the MCP server. "
            "Set it to the public URL where the MCP server is accessible."
        )

    oidc_config = get_platform_oidc_config()
    if not oidc_config.issuer:
        raise ValueError("OIDC_ISSUER must be configured for the MCP server.")

    class TracecatOIDCProxy(OIDCProxy):
        """OIDC proxy with user-existence validation and a custom consent page."""

        async def _extract_upstream_claims(
            self, idp_tokens: dict[str, Any]
        ) -> dict[str, Any] | None:
            """Validate the authenticated user exists in Tracecat before issuing a session token."""
            id_token = idp_tokens.get("id_token")
            if not id_token:
                raise TokenError(
                    "invalid_grant",
                    "OIDC provider did not return an id_token",
                )

            # Decode the JWT payload without verification (already
            # validated by the upstream exchange).
            try:
                payload_b64 = id_token.split(".")[1]
                # Pad base64
                padded = payload_b64 + "=" * (-len(payload_b64) % 4)
                claims = json.loads(urlsafe_b64decode(padded))
            except Exception as exc:
                raise TokenError(
                    "invalid_grant",
                    "Failed to decode id_token claims",
                ) from exc

            email = claims.get("email")
            if not email:
                raise TokenError(
                    "invalid_client",
                    "No email claim in id_token — cannot resolve Tracecat user",
                )

            # Check the user exists in the platform DB
            try:
                await resolve_user_by_email(email)
            except ValueError:
                logger.warning(
                    "MCP auth rejected: no Tracecat user for email",
                    email=email,
                )
                raise TokenError(
                    "invalid_client",
                    f"No Tracecat account found for {email}. "
                    "Please sign up or ask an admin to invite you.",
                ) from None

            return {"email": email}

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

    config_url = f"{oidc_config.issuer}/.well-known/openid-configuration"
    return TracecatOIDCProxy(
        config_url=config_url,
        client_id=oidc_config.client_id,
        client_secret=oidc_config.client_secret,
        base_url=base_url,
    )


async def resolve_user_by_email(email: str) -> User:
    """Look up a user by email, raising if not found."""
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(User).where(User.email == email)  # pyright: ignore[reportArgumentType]
        )
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
    async with get_async_session_context_manager() as session:
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
    async with get_async_session_context_manager() as session:
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
    async with get_async_session_context_manager() as session:
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


async def resolve_role(email: str, workspace_id: WorkspaceID) -> Role:
    """Resolve a user's Role for a given workspace from their OAuth email.

    Pipeline: email -> User -> Workspace.organization_id -> OrganizationMembership -> Membership -> Role

    The workspace's owning organization is resolved first, then the user's
    membership in *that* organization is checked. This prevents an admin in
    org A from gaining access to a workspace belonging to org B.
    """
    user = await resolve_user_by_email(email)
    org_id = await resolve_workspace_org(workspace_id)
    # Validate the user belongs to the organization (raises on missing membership)
    await resolve_org_membership(user.id, org_id)
    # Validate workspace-level access
    await resolve_workspace_membership(user.id, workspace_id)

    role = Role(
        type="user",
        user_id=user.id,
        workspace_id=workspace_id,
        organization_id=org_id,
        service_id="tracecat-mcp",
        is_platform_superuser=user.is_superuser,
    )
    scopes = await compute_effective_scopes(role)
    role = role.model_copy(update={"scopes": scopes})
    # Set context variable so downstream services that rely on ctx_role
    # (e.g. SecretsService.with_session()) can resolve the role automatically.
    ctx_role.set(role)
    return role


async def list_user_workspaces(
    email: str,
    organization_id: OrganizationID | None = None,
) -> list[dict[str, str]]:
    """List workspaces the user has explicit Membership rows for.

    Only returns workspaces where the user is a direct member.
    """
    user = await resolve_user_by_email(email)
    async with get_async_session_context_manager() as session:
        stmt = (
            select(Workspace.id, Workspace.name)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id)
            .order_by(Workspace.name.asc(), Workspace.id.asc())
        )
        if organization_id is not None:
            stmt = stmt.where(Workspace.organization_id == organization_id)

        result = await session.execute(stmt)
        return [{"id": str(row.id), "name": row.name} for row in result.all()]


async def resolve_role_for_request(workspace_id: WorkspaceID) -> Role:
    """Resolve caller role for a workspace."""
    email = get_email_from_token()
    return await resolve_role(email, workspace_id)


async def list_workspaces_for_request() -> list[dict[str, str]]:
    """List workspaces accessible to the current MCP caller."""
    email = get_email_from_token()
    return await list_user_workspaces(email)


def get_email_from_token() -> str:
    """Extract user email from the current MCP access token."""
    identity = get_token_identity()
    if identity.email is None:
        raise ValueError("Token does not contain an email claim")
    return identity.email
