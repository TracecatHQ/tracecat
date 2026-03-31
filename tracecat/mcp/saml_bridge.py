"""Tracecat-native SAML bridge for MCP OAuth."""

from __future__ import annotations

import html
import json
import secrets
import time
from collections.abc import Mapping, Sequence
from typing import cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import Request, status
from fastmcp.server.auth import AccessToken, OAuthProvider
from fastmcp.server.auth.jwt_issuer import JWTIssuer, derive_jwt_key
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import select
from starlette.datastructures import MutableHeaders
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracecat import config
from tracecat.api.common import get_default_organization_id
from tracecat.auth.discovery import (
    AuthDiscoverResponse,
    AuthDiscoveryMethod,
    AuthDiscoveryService,
)
from tracecat.auth.saml import start_saml_login
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Organization
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.mcp.saml_bridge_state import (
    MCP_SAML_IDENTIFY_PATH,
    MCP_SAML_START_PATH,
    SAMLAuthorizationCode,
    SAMLMCPAuthTransaction,
    create_saml_bridge_stores,
    delete_saml_bridge_session,
)

_DEFAULT_SCOPES = ("openid", "profile", "email")
_MCP_AUTH_SOURCE_CLAIM = "tracecat_mcp_auth_source"
_MCP_AUTH_SOURCE_VALUE = "saml_bridge"
_MCP_OAUTH_TRANSACTION_TTL_SECONDS = 15 * 60
_MCP_TOKEN_ENDPOINT_AUTH_METHODS = ["none"]
_MCP_REFRESH_SCOPE = "offline_access"
_MCP_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _merge_unique_scopes(
    scopes: Sequence[str],
    extra_scopes: Sequence[str],
) -> list[str]:
    merged = list(scopes)
    for scope in extra_scopes:
        if scope not in merged:
            merged.append(scope)
    return merged


def _is_loopback_redirect_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    return parsed.scheme == "http" and parsed.hostname in _MCP_LOOPBACK_HOSTS


def _has_explicit_redirect_port(uri: str) -> bool:
    parsed = urlparse(uri)
    netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    if netloc.startswith("["):
        return "]:" in netloc
    return ":" in netloc


def _add_loopback_port_wildcard(uri: str) -> str:
    parsed = urlparse(uri)
    hostname = parsed.hostname
    if hostname is None:
        return uri

    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo = f"{userinfo}:{parsed.password}"
        userinfo = f"{userinfo}@"

    host = (
        f"[{hostname}]"
        if ":" in hostname and not hostname.startswith("[")
        else hostname
    )
    return urlunparse(parsed._replace(netloc=f"{userinfo}{host}:*"))


def _extract_fastmcp_scopes(fastmcp_claims: Mapping[str, object]) -> list[str] | None:
    raw_scope = fastmcp_claims.get("scope")
    if isinstance(raw_scope, str):
        return [scope for scope in raw_scope.split() if scope]
    if isinstance(raw_scope, list) and all(
        isinstance(scope, str) for scope in raw_scope
    ):
        return [scope for scope in raw_scope if scope]
    return None


def _strip_refresh_scope(scope_value: str | None) -> str | None:
    if not scope_value:
        return None
    scopes = [scope for scope in scope_value.split() if scope != _MCP_REFRESH_SCOPE]
    return " ".join(scopes) or None


def _patch_oauth_metadata_route(app: ASGIApp) -> ASGIApp:
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
            if isinstance(payload.get("scopes_supported"), list):
                payload["scopes_supported"] = [
                    scope
                    for scope in payload["scopes_supported"]
                    if scope != _MCP_REFRESH_SCOPE
                ]
            if isinstance(payload.get("grant_types_supported"), list):
                payload["grant_types_supported"] = [
                    grant_type
                    for grant_type in payload["grant_types_supported"]
                    if grant_type != "refresh_token"
                ]
            body = json.dumps(payload).encode("utf-8")

        headers = MutableHeaders(raw=start_message["headers"])
        headers["content-length"] = str(len(body))
        await send(start_message)
        await send({"type": "http.response.body", "body": body, "more_body": False})

    return patched_app


