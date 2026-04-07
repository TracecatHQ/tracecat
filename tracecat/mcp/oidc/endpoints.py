"""Internal OIDC issuer endpoints for MCP authentication.

Implements the authorization-code + PKCE flow that FastMCP's ``OIDCProxy``
uses as its upstream OIDC provider, authenticating users via existing
Tracecat session cookies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Annotated, Any
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Depends, Form, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse, Response

from tracecat.auth.users import optional_current_active_user
from tracecat.config import TRACECAT__PUBLIC_APP_URL
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.db.models import User
from tracecat.logger import logger
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc.refresh_tokens import (
    RefreshTokenError,
    consume_refresh_token,
    issue_refresh_token,
)
from tracecat.mcp.oidc.schemas import (
    AuthCodeData,
    RefreshTokenMetadata,
    ResumeTransaction,
)
from tracecat.mcp.oidc.session import (
    NeedsAction,
    SessionNeedsAction,
    SessionResult,
    resolve_authorize_session,
)
from tracecat.mcp.oidc.signing import get_public_jwk, mint_jwt, verify_jwt
from tracecat.mcp.oidc.storage import (
    check_token_rate_limit,
    load_and_delete_auth_code,
    load_and_delete_resume_transaction,
    store_auth_code,
    store_jti,
    store_resume_transaction,
)

_OFFLINE_ACCESS_SCOPE = "offline_access"

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OptionalUserDep = Annotated[User | None, Depends(optional_current_active_user)]


def _get_client_ip(request: Request) -> str:
    """Return the client IP, preferring forwarded headers from the reverse proxy."""
    if forwarded_for := request.headers.get("X-Forwarded-For"):
        return forwarded_for.split(",")[0].strip()
    if real_ip := request.headers.get("X-Real-IP"):
        return real_ip
    return request.client.host if request.client else "unknown"


def _hash_ip(request: Request) -> str:
    """SHA-256 hex digest of the client IP for binding."""
    return hashlib.sha256(_get_client_ip(request).encode()).hexdigest()


def _allowed_redirect_uri() -> str:
    """Return the single allowed redirect URI for the internal client.

    The FastMCP proxy's callback is ``{PUBLIC_APP_URL}/auth/callback``.
    """
    return f"{TRACECAT__PUBLIC_APP_URL.rstrip('/')}/auth/callback"


def _error_response(
    error: str,
    description: str,
    *,
    status_code: int = 400,
) -> JSONResponse:
    """Return a standard OAuth error JSON response."""
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


def _validate_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Validate PKCE S256: base64url(sha256(verifier)) == challenge.

    Returns False for non-ASCII verifiers (RFC 7636 §4.1 restricts
    code_verifier to ``[A-Z] / [a-z] / [0-9] / "-" / "." / "_" / "~"``).
    """
    try:
        verifier_bytes = code_verifier.encode("ascii")
    except UnicodeEncodeError:
        return False
    digest = hashlib.sha256(verifier_bytes).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, code_challenge)


def _parse_basic_auth(authorization: str) -> tuple[str, str] | None:
    """Parse HTTP Basic auth header, returning (client_id, client_secret) or None."""
    if not authorization.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        client_id, _, client_secret = decoded.partition(":")
        if client_id and client_secret:
            return client_id, client_secret
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Discovery endpoints
# ---------------------------------------------------------------------------


@router.get("/.well-known/openid-configuration")
async def openid_configuration(request: Request) -> dict[str, Any]:
    """OpenID Connect discovery document.

    The ``token_endpoint`` and ``userinfo_endpoint`` use the request's own
    origin so that server-to-server callers (the MCP proxy) can reach them
    without hairpin NAT issues, while browser-initiated flows use the same
    origin they arrived on.
    """
    issuer = oidc_config.get_issuer_url()
    # Build a base URL from the incoming request so the token endpoint is
    # reachable by whoever fetched this discovery document.
    # When the request arrived through a path-stripping reverse proxy (e.g.
    # Caddy's ``handle_path /api*``), include the app's root_path so the
    # returned endpoint URLs are reachable via that same proxy.
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        scheme = request.headers.get("x-forwarded-proto", "https")
        root_path = request.scope.get("root_path", "")
        request_base = f"{scheme}://{forwarded_host}{root_path}"
    else:
        request_base = f"{request.base_url.scheme}://{request.base_url.netloc}"
    request_issuer = f"{request_base}{oidc_config.ISSUER_PATH_PREFIX}"
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{request_issuer}/token",
        "userinfo_endpoint": f"{request_issuer}/userinfo",
        "jwks_uri": f"{request_issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["ES256"],
        "scopes_supported": ["openid", "profile", "email", "offline_access"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
        ],
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "claims_supported": [
            "sub",
            "email",
            "email_verified",
            "name",
            "organization_id",
        ],
    }


