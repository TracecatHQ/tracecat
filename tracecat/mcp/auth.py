"""MCP server authentication and user resolution."""

from __future__ import annotations

import json
import re
import time
import uuid
from base64 import urlsafe_b64decode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken, AuthProvider
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.dependencies import get_access_token
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthToken,
)
from pydantic import AnyHttpUrl, BaseModel, Field
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracecat import config
from tracecat.auth.credentials import compute_effective_scopes
from tracecat.auth.dex.mode import MCPDexMode, get_mcp_dex_mode
from tracecat.auth.oidc import OIDCProviderConfig, get_mcp_oidc_config
from tracecat.auth.types import Role
from tracecat.auth.users import get_user_db_context, get_user_manager_context
from tracecat.authz.controls import has_scope
from tracecat.authz.enums import OrgRole, WorkspaceRole
from tracecat.config import REDIS_URL, TRACECAT__DB_ENCRYPTION_KEY
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
from tracecat.mcp import config as mcp_config
from tracecat.mcp.consent_page import build_oidc_consent_html


class MCPAuthSource(StrEnum):
    OIDC = "oidc"
    NONE = "none"


class MCPTokenIdentity(BaseModel):
    """Identity extracted from the active MCP access token."""

    client_id: str
    auth_source: MCPAuthSource = MCPAuthSource.OIDC
    email: str | None = None
    organization_ids: frozenset[uuid.UUID] = Field(default_factory=frozenset)
    workspace_ids: frozenset[uuid.UUID] = Field(default_factory=frozenset)
    is_superuser_bypass: bool = False


_UUID_SCOPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "organization": re.compile(
        r"^(?:organization|org|organization_id|org_id):(?P<uuid>[0-9a-fA-F-]{36})$"
    ),
    "workspace": re.compile(r"^(?:workspace|workspace_id):(?P<uuid>[0-9a-fA-F-]{36})$"),
}

_MCP_REFRESH_SCOPE = "offline_access"
_MCP_ACCESS_TOKEN_FALLBACK_EXPIRY_SECONDS = 24 * 60 * 60
_MCP_OAUTH_TRANSACTION_TTL_SECONDS = 15 * 60
_MCP_TOKEN_ENDPOINT_AUTH_METHODS = ["none"]
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MCP_AUTH_SOURCE_CLAIM = "tracecat_mcp_auth_source"
_MCP_BYPASS_CLAIM = "tracecat_mcp_superuser_bypass"
_MCP_NONE_CLIENT_ID = "tracecat-mcp-none"
_MCP_DEX_REDIRECT_PATH = "/_/mcp/auth/callback"
_MCP_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _normalize_auth_source(value: object) -> MCPAuthSource:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            try:
                return MCPAuthSource(normalized)
            except ValueError:
                pass
    return MCPAuthSource.OIDC


def _is_trusted_bypass_identity(
    auth_source: MCPAuthSource,
    client_id: str,
) -> bool:
    return auth_source is MCPAuthSource.NONE and client_id == _MCP_NONE_CLIENT_ID


class _UserinfoFetchError(RuntimeError):
    """Raised when the upstream userinfo endpoint cannot be queried."""


def _build_required_scopes(
    scopes: Sequence[str],
    *,
    requests_refresh_tokens: bool,
) -> list[str]:
    required_scopes = [scope for scope in scopes if scope != _MCP_REFRESH_SCOPE]
    if requests_refresh_tokens:
        return _merge_unique_scopes(required_scopes, (_MCP_REFRESH_SCOPE,))
    return required_scopes


def _merge_unique_scopes(
    scopes: Sequence[str],
    extra_scopes: Sequence[str],
) -> list[str]:
    merged = list(scopes)
    for scope in extra_scopes:
        if scope not in merged:
            merged.append(scope)
    return merged


