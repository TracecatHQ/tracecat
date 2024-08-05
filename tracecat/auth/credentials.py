"""Tracecat authn credentials."""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import partial
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer

from tracecat import config
from tracecat.auth.users import current_active_user
from tracecat.contexts import ctx_role
from tracecat.db.schemas import User
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


HTTP_EXC = partial(
    lambda msg: HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=msg or "Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
)


async def _get_role_from_service_key(request: Request, api_key: str) -> Role:
    user_id = request.headers.get("Service-User-ID")
    service_role_name = request.headers.get("Service-Role")
    role = _internal_get_role_from_service_key(
        user_id=user_id, service_role_name=service_role_name, api_key=api_key
    )
    return role


async def authenticate_user(
    current_user: Annotated[User, Depends(current_active_user)],
) -> Role:
    """Map the current user to a role.

    `ctx_role` ContextVar is set here.
    """
    role = Role(type="user", user_id=str(current_user.id), service_id="tracecat-api")
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