@router.get("/.well-known/jwks.json")
async def jwks() -> dict[str, list[dict[str, str]]]:
    """JSON Web Key Set containing the issuer's ECDSA P-256 (ES256) public key."""
    return {"keys": [get_public_jwk()]}


# ---------------------------------------------------------------------------
# Authorize
# ---------------------------------------------------------------------------


def _default_resource() -> str:
    """Return the default OAuth resource identifier (the MCP endpoint URL)."""
    return f"{TRACECAT__PUBLIC_APP_URL.rstrip('/')}/mcp"


async def _handle_authorize(
    request: Request,
    user: User | None,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    state: str,
    resource: str | None,
    nonce: str | None,
) -> Response:
    """Core authorization logic shared between /authorize and /authorize/resume."""
    # --- Parameter validation ---
    if response_type != "code":
        return _error_response("unsupported_response_type", "Only 'code' is supported")
    if client_id != oidc_config.INTERNAL_CLIENT_ID:
        return _error_response("invalid_client", "Unknown client_id")
    if redirect_uri != _allowed_redirect_uri():
        return _error_response(
            "invalid_request",
            "redirect_uri does not match the registered callback",
        )
    if code_challenge_method != "S256":
        return _error_response(
            "invalid_request",
            "Only code_challenge_method=S256 is supported",
        )
    if not code_challenge:
        return _error_response("invalid_request", "code_challenge is required")

    # Default resource to the MCP endpoint URL when not provided
    # (FastMCP's OIDCProxy does not forward the RFC 8707 resource parameter).
    if not resource:
        resource = _default_resource()

    ip_hash = _hash_ip(request)

    # --- Session resolution ---
    try:
        session_result = await resolve_authorize_session(request, user)
    except ValueError as exc:
        # Regular users with zero or multiple org memberships land here.
        logger.warning(
            "MCP OIDC: session resolution failed",
            error=str(exc),
            client_ip=_get_client_ip(request),
        )
        return _error_response(
            "access_denied",
            "User cannot be resolved to a single organization",
            status_code=403,
        )

    if isinstance(session_result, SessionNeedsAction):
        # Store authorize params for replay after login/org-selection.
        txn_id = secrets.token_urlsafe(32)
        txn = ResumeTransaction(
            transaction_id=txn_id,
            authorize_params={
                "response_type": response_type,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "scope": scope,
                "state": state,
                "resource": resource,
                **({"nonce": nonce} if nonce else {}),
            },
            created_at=time.time(),
            bound_ip=ip_hash,
        )
        await store_resume_transaction(txn)

        frontend_base = TRACECAT__PUBLIC_APP_URL.rstrip("/")
        match session_result.action:
            case NeedsAction.LOGIN:
                logger.info(
                    "MCP OIDC: no session, redirecting to login",
                    txn_id=txn_id,
                )
                return RedirectResponse(
                    f"{frontend_base}/oauth/mcp/continue?txn={txn_id}",
                    status_code=302,
                )
            case NeedsAction.ORG_SELECTION:
                logger.info(
                    "MCP OIDC: superuser needs org selection",
                    txn_id=txn_id,
                )
                return RedirectResponse(
                    f"{frontend_base}/oauth/mcp/select-org?txn={txn_id}",
                    status_code=302,
                )

    # --- Issue authorization code ---
    assert isinstance(session_result, SessionResult)
    resolved_user = session_result.user
    org_id = session_result.organization_id

    code = secrets.token_urlsafe(32)
    code_data = AuthCodeData(
        code=code,
        user_id=resolved_user.id,
        email=resolved_user.email,
        organization_id=org_id,
        is_platform_superuser=resolved_user.is_superuser,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        resource=resource,
        nonce=nonce,
        created_at=time.time(),
        bound_ip=ip_hash,
    )
    await store_auth_code(code_data)

    logger.info(
        "MCP OIDC: issued authorization code",
        user_id=str(resolved_user.id),
        organization_id=str(org_id),
        client_ip=_get_client_ip(request),
    )

    query = urlencode({"code": code, "state": state})
    redirect_url = f"{redirect_uri}?{query}"
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/authorize")
@router.post("/authorize")
async def authorize(
    request: Request,
    user: OptionalUserDep,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(default="S256"),
    scope: str = Query(default="openid"),
    state: str = Query(...),
    resource: str | None = Query(default=None),
    nonce: str | None = Query(default=None),
) -> Response:
    """OIDC authorization endpoint (authorization-code + PKCE)."""
    return await _handle_authorize(
        request=request,
        user=user,
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
        resource=resource,
        nonce=nonce,
    )


