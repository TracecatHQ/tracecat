"""Tracecat authn credentials."""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import partial
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwk, jwt

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.logging import logger
from tracecat.types.auth import Role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def _internal_get_role_from_service_key(
    *, user_id: str, service_role_name: str, api_key: str
) -> Role:
    if (
        not service_role_name
        or service_role_name not in config.TRACECAT__SERVICE_ROLES_WHITELIST
    ):
        msg = f"Service-Role {service_role_name!r} invalid or not allowed"
        logger.error(msg)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=msg,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if api_key != os.environ["TRACECAT__SERVICE_KEY"]:
        logger.error("Could not validate service key")
        raise CREDENTIALS_EXCEPTION
    return Role(
        type="service",
        user_id=user_id,
        service_id=service_role_name,
    )


async def get_clerk_public_key(kid: str) -> dict[str, Any] | None:
    """Get the public key from the JWKS endpoint using the JWT kid claim."""
    async with httpx.AsyncClient() as client:
        jwks_uri = os.environ["CLERK_FRONTEND_API_URL"] + "/.well-known/jwks.json"
        response = await client.get(jwks_uri)
        jwks = response.json()
    public_keys = {
        key["kid"]: jwk.construct(key) for key in jwks["keys"] if key["kid"] == kid
    }
    return public_keys.get(kid)


HTTP_EXC = partial(
    lambda msg: HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=msg or "Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
)

if config.TRACECAT__AUTH_DISABLED:
    # Override the authentication functions with a dummy function
    _DEFAULT_TRACECAT_USER_ID = "default-tracecat-user"
    _DEFAULT_TRACECAT_JWT = "super-secret-jwt-token"

    async def _get_role_from_jwt(token: str | bytes) -> Role:
        if token != _DEFAULT_TRACECAT_JWT:
            logger.error("Auth disabled, please use the default JWT")
            raise HTTP_EXC(f"Auth disabled, please use {_DEFAULT_TRACECAT_JWT!r}.")
        role = Role(
            type="user", user_id=_DEFAULT_TRACECAT_USER_ID, service_id="tracecat-api"
        )
        return role

    async def _get_role_from_service_key(request: Request, api_key: str) -> Role:
        user_id = _DEFAULT_TRACECAT_USER_ID
        service_role_name = request.headers.get("Service-Role")
        role = _internal_get_role_from_service_key(
            user_id=user_id, service_role_name=service_role_name, api_key=api_key
        )
        return role
else:

    async def _get_role_from_jwt(token: str | bytes) -> Role:
        try:
            match jwt.get_unverified_headers(token):
                case {
                    "alg": alg,
                    "kid": kid,
                    "typ": "JWT",
                }:
                    clerk_public_key = await get_clerk_public_key(kid=kid)
                case _:
                    msg = "Invalid JWT headers"
                    logger.error(msg)
                    raise HTTP_EXC(msg)
            if clerk_public_key is None:
                msg = "Could not get public key"
                logger.error(msg)
                raise HTTP_EXC(msg)
            payload = jwt.decode(
                token,
                key=clerk_public_key,
                algorithms=alg,
                issuer=os.environ["CLERK_FRONTEND_API_URL"],
                # NOTE: Workaround, not sure if there are alternatives
                options={"verify_aud": False},
            )
            user_id: str = payload.get("sub")
            if user_id is None:
                raise HTTP_EXC("No sub claim in JWT")
        except ExpiredSignatureError as e:
            logger.error("Signature expired", error=e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
                headers={
                    "WWW-Authenticate": "Bearer",
                    "Access-Control-Expose-Headers": "X-Expired-Token",
                    "X-Expired-Token": "true",
                },
            ) from e
        except JWTError as e:
            msg = f"JWT Error {e}"
            logger.error(msg)
            raise HTTP_EXC(msg) from e
        except Exception as e:
            msg = f"Error {e}"
            logger.error(msg)
            raise HTTP_EXC(msg) from e

        role = Role(type="user", user_id=user_id, service_id="tracecat-api")
        return role

    async def _get_role_from_service_key(request: Request, api_key: str) -> Role:
        user_id = request.headers.get("Service-User-ID")
        service_role_name = request.headers.get("Service-Role")
        role = _internal_get_role_from_service_key(
            user_id=user_id, service_role_name=service_role_name, api_key=api_key
        )
        return role


async def authenticate_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> Role:
    """Authenticate a user JWT and return the 'sub' claim as the user_id.

    `ctx_role` ContextVar is set here.
    """
    if not token:
        raise CREDENTIALS_EXCEPTION
    role = await _get_role_from_jwt(token)
    ctx_role.set(role)
    return role


async def authenticate_service(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
) -> Role:
    """Authenticate a service using an API key.

    `ctx_role` ContextVar is set here.
    """
    role = await _get_role_from_service_key(request, api_key)
    ctx_role.set(role)
    return role


async def authenticate_user_or_service(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
    request: Request = None,
) -> Role:
    """Authenticate a user or service and return the role.

    Note: Don't have to set the session context here,
    we've already done that in the user/service checks."""
    if token:
        return await authenticate_user(token)
    if api_key:
        return await authenticate_service(request, api_key)
    raise HTTP_EXC("Could not validate credentials")


@contextmanager
def TemporaryRole(
    type: Literal["user", "service"] = "service",
    user_id: str | None = None,
    service_id: str | None = None,
):
    """An async context manager to authenticate a user or service."""
    prev_role = ctx_role.get()
    temp_role = Role(type=type, user_id=user_id, service_id=service_id)
    ctx_role.set(temp_role)
    try:
        yield temp_role
    finally:
        ctx_role.set(prev_role)
