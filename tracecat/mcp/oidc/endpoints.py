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
from starlette.responses import RedirectResponse, Response

from tracecat.auth.users import optional_current_active_user
from tracecat.config import TRACECAT__PUBLIC_APP_URL
from tracecat.db.models import User
from tracecat.logger import logger
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc.schemas import AuthCodeData, ResumeTransaction
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

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

OptionalUserDep = Annotated[User | None, Depends(optional_current_active_user)]


def _hash_ip(request: Request) -> str:
    """SHA-256 hex digest of the client IP for binding."""
    ip = request.client.host if request.client else "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()


def _get_client_ip(request: Request) -> str:
    """Return the raw client IP string."""
    return request.client.host if request.client else "unknown"


def _allowed_redirect_uri() -> str:
    """Return the single allowed redirect URI for the internal client.

    The FastMCP proxy's callback is ``{MCP_BASE_URL}/auth/callback``.
    Import lazily to avoid circular-import issues at module level.
    """
    from tracecat.mcp.config import TRACECAT_MCP__BASE_URL

    return f"{TRACECAT_MCP__BASE_URL.rstrip('/')}/auth/callback"


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
    """Validate PKCE S256: base64url(sha256(verifier)) == challenge."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
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
        "scopes_supported": ["openid", "profile", "email"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "client_secret_post",
        ],
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code"],
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
    from tracecat.mcp.config import TRACECAT_MCP__BASE_URL

    return f"{TRACECAT_MCP__BASE_URL.rstrip('/')}/mcp"


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
    session_result = await resolve_authorize_session(request, user)

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
                    f"{frontend_base}/mcp-auth/continue?txn={txn_id}",
                    status_code=302,
                )
            case NeedsAction.ORG_SELECTION:
                logger.info(
                    "MCP OIDC: superuser needs org selection",
                    txn_id=txn_id,
                )
                return RedirectResponse(
                    f"{frontend_base}/mcp-auth/select-org?txn={txn_id}",
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


@router.post("/token")
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    code_verifier: str = Form(...),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """OIDC token endpoint — exchanges an authorization code for tokens."""
    # --- Content-Type enforcement ---
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        return _error_response(
            "invalid_request",
            "Content-Type must be application/x-www-form-urlencoded",
            status_code=415,
        )

    # --- Grant type ---
    if grant_type != "authorization_code":
        return _error_response(
            "unsupported_grant_type",
            "Only authorization_code is supported",
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

    # --- Rate limiting (per-client, after authentication) ---
    # Keyed by client_id rather than IP because the token exchange is
    # server-to-server from the MCP container — all end users share one
    # source IP per MCP instance.
    if not await check_token_rate_limit(auth_client_id):
        logger.warning(
            "MCP OIDC: token rate limit exceeded",
            client_id=auth_client_id,
        )
        return _error_response(
            "invalid_request",
            "Rate limit exceeded",
            status_code=429,
        )

    # --- Load and validate auth code ---
    code_data = await load_and_delete_auth_code(code)
    if code_data is None:
        client_ip = _get_client_ip(request)
        logger.warning(
            "MCP OIDC: unknown or reused auth code",
            client_ip=client_ip,
        )
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

    # --- Mint tokens ---
    issuer = oidc_config.get_issuer_url()
    now = int(time.time())
    jti = secrets.token_urlsafe(16)

    access_token_claims = {
        "iss": issuer,
        "sub": str(code_data.user_id),
        "aud": code_data.resource,
        "exp": now + oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS,
        "iat": now,
        "jti": jti,
        "scope": code_data.scope,
        "email": code_data.email,
        "organization_id": str(code_data.organization_id),
        "is_platform_superuser": code_data.is_platform_superuser,
    }
    access_token = mint_jwt(access_token_claims)

    # Store JTI for future revocation support
    await store_jti(jti)

    id_token_claims = {
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

    logger.info(
        "MCP OIDC: issued tokens",
        user_id=str(code_data.user_id),
        organization_id=str(code_data.organization_id),
        jti=jti,
        client_id=auth_client_id,
    )

    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": oidc_config.ACCESS_TOKEN_LIFETIME_SECONDS,
            "id_token": id_token,
            "scope": code_data.scope,
        }
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
        claims = verify_jwt(token_str, issuer=oidc_config.get_issuer_url())
    except jwt.InvalidTokenError as exc:
        logger.warning("MCP OIDC: invalid token at userinfo", error=str(exc))
        return _error_response(
            "invalid_token",
            "Token verification failed",
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