@router.get("/authorize/resume")
async def authorize_resume(
    request: Request,
    user: OptionalUserDep,
    txn: str = Query(...),
) -> Response:
    """Resume an authorization request after login or org selection."""
    txn_data = await load_and_delete_resume_transaction(txn)
    if txn_data is None:
        return _error_response(
            "invalid_request",
            "Unknown or expired resume transaction",
        )

    # Validate expiry
    if (
        time.time() - txn_data.created_at
        > oidc_config.RESUME_TRANSACTION_LIFETIME_SECONDS
    ):
        logger.warning("MCP OIDC: expired resume transaction", txn_id=txn)
        return _error_response(
            "invalid_request",
            "Resume transaction has expired",
        )

    # Validate IP binding
    if _hash_ip(request) != txn_data.bound_ip:
        logger.warning(
            "MCP OIDC: resume IP mismatch",
            txn_id=txn,
            expected_ip_hash=txn_data.bound_ip[:8],
            actual_ip_hash=_hash_ip(request)[:8],
        )
        return _error_response(
            "invalid_request",
            "Resume transaction IP mismatch",
        )

    params = txn_data.authorize_params
    logger.info("MCP OIDC: resuming authorization", txn_id=txn)

    return await _handle_authorize(
        request=request,
        user=user,
        response_type=params.get("response_type", "code"),
        client_id=params.get("client_id", ""),
        redirect_uri=params.get("redirect_uri", ""),
        code_challenge=params.get("code_challenge", ""),
        code_challenge_method=params.get("code_challenge_method", "S256"),
        scope=params.get("scope", "openid"),
        state=params.get("state", ""),
        resource=params.get("resource", ""),
        nonce=params.get("nonce"),
    )


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def _success_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _mint_access_token(
    *,
    user_id: str,
    organization_id: str,
    email: str,
    is_platform_superuser: bool,
    scope: str,
    resource: str,
) -> tuple[str, str, int]:
    """Mint an access token JWT. Returns (token, jti, exp)."""
    issuer = oidc_config.get_issuer_url()
    now = int(time.time())
    jti = secrets.token_urlsafe(16)
    claims = {
        "iss": issuer,
        "sub": user_id,
        "aud": resource,
        "exp": now + oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS,
        "iat": now,
        "jti": jti,
        "scope": scope,
        "email": email,
        "organization_id": organization_id,
        "is_platform_superuser": is_platform_superuser,
    }
    return mint_jwt(claims), jti, now