def _patch_oauth_register_route(app: ASGIApp) -> ASGIApp:
    async def patched_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        request_messages: list[Message] = []
        body_chunks: list[bytes] = []
        while True:
            message = await receive()
            request_messages.append(message)
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body_chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        body = b"".join(body_chunks)
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            if payload.get("token_endpoint_auth_method") != "none":
                payload["token_endpoint_auth_method"] = "none"
            if isinstance(payload.get("scope"), str):
                if stripped_scope := _strip_refresh_scope(payload["scope"]):
                    payload["scope"] = stripped_scope
                else:
                    payload.pop("scope", None)
            body = json.dumps(payload).encode("utf-8")
            request_messages = [
                {"type": "http.request", "body": body, "more_body": False}
            ]

        pending_messages = iter(request_messages)

        start_message: Message | None = None
        response_body_chunks: list[bytes] = []

        async def replay_receive() -> Message:
            try:
                return next(pending_messages)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        async def capture(message: Message) -> None:
            nonlocal start_message
            match message["type"]:
                case "http.response.start":
                    start_message = dict(message)
                case "http.response.body":
                    response_body_chunks.append(message.get("body", b""))
                case _:
                    await send(message)

        await app(scope, replay_receive, capture)
        if start_message is None:
            return

        response_body = b"".join(response_body_chunks)
        try:
            response_payload = json.loads(response_body)
        except json.JSONDecodeError:
            response_payload = None
        if isinstance(response_payload, dict):
            if isinstance(response_payload.get("scope"), str):
                response_payload["scope"] = _strip_refresh_scope(
                    response_payload["scope"]
                )
            if isinstance(response_payload.get("grant_types"), list):
                response_payload["grant_types"] = [
                    grant_type
                    for grant_type in response_payload["grant_types"]
                    if grant_type != "refresh_token"
                ]
            response_body = json.dumps(response_payload).encode("utf-8")

        headers = MutableHeaders(raw=start_message["headers"])
        headers["content-length"] = str(len(response_body))
        await send(start_message)
        await send(
            {
                "type": "http.response.body",
                "body": response_body,
                "more_body": False,
            }
        )

    return patched_app


def _patch_oauth_authorize_route(app: ASGIApp) -> ASGIApp:
    async def patched_app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        patched_scope = dict(scope)
        if scope["method"] == "GET":
            params = parse_qsl(
                scope.get("query_string", b"").decode("utf-8"),
                keep_blank_values=True,
            )
            rewritten_params: list[tuple[str, str]] = []
            for key, value in params:
                if key == "scope":
                    if stripped_scope := _strip_refresh_scope(value):
                        rewritten_params.append((key, stripped_scope))
                else:
                    rewritten_params.append((key, value))
            patched_scope["query_string"] = urlencode(rewritten_params).encode("utf-8")
            await app(patched_scope, receive, send)
            return

        request_messages: list[Message] = []
        body_chunks: list[bytes] = []
        while True:
            message = await receive()
            request_messages.append(message)
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body_chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break

        form_pairs = parse_qsl(
            b"".join(body_chunks).decode("utf-8"), keep_blank_values=True
        )
        rewritten_pairs: list[tuple[str, str]] = []
        for key, value in form_pairs:
            if key == "scope":
                if stripped_scope := _strip_refresh_scope(value):
                    rewritten_pairs.append((key, stripped_scope))
            else:
                rewritten_pairs.append((key, value))
        body = urlencode(rewritten_pairs).encode("utf-8")
        pending_messages = iter(
            [{"type": "http.request", "body": body, "more_body": False}]
        )

        async def replay_receive() -> Message:
            try:
                return next(pending_messages)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        await app(patched_scope, replay_receive, send)

    return patched_app