def _normalize_requested_scopes(
    scopes: Sequence[str],
    *,
    required_scopes: Sequence[str],
    requests_refresh_tokens: bool,
) -> list[str]:
    normalized_scopes = _merge_unique_scopes(scopes, required_scopes)
    if requests_refresh_tokens:
        return normalized_scopes
    return [scope for scope in normalized_scopes if scope != _MCP_REFRESH_SCOPE]


def _rewrite_issuer_url(
    value: str | None,
    *,
    public_issuer: str,
    internal_issuer: str | None,
) -> str | None:
    if value is None or not internal_issuer:
        return value
    text = str(value)
    if not text.startswith(public_issuer):
        return text
    return f"{internal_issuer}{text[len(public_issuer) :]}"


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
    netloc = f"{userinfo}{host}:*"
    return urlunparse(parsed._replace(netloc=netloc))


def _expand_cimd_loopback_redirect_uris(redirect_uris: Sequence[str]) -> list[str]:
    expanded: list[str] = []
    for redirect_uri in redirect_uris:
        if redirect_uri not in expanded:
            expanded.append(redirect_uri)
        if _is_loopback_redirect_uri(redirect_uri) and not _has_explicit_redirect_port(
            redirect_uri
        ):
            wildcard = _add_loopback_port_wildcard(redirect_uri)
            if wildcard not in expanded:
                expanded.append(wildcard)
    return expanded


def _normalize_mcp_client(
    client: OAuthClientInformationFull | None,
    *,
    required_scopes: Sequence[str],
) -> OAuthClientInformationFull | None:
    if client is None:
        return None

    updates: dict[str, object] = {}

    current_scopes = [scope for scope in (client.scope or "").split(" ") if scope]
    normalized_scopes = _merge_unique_scopes(current_scopes, required_scopes)
    normalized_scope_str = " ".join(normalized_scopes)
    if normalized_scope_str != (client.scope or ""):
        updates["scope"] = normalized_scope_str

    if isinstance(client, ProxyDCRClient) and client.cimd_document is not None:
        redirect_uris = client.cimd_document.redirect_uris
        expanded_redirect_uris = _expand_cimd_loopback_redirect_uris(redirect_uris)
        if expanded_redirect_uris != redirect_uris:
            updates["cimd_document"] = client.cimd_document.model_copy(
                update={"redirect_uris": expanded_redirect_uris}
            )

    if not updates:
        return client
    return client.model_copy(update=updates)


def supports_refresh_scope(scopes_supported: Sequence[str] | None) -> bool:
    """Return whether provider metadata supports MCP refresh scope requests."""
    if scopes_supported is None:
        # If provider metadata omits scopes_supported, optimistically request.
        return True
    return _MCP_REFRESH_SCOPE in scopes_supported


@dataclass(frozen=True)
class MCPAuthSettings:
    base_url: str
    public_issuer: str
    internal_issuer: str | None
    required_scopes: tuple[str, ...]
    requests_refresh_tokens: bool
    fallback_access_token_expiry_seconds: int


def _build_mcp_auth_settings(
    *,
    base_url: str,
    oidc_config: OIDCProviderConfig,
) -> MCPAuthSettings:
    requests_refresh_tokens = True
    return MCPAuthSettings(
        base_url=base_url,
        public_issuer=config.DEX_ISSUER or oidc_config.issuer or "",
        internal_issuer=config.DEX_INTERNAL_ISSUER or None,
        required_scopes=tuple(
            _build_required_scopes(
                oidc_config.scopes,
                requests_refresh_tokens=requests_refresh_tokens,
            )
        ),
        requests_refresh_tokens=requests_refresh_tokens,
        fallback_access_token_expiry_seconds=_MCP_ACCESS_TOKEN_FALLBACK_EXPIRY_SECONDS,
    )