@router.post("/token")
async def token(
    request: Request,
    session: AsyncDBSessionBypass,
    grant_type: str = Form(...),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    code_verifier: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """OIDC token endpoint — supports authorization_code and refresh_token grants."""
    # --- Content-Type enforcement ---
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return _error_response(
            "invalid_request",
            "Content-Type must be application/x-www-form-urlencoded",
            status_code=415,
        )

    # --- Client authentication ---
    auth_client_id = client_id
    auth_client_secret = client_secret

    # Prefer Basic auth header if present
    if authorization:
        if parsed := _parse_basic_auth(authorization):
            auth_client_id, auth_client_secret = parsed

    if auth_client_id != oidc_config.INTERNAL_CLIENT_ID:
        return _error_response("invalid_client", "Unknown client", status_code=401)

    expected_secret = oidc_config.get_internal_client_secret()
    if not hmac.compare_digest(
        auth_client_secret.encode("utf-8"),
        expected_secret.encode("utf-8"),
    ):
        client_ip = _get_client_ip(request)
        logger.warning(
            "MCP OIDC: invalid client secret",
            client_ip=client_ip,
        )
        return _error_response(
            "invalid_client",
            "Invalid client credentials",
            status_code=401,
        )

    # --- Rate limiting (per-source-IP, after authentication) ---
    # Keyed by source IP because the token exchange is server-to-server
    # (MCP instance → API).  Each MCP instance has a distinct source IP,
    # giving per-instance isolation instead of a single global bucket.
    client_ip = _get_client_ip(request)
    if not await check_token_rate_limit(client_ip):
        logger.warning(
            "MCP OIDC: token rate limit exceeded",
            client_ip=client_ip,
        )
        return _error_response(
            "invalid_request",
            "Rate limit exceeded",
            status_code=429,
        )

    # --- Grant type dispatch ---
    if grant_type == "authorization_code":
        return await _handle_authorization_code_grant(
            session=session,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            auth_client_id=auth_client_id,
        )
    if grant_type == "refresh_token":
        return await _handle_refresh_token_grant(
            session=session,
            refresh_token=refresh_token,
            auth_client_id=auth_client_id,
        )
    return _error_response(
        "unsupported_grant_type",
        "Only authorization_code and refresh_token are supported",
    )


async def _handle_authorization_code_grant(
    *,
    session: AsyncSession,
    code: str | None,
    redirect_uri: str | None,
    code_verifier: str | None,
    auth_client_id: str,
) -> Response:
    """Exchange an authorization code for access (+ id, + refresh) tokens."""
    if not code or not redirect_uri or not code_verifier:
        return _error_response(
            "invalid_request",
            "code, redirect_uri, and code_verifier are required",
        )

    # --- Load and validate auth code ---
    code_data = await load_and_delete_auth_code(code)
    if code_data is None:
        logger.warning("MCP OIDC: unknown or reused auth code")
        return _error_response(
            "invalid_grant",
            "Authorization code is invalid, expired, or already used",
        )

    # Validate expiry
    if time.time() - code_data.created_at > oidc_config.AUTH_CODE_LIFETIME_SECONDS:
        logger.warning("MCP OIDC: expired auth code")
        return _error_response("invalid_grant", "Authorization code has expired")

    # Validate redirect_uri
    if redirect_uri != code_data.redirect_uri:
        return _error_response("invalid_grant", "redirect_uri mismatch")

    # Validate client_id
    if auth_client_id != code_data.client_id:
        return _error_response("invalid_grant", "client_id mismatch")

    # NOTE: IP binding is intentionally skipped on the token endpoint.
    # The authorize request arrives via Caddy (browser proxy) while the
    # token exchange comes directly from the MCP container — they will
    # always have different source IPs in a containerized deployment.
    # PKCE S256 already prevents authorization-code interception.

    # --- PKCE verification ---
    if code_data.code_challenge_method != "S256":
        return _error_response(
            "invalid_grant",
            "Only S256 code_challenge_method is supported",
        )
    if not _validate_pkce_s256(code_verifier, code_data.code_challenge):
        return _error_response("invalid_grant", "PKCE verification failed")

    # --- Mint access token ---
    access_token, jti, now = _mint_access_token(
        user_id=str(code_data.user_id),
        organization_id=str(code_data.organization_id),
        email=code_data.email,
        is_platform_superuser=code_data.is_platform_superuser,
        scope=code_data.scope,
        resource=code_data.resource,
    )

    await store_jti(jti)

    # --- Mint id token ---
    issuer = oidc_config.get_issuer_url()
    id_token_claims: dict[str, Any] = {
        "iss": issuer,
        "sub": str(code_data.user_id),
        "aud": auth_client_id,
        "exp": now + oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS,
        "iat": now,
        "email": code_data.email,
        "email_verified": True,
        "organization_id": str(code_data.organization_id),
    }
    if code_data.nonce:
        id_token_claims["nonce"] = code_data.nonce
    id_token = mint_jwt(id_token_claims)

    response_body: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS,
        "id_token": id_token,
        "scope": code_data.scope,
    }

    # --- Issue refresh token if offline_access was requested ---
    if _OFFLINE_ACCESS_SCOPE in code_data.scope.split():
        metadata = RefreshTokenMetadata(
            email=code_data.email,
            is_platform_superuser=code_data.is_platform_superuser,
            scope=code_data.scope,
            resource=code_data.resource,
        )
        refresh_token_value = await issue_refresh_token(
            session,
            user_id=code_data.user_id,
            organization_id=code_data.organization_id,
            client_id=auth_client_id,
            metadata=metadata,
        )
        response_body["refresh_token"] = refresh_token_value

    logger.info(
        "MCP OIDC: issued tokens",
        user_id=str(code_data.user_id),
        organization_id=str(code_data.organization_id),
        jti=jti,
        client_id=auth_client_id,
        with_refresh="refresh_token" in response_body,
    )

    return JSONResponse(response_body, headers=_success_headers())