def _normalize_saml_mcp_client(
    client: OAuthClientInformationFull,
    *,
    required_scopes: Sequence[str],
) -> ProxyDCRClient:
    current_scopes = [scope for scope in (client.scope or "").split(" ") if scope]
    normalized_scope = " ".join(_merge_unique_scopes(current_scopes, required_scopes))

    redirect_uris = [str(uri) for uri in (client.redirect_uris or [])]
    expanded_redirect_uris: list[str] = []
    for redirect_uri in redirect_uris:
        if redirect_uri not in expanded_redirect_uris:
            expanded_redirect_uris.append(redirect_uri)
        if _is_loopback_redirect_uri(redirect_uri) and not _has_explicit_redirect_port(
            redirect_uri
        ):
            wildcard = _add_loopback_port_wildcard(redirect_uri)
            if wildcard not in expanded_redirect_uris:
                expanded_redirect_uris.append(wildcard)

    return ProxyDCRClient.model_validate(
        client.model_dump(
            mode="json",
            exclude_none=True,
        )
        | {
            "redirect_uris": redirect_uris,
            "allowed_redirect_uri_patterns": expanded_redirect_uris,
            "scope": normalized_scope,
            "token_endpoint_auth_method": "none",
        }
    )


def _build_identify_html(txn_id: str, email: str | None = None) -> str:
    safe_txn_id = html.escape(txn_id, quote=True)
    safe_email = html.escape(email or "", quote=True)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Tracecat MCP sign in</title>
    <style>
      body {{ font-family: ui-sans-serif, system-ui, sans-serif; background: #f5f5f4; color: #1c1917; }}
      .shell {{ max-width: 420px; margin: 64px auto; padding: 32px; background: white; border: 1px solid #e7e5e4; border-radius: 16px; }}
      h1 {{ margin: 0 0 8px; font-size: 28px; }}
      p {{ margin: 0 0 24px; color: #57534e; }}
      label {{ display: block; margin-bottom: 8px; font-size: 14px; font-weight: 600; }}
      input {{ width: 100%; box-sizing: border-box; border: 1px solid #d6d3d1; border-radius: 10px; padding: 12px; margin-bottom: 16px; font: inherit; }}
      button {{ width: 100%; border: 0; border-radius: 10px; padding: 12px; background: #1c1917; color: white; font: inherit; cursor: pointer; }}
    </style>
  </head>
  <body>
    <div class="shell">
      <h1>Sign in to Tracecat MCP</h1>
      <p>Enter your work email to route into your organization SAML login.</p>
      <form method="post" action="{MCP_SAML_IDENTIFY_PATH}">
        <input type="hidden" name="txn_id" value="{safe_txn_id}">
        <label for="email">Work email</label>
        <input id="email" name="email" type="email" autocomplete="email" required value="{safe_email}">
        <label for="org">Organization slug (optional)</label>
        <input id="org" name="org" type="text" autocomplete="organization">
        <button type="submit">Continue</button>
      </form>
    </div>
  </body>
</html>"""


def _build_error_html(message: str, *, status_code: int = 400) -> HTMLResponse:
    escaped_message = html.escape(message, quote=True)
    return HTMLResponse(
        content=f"""<!doctype html>
<html lang="en"><body style="font-family: ui-sans-serif, system-ui, sans-serif; margin: 48px; color: #1c1917;">
<h1 style="font-size: 28px; margin-bottom: 12px;">Tracecat MCP auth</h1>
<p style="font-size: 16px; color: #57534e;">{escaped_message}</p>
</body></html>""",
        status_code=status_code,
    )


class TracecatSAMLBridgeAuthProvider(OAuthProvider):
    """Minimal OAuth server for MCP auth backed by Tracecat SAML login."""

    def __init__(self, *, base_url: str) -> None:
        if not config.TRACECAT__DB_ENCRYPTION_KEY:
            raise ValueError(
                "TRACECAT__DB_ENCRYPTION_KEY must be configured for MCP SAML bridge auth"
            )
        required_scopes = list(config.OIDC_SCOPES or _DEFAULT_SCOPES)
        super().__init__(
            base_url=base_url,
            issuer_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=required_scopes,
                default_scopes=required_scopes,
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=required_scopes,
        )
        self._stores = create_saml_bridge_stores()
        self._jwt_signing_key = derive_jwt_key(
            low_entropy_material=config.TRACECAT__DB_ENCRYPTION_KEY,
            salt="tracecat-mcp-saml-bridge-jwt",
        )
        self._jwt_issuer: JWTIssuer | None = None

    def set_mcp_path(self, mcp_path: str | None) -> None:
        super().set_mcp_path(mcp_path)
        if self._resource_url is None:
            raise RuntimeError("MCP resource URL is required for SAML bridge auth")
        self._jwt_issuer = JWTIssuer(
            issuer=str(self.base_url),
            audience=str(self._resource_url),
            signing_key=self._jwt_signing_key,
        )

    @property
    def jwt_issuer(self) -> JWTIssuer:
        if self._jwt_issuer is None:
            raise RuntimeError("JWT issuer not initialized")
        return self._jwt_issuer

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client_dict = await self._stores.clients.get(client_id)
        if client_dict is None:
            return None
        return _normalize_saml_mcp_client(
            OAuthClientInformationFull.model_validate(client_dict),
            required_scopes=self.required_scopes,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        normalized_client = _normalize_saml_mcp_client(
            client_info,
            required_scopes=self.required_scopes,
        )
        await self._stores.clients.put(
            normalized_client.client_id or secrets.token_urlsafe(16),
            normalized_client.model_dump(mode="json", exclude_none=True),
        )

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        client_id = client.client_id
        if not client_id:
            raise AuthorizeError("invalid_request", "Client ID is required")

        txn_id = secrets.token_urlsafe(32)
        now = time.time()
        requested_scopes = _merge_unique_scopes(
            list(params.scopes or []),
            self.required_scopes,
        )
        issued_scopes = [
            scope for scope in requested_scopes if scope != _MCP_REFRESH_SCOPE
        ]
        transaction = SAMLMCPAuthTransaction(
            id=txn_id,
            client_id=client_id,
            client_redirect_uri=AnyUrl(str(params.redirect_uri)),
            client_state=params.state,
            code_challenge=params.code_challenge,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=issued_scopes,
            resource=params.resource,
            created_at=now,
            expires_at=now + _MCP_OAUTH_TRANSACTION_TTL_SECONDS,
        )
        await self._stores.transactions.put(
            txn_id,
            transaction,
            ttl=_MCP_OAUTH_TRANSACTION_TTL_SECONDS,
        )
        return (
            f"{str(self.base_url).rstrip('/')}{MCP_SAML_IDENTIFY_PATH}?txn_id={txn_id}"
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> SAMLAuthorizationCode | None:
        code = await self._stores.codes.get(authorization_code)
        if code is None:
            return None
        if client.client_id and code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        if not isinstance(authorization_code, SAMLAuthorizationCode):
            raise TokenError("invalid_grant", "authorization code is invalid")
        session = await self._stores.sessions.get(authorization_code.session_id)
        if session is None:
            raise TokenError(
                "invalid_grant", "authorization session is no longer valid"
            )
        if client.client_id is None or client.client_id != session.client_id:
            raise TokenError("invalid_client", "client_id mismatch")

        expires_in = max(1, int(session.expires_at - time.time()))
        upstream_claims = {
            "email": session.user_email,
            "organization_id": str(session.organization_id),
            "organization_ids": [str(session.organization_id)],
            "mcp_session_id": session.id,
            _MCP_AUTH_SOURCE_CLAIM: _MCP_AUTH_SOURCE_VALUE,
        }
        access_token = self.jwt_issuer.issue_access_token(
            client_id=session.client_id,
            scopes=authorization_code.scopes,
            jti=session.jti,
            expires_in=expires_in,
            upstream_claims=upstream_claims,
        )
        await self._stores.codes.delete(authorization_code.code)
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=None,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        raise TokenError(
            "invalid_grant",
            "refresh tokens are not supported for MCP SAML bridge sessions",
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        try:
            payload = self.jwt_issuer.verify_token(token)
        except Exception:
            return None

        claims = dict(payload)
        if isinstance((upstream_claims := claims.get("upstream_claims")), Mapping):
            for key, value in cast(Mapping[str, object], upstream_claims).items():
                claims.setdefault(key, value)

        session_id = claims.get("mcp_session_id")
        if not isinstance(session_id, str):
            return None
        session = await self._stores.sessions.get(session_id)
        if session is None or claims.get("jti") != session.jti:
            return None

        scopes = _extract_fastmcp_scopes(claims) or []
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or session.client_id),
            scopes=scopes,
            expires_at=int(claims["exp"])
            if isinstance(claims.get("exp"), int)
            else None,
            resource=claims.get("resource")
            if isinstance(claims.get("resource"), str)
            else None,
            claims=claims,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            session_id = token.claims.get("mcp_session_id")
            if isinstance(session_id, str):
                await delete_saml_bridge_session(self._stores, session_id)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        for route in routes:
            if not isinstance(route, Route):
                continue
            if route.path.startswith("/.well-known/oauth-authorization-server"):
                route.app = _patch_oauth_metadata_route(route.app)
            elif route.path == "/authorize":
                route.app = _patch_oauth_authorize_route(route.app)
            elif route.path == "/register":
                route.app = _patch_oauth_register_route(route.app)
        routes.extend(
            [
                Route(
                    MCP_SAML_IDENTIFY_PATH,
                    endpoint=self._identify,
                    methods=["GET", "POST"],
                ),
                Route(MCP_SAML_START_PATH, endpoint=self._start_saml, methods=["GET"]),
            ]
        )
        return routes

    async def _identify(self, request: Request) -> HTMLResponse | RedirectResponse:
        if request.method == "GET":
            txn_id = request.query_params.get("txn_id")
            if not txn_id:
                return _build_error_html("Missing MCP authorization transaction.")
            return HTMLResponse(_build_identify_html(txn_id))

        form = await request.form()
        txn_id = form.get("txn_id")
        email = form.get("email")
        org = form.get("org")
        org_slug = org.strip() if isinstance(org, str) and org.strip() else None
        if not isinstance(txn_id, str) or not txn_id:
            return _build_error_html("Missing MCP authorization transaction.")
        if not isinstance(email, str) or not email.strip():
            return _build_error_html("A work email is required.")

        transaction = await self._stores.transactions.get(txn_id)
        if transaction is None or transaction.expires_at <= time.time():
            return _build_error_html(
                "Authorization transaction expired.", status_code=400
            )

        async with get_async_session_bypass_rls_context_manager() as session:
            discovery = AuthDiscoveryService(session)
            try:
                result = await discovery.discover(email.strip(), org_slug=org_slug)
            except Exception:
                logger.exception(
                    "Failed to resolve MCP SAML discovery", email=email, org=org
                )
                return _build_error_html(
                    "Unable to resolve your organization.", status_code=400
                )

            if result.method is not AuthDiscoveryMethod.SAML:
                return _build_error_html(
                    f"This MCP deployment only supports SAML. Resolved auth mode was '{result.method}'.",
                    status_code=status.HTTP_409_CONFLICT,
                )

            organization_id = await _resolve_saml_organization_id(session, result)
            if organization_id is None:
                return _build_error_html(
                    "Could not resolve a SAML-enabled organization for this login.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        remaining_ttl = max(1, int(transaction.expires_at - time.time()))
        await self._stores.transactions.put(
            txn_id,
            transaction.model_copy(
                update={
                    "email": email.strip(),
                    "organization_id": organization_id,
                    "organization_slug": result.organization_slug,
                }
            ),
            ttl=remaining_ttl,
        )
        return RedirectResponse(
            url=f"{str(self.base_url).rstrip('/')}{MCP_SAML_START_PATH}?txn_id={txn_id}",
            status_code=302,
        )

    async def _start_saml(self, request: Request) -> RedirectResponse | HTMLResponse:
        txn_id = request.query_params.get("txn_id")
        if not txn_id:
            return _build_error_html("Missing MCP authorization transaction.")

        transaction = await self._stores.transactions.get(txn_id)
        if transaction is None or transaction.organization_id is None:
            return _build_error_html(
                "Authorization transaction is not ready for SAML login."
            )

        async with get_async_session_bypass_rls_context_manager() as session:
            response = await start_saml_login(
                session,
                transaction.organization_id,
                mcp_transaction_id=txn_id,
            )
        return RedirectResponse(url=response.redirect_url, status_code=302)


async def _resolve_saml_organization_id(
    session,
    discovery: AuthDiscoverResponse,
) -> OrganizationID | None:
    if discovery.organization_slug:
        stmt = select(Organization.id).where(
            Organization.slug == discovery.organization_slug,
            Organization.is_active.is_(True),
        )
        return (await session.execute(stmt)).scalar_one_or_none()
    if not config.TRACECAT__EE_MULTI_TENANT:
        return await get_default_organization_id(session)
    return None