class _TracecatOIDCProxy(OIDCProxy):
    """OIDC proxy with user-existence validation and public-client registration."""

    __slots__ = ("_settings",)

    def __init__(
        self,
        *args: Any,
        settings: MCPAuthSettings,
        **kwargs: Any,
    ) -> None:
        object.__setattr__(self, "_settings", settings)
        super().__init__(*args, **kwargs)

    def get_oidc_configuration(
        self,
        config_url: AnyHttpUrl,
        strict: bool | None,
        timeout_seconds: int | None,
    ):
        oidc_metadata = super().get_oidc_configuration(
            config_url, strict, timeout_seconds
        )
        update_fields = {
            field: rewritten
            for field in (
                "token_endpoint",
                "userinfo_endpoint",
                "jwks_uri",
                "registration_endpoint",
                "revocation_endpoint",
                "introspection_endpoint",
                "service_documentation",
            )
            if (
                rewritten := _rewrite_issuer_url(
                    getattr(oidc_metadata, field, None),
                    public_issuer=self._settings.public_issuer,
                    internal_issuer=self._settings.internal_issuer,
                )
            )
            != getattr(oidc_metadata, field, None)
        }
        if not update_fields:
            return oidc_metadata
        return oidc_metadata.model_copy(update=update_fields)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        normalized_client = _normalize_mcp_client(
            client_info,
            required_scopes=self._settings.required_scopes,
        )
        if normalized_client is None:
            return
        await super().register_client(normalized_client)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = await super().get_client(client_id)
        return _normalize_mcp_client(
            client, required_scopes=self._settings.required_scopes
        )

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        scopes = _normalize_requested_scopes(
            list(params.scopes or []),
            required_scopes=self._settings.required_scopes,
            requests_refresh_tokens=self._settings.requests_refresh_tokens,
        )
        params_with_scopes = params.model_copy(update={"scopes": scopes})
        return await super().authorize(client, params_with_scopes)

    async def _retry_without_refresh_scope(
        self,
        *,
        request: Request,
    ) -> RedirectResponse | None:
        if request.query_params.get("error") != "invalid_scope":
            return None
        if not self._settings.requests_refresh_tokens:
            return None

        txn_id = request.query_params.get("state")
        if not txn_id:
            return None

        txn_model = await self._transaction_store.get(key=txn_id)
        if txn_model is None:
            return None

        scopes = list(txn_model.scopes or [])
        if _MCP_REFRESH_SCOPE not in scopes:
            return None

        updated_scopes = [scope for scope in scopes if scope != _MCP_REFRESH_SCOPE]
        updated_txn = txn_model.model_copy(update={"scopes": updated_scopes})

        age_seconds = max(0.0, time.time() - float(txn_model.created_at))
        remaining_ttl = max(1, int(_MCP_OAUTH_TRANSACTION_TTL_SECONDS - age_seconds))
        await self._transaction_store.put(
            key=txn_id,
            value=updated_txn,
            ttl=remaining_ttl,
        )

        logger.warning(
            "OIDC provider rejected refresh scope; retrying authorization without refresh scope",
            scope=_MCP_REFRESH_SCOPE,
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
        try:
            email = await self._resolve_idp_email(idp_tokens)
        except _UserinfoFetchError as exc:
            raise TokenError(
                "invalid_grant",
                "Failed to fetch OIDC userinfo",
            ) from exc
        except Exception as exc:
            raise TokenError(
                "invalid_grant",
                "Failed to resolve OIDC email claims",
            ) from exc

        if email is None:
            raise TokenError(
                "invalid_client",
                "No email claim in id_token or userinfo — cannot resolve Tracecat user",
            )

        try:
            await resolve_user_by_email(email)
        except ValueError as exc:
            detail = str(exc)
            if detail == f"No user found for email: {email}":
                detail = (
                    f"No Tracecat account found for {email}. "
                    "Please sign up or ask an admin to invite you."
                )
            logger.warning(
                "MCP auth rejected",
                email=email,
                reason=detail,
            )
            raise TokenError(
                "invalid_client",
                detail,
            ) from None

        return {"email": email}

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        client_id = client.client_id or authorization_code.client_id
        logger.info(
            "Exchanging MCP authorization code",
            client_id=client_id,
            scope_count=len(authorization_code.scopes),
            requests_refresh_scope=_MCP_REFRESH_SCOPE in authorization_code.scopes,
        )
        try:
            token = await super().exchange_authorization_code(
                client, authorization_code
            )
        except TokenError as exc:
            logger.warning(
                "MCP authorization code exchange failed",
                client_id=client_id,
                error=exc.error,
                error_description=exc.error_description,
            )
            raise

        logger.info(
            "Issued MCP tokens from authorization code exchange",
            client_id=client_id,
            access_token_expires_in=token.expires_in,
            issued_refresh_token=bool(token.refresh_token),
        )
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        client_id = client.client_id or refresh_token.client_id
        logger.info(
            "Attempting MCP refresh grant",
            client_id=client_id,
            scope_count=len(scopes),
            requests_refresh_scope=_MCP_REFRESH_SCOPE in scopes,
        )
        try:
            token = await super().exchange_refresh_token(client, refresh_token, scopes)
        except TokenError as exc:
            logger.warning(
                "MCP refresh grant failed",
                client_id=client_id,
                error=exc.error,
                error_description=exc.error_description,
            )
            raise

        logger.info(
            "MCP refresh grant succeeded",
            client_id=client_id,
            access_token_expires_in=token.expires_in,
            issued_refresh_token=bool(token.refresh_token),
            refresh_token_rotated=bool(
                token.refresh_token and token.refresh_token != refresh_token.token
            ),
        )
        return token

    async def load_access_token(self, token: str) -> AccessToken | None:
        fastmcp_claims: Mapping[str, object] | None = None
        try:
            verified_claims = self.jwt_issuer.verify_token(token)
        except Exception as exc:
            logger.warning(
                "Failed to decode FastMCP token claims during MCP auth",
                error=str(exc),
            )
        else:
            if isinstance(verified_claims, Mapping):
                fastmcp_claims = cast(Mapping[str, object], verified_claims)

        access_token = cast(AccessToken | None, await super().load_access_token(token))
        if access_token is None:
            if fastmcp_claims is not None:
                logger.warning(
                    "MCP access token validation failed after upstream check",
                    client_id=fastmcp_claims.get("client_id"),
                    jti_prefix=(
                        jti[:8]
                        if isinstance((jti := fastmcp_claims.get("jti")), str)
                        else None
                    ),
                    scope_count=len(_extract_fastmcp_scopes(fastmcp_claims) or []),
                )
            return None

        if fastmcp_claims is None:
            return access_token

        merged_claims = _merge_fastmcp_token_claims(
            validated_claims=access_token.claims,
            fastmcp_claims=fastmcp_claims,
        )
        scopes = access_token.scopes
        if (fastmcp_scopes := _extract_fastmcp_scopes(fastmcp_claims)) is not None:
            scopes = fastmcp_scopes
        client_id = access_token.client_id
        if not client_id and isinstance(
            (fastmcp_client_id := fastmcp_claims.get("client_id")), str
        ):
            client_id = fastmcp_client_id.strip()

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

    async def _resolve_idp_email(self, idp_tokens: Mapping[str, object]) -> str | None:
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

        response.body = build_oidc_consent_html(
            client_id=client_id,
            redirect_uri=redirect_uri,
            scopes=[str(scope) for scope in scopes],
            txn_id=txn_id,
            csrf_token=csrf_token,
        ).encode("utf-8")
        response.headers["content-length"] = str(len(response.body))
        return response

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        if self.base_url is None:
            return routes

        for route in routes:
            if not isinstance(route, Route):
                continue

            if route.path.startswith("/.well-known/oauth-authorization-server"):
                route.app = _patch_oauth_metadata_route(route.app)
            elif route.path == "/register":
                route.app = _patch_oauth_register_route(route.app)

        return routes


def _create_mcp_oidc_auth() -> AuthProvider:
    base_url = mcp_config.TRACECAT_MCP__BASE_URL.strip().rstrip("/")
    if not base_url:
        raise ValueError(
            "TRACECAT_MCP__BASE_URL must be configured for the MCP server. "
            "Set it to the public URL where the MCP server is accessible."
        )

    oidc_config = get_mcp_oidc_config()
    if not oidc_config.issuer:
        raise ValueError("DEX_ISSUER must be configured for the MCP server.")
    if not config.DEX_TRACECAT_CLIENT_ID or not config.DEX_TRACECAT_CLIENT_SECRET:
        raise ValueError(
            "DEX_TRACECAT_CLIENT_ID and DEX_TRACECAT_CLIENT_SECRET must be configured for the MCP server."
        )

    settings = _build_mcp_auth_settings(base_url=base_url, oidc_config=oidc_config)
    client_storage = _create_mcp_client_storage()
    config_url = (
        f"{settings.internal_issuer or settings.public_issuer}"
        "/.well-known/openid-configuration"
    )
    auth = _TracecatOIDCProxy(
        config_url=config_url,
        client_id=config.DEX_TRACECAT_CLIENT_ID,
        client_secret=config.DEX_TRACECAT_CLIENT_SECRET,
        base_url=settings.base_url,
        redirect_path=_MCP_DEX_REDIRECT_PATH,
        client_storage=client_storage,
        fallback_access_token_expiry_seconds=(
            settings.fallback_access_token_expiry_seconds
        ),
        settings=settings,
    )
    if auth.client_registration_options is not None:
        auth.client_registration_options.valid_scopes = list(settings.required_scopes)
        auth.client_registration_options.default_scopes = list(settings.required_scopes)
    return auth


def _patch_oauth_metadata_route(app: ASGIApp) -> ASGIApp:
    """Patch discovery responses to advertise public-client auth only."""

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


def _patch_oauth_register_route(app: ASGIApp) -> ASGIApp:
    """Normalize MCP client registrations to public-client auth.

    FastMCP defaults omitted ``token_endpoint_auth_method`` values to
    ``client_secret_post``. Tracecat's MCP proxy uses public clients locally and
    handles the upstream confidential-client exchange itself, so registrations
    must consistently use ``none``.
    """

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
            requested_auth_method = payload.get("token_endpoint_auth_method")
            if requested_auth_method != "none":
                payload["token_endpoint_auth_method"] = "none"
                body = json.dumps(payload).encode("utf-8")
                request_messages = [
                    {
                        "type": "http.request",
                        "body": body,
                        "more_body": False,
                    }
                ]
                logger.info(
                    "Normalized MCP client registration to public auth method",
                    requested_auth_method=requested_auth_method,
                )

        pending_messages = iter(request_messages)

        async def replay_receive() -> Message:
            try:
                return next(pending_messages)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        await app(scope, replay_receive, send)

    return patched_app


def _create_mcp_client_storage() -> PrefixCollectionsWrapper | FernetEncryptionWrapper:
    """Build storage for MCP OAuth state."""
    redis_client = AsyncRedis.from_url(REDIS_URL, decode_responses=True)
    redis_store = RedisStore(client=redis_client)
    prefixed_store = PrefixCollectionsWrapper(redis_store, prefix="mcp")
    if TRACECAT__DB_ENCRYPTION_KEY:
        return FernetEncryptionWrapper(
            prefixed_store, fernet=Fernet(TRACECAT__DB_ENCRYPTION_KEY)
        )

    logger.warning(
        "TRACECAT__DB_ENCRYPTION_KEY is not set; "
        "MCP OAuth state will be stored unencrypted in Redis"
    )
    return prefixed_store


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
    auth_source = _normalize_auth_source(claims.get(_MCP_AUTH_SOURCE_CLAIM))
    is_superuser_bypass = claims.get(_MCP_BYPASS_CLAIM) is True and (
        _is_trusted_bypass_identity(auth_source, client_id)
    )

    return MCPTokenIdentity(
        client_id=client_id,
        auth_source=auth_source,
        email=email,
        organization_ids=frozenset(organization_ids),
        workspace_ids=frozenset(workspace_ids),
        is_superuser_bypass=is_superuser_bypass,
    )


def create_mcp_auth() -> AuthProvider | None:
    """Build the configured auth provider for external MCP."""
    base_url = mcp_config.TRACECAT_MCP__BASE_URL.strip().rstrip("/")
    if not base_url:
        raise ValueError(
            "TRACECAT_MCP__BASE_URL must be configured for the MCP server. "
            "Set it to the public URL where the MCP server is accessible."
        )

    return _create_mcp_oidc_auth()


async def resolve_user_by_email(email: str) -> User:
    """Look up a user by email, raising if not found."""
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(select(User).filter_by(email=email))
        user = result.scalars().first()
        if user is None:
            raise ValueError(f"No user found for email: {email}")
        if get_mcp_dex_mode() is MCPDexMode.BASIC:
            async with get_user_db_context(session) as user_db:
                async with get_user_manager_context(user_db) as user_manager:
                    if not await user_manager.is_local_password_login_allowed(user):
                        raise ValueError(
                            "Local password login is not allowed for this user; "
                            "use your organization SSO flow instead."
                        )
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


async def resolve_bypass_role_for_workspace(workspace_id: WorkspaceID) -> Role:
    """Construct a superuser-equivalent MCP role for unsafe bypass access."""
    org_id = await resolve_workspace_org(workspace_id)
    role = Role(
        type="service",
        user_id=None,
        workspace_id=workspace_id,
        organization_id=org_id,
        service_id="tracecat-mcp",
        is_platform_superuser=True,
        scopes=frozenset({"*"}),
    )
    ctx_role.set(role)
    return role


async def list_all_workspaces(
    organization_ids: frozenset[OrganizationID] | None = None,
) -> list[dict[str, str]]:
    """List all workspaces, optionally narrowed to specific organizations."""
    async with get_async_session_bypass_rls_context_manager() as session:
        stmt = select(Workspace.id, Workspace.name)
        if organization_ids:
            stmt = stmt.where(Workspace.organization_id.in_(organization_ids))
        stmt = stmt.order_by(Workspace.name.asc(), Workspace.id.asc())
        result = await session.execute(stmt)
        return [
            {"id": str(workspace_id), "name": workspace_name}
            for workspace_id, workspace_name in result.tuples().all()
        ]


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


async def resolve_role(email: str, workspace_id: WorkspaceID) -> Role:
    """Resolve a user's Role for a given workspace from their OAuth email.

    Pipeline: email -> User -> Workspace.organization_id -> scopes/membership -> Role

    Org admins/owners (users with ``org:workspace:read`` scope) bypass the
    workspace-level membership check, matching the behaviour of the main API.
    Platform superusers also bypass direct membership checks.
    """
    user = await resolve_user_by_email(email)
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
    superusers see every workspace in their organization(s).  Other users see
    only workspaces where they have an explicit Membership row.
    """
    user = await resolve_user_by_email(email)

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
    identity = get_token_identity()
    if identity.is_superuser_bypass:
        return await resolve_bypass_role_for_workspace(workspace_id)
    email = get_email_from_token()
    return await resolve_role(email, workspace_id)


async def list_workspaces_for_request() -> list[dict[str, str]]:
    """List workspaces accessible to the current MCP caller."""
    identity = get_token_identity()
    if identity.is_superuser_bypass:
        return await list_all_workspaces(identity.organization_ids or None)
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