async def _handle_refresh_token_grant(
    *,
    session: AsyncSession,
    refresh_token: str | None,
    auth_client_id: str,
) -> Response:
    """Rotate a refresh token: validate, mint a new access + refresh pair."""
    if not refresh_token:
        return _error_response(
            "invalid_request", "refresh_token is required for refresh_token grant"
        )

    try:
        ctx = await consume_refresh_token(
            session, token=refresh_token, client_id=auth_client_id
        )
    except RefreshTokenError as exc:
        return _error_response(exc.oauth_error, exc.description)

    access_token, jti, _ = _mint_access_token(
        user_id=str(ctx.user_id),
        organization_id=str(ctx.organization_id),
        email=ctx.metadata.email,
        is_platform_superuser=ctx.metadata.is_platform_superuser,
        scope=ctx.metadata.scope,
        resource=ctx.metadata.resource,
    )

    await store_jti(jti)

    new_refresh_token = await issue_refresh_token(
        session,
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        client_id=auth_client_id,
        metadata=ctx.metadata,
        family_id=ctx.family_id,
    )

    logger.info(
        "MCP OIDC: rotated refresh token",
        user_id=str(ctx.user_id),
        organization_id=str(ctx.organization_id),
        jti=jti,
        family_id=str(ctx.family_id),
    )

    # Per OIDC spec, id_token is not re-issued on refresh — only access + refresh.
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS,
            "refresh_token": new_refresh_token,
            "scope": ctx.metadata.scope,
        },
        headers=_success_headers(),
    )


# ---------------------------------------------------------------------------
# Userinfo
# ---------------------------------------------------------------------------


@router.get("/userinfo")
async def userinfo(
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """OIDC userinfo endpoint — returns claims from the access token."""
    if not authorization or not authorization.startswith("Bearer "):
        return _error_response(
            "invalid_token",
            "Bearer token required",
            status_code=401,
        )

    token_str = authorization[7:]
    try:
        # Accept any resource audience — do not hardcode to
        # _default_resource() so that tokens issued with a custom
        # ``resource`` parameter are not spuriously rejected.
        # Reject id_tokens (aud == client_id) since they are not
        # access tokens.
        claims = verify_jwt(
            token_str,
            issuer=oidc_config.get_issuer_url(),
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("MCP OIDC: invalid token at userinfo", error=str(exc))
        return _error_response(
            "invalid_token",
            "Token verification failed",
            status_code=401,
        )

    # Guard: id_tokens have aud=client_id; access tokens have aud=resource_url.
    if claims.get("aud") == oidc_config.INTERNAL_CLIENT_ID:
        return _error_response(
            "invalid_token",
            "id_token cannot be used at the userinfo endpoint",
            status_code=401,
        )

    return JSONResponse(
        {
            "sub": claims.get("sub"),
            "email": claims.get("email"),
            "email_verified": True,
            "organization_id": claims.get("organization_id"),
        }
    )
