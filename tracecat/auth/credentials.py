"""Tracecat authn credentials."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from functools import partial
from typing import Annotated, Any, Literal

import httpx
import orjson
import psycopg
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwk, jwt
from loguru import logger

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.types.auth import Role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

IS_AUTH_DISABLED = str(os.environ.get("TRACECAT__DISABLE_AUTH")) in ("true", "1")


def compute_hash(object_id: str) -> str:
    return hashlib.sha256(
        f"{object_id}{os.environ["TRACECAT__SIGNING_SECRET"]}".encode()
    ).hexdigest()


def encrypt(value: str) -> bytes:
    key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
    cipher_suite = Fernet(key)
    encrypted_value = cipher_suite.encrypt(value.encode())
    return encrypted_value


def decrypt(encrypted_value: bytes) -> str:
    key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
    cipher_suite = Fernet(key)
    decrypted_value = cipher_suite.decrypt(encrypted_value).decode()
    return decrypted_value


def encrypt_object(obj: dict[str, Any]) -> bytes:
    key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
    cipher_suite = Fernet(key)
    obj_bytes = orjson.dumps(obj)
    encrypted_value = cipher_suite.encrypt(obj_bytes)
    return encrypted_value


def decrypt_object(encrypted_obj: bytes) -> dict[str, Any]:
    key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
    cipher_suite = Fernet(key)
    obj_bytes = cipher_suite.decrypt(encrypted_obj)
    return orjson.loads(obj_bytes)


# TODO: Fix this
async def _validate_user_exists_in_db(user_id: str) -> tuple[str, ...] | None:
    """Check that a user exists in supabase and is authenticated."""
    # psycopg only supports postgresql:// URIs.
    # postgresql+psycopg:// is used by SQLAlchemy.
    db_uri = os.environ["TRACECAT__DB_URI"].replace("postgresql+psycopg", "postgresql")
    conn_manager = await psycopg.AsyncConnection.connect(db_uri)
    async with conn_manager as aconn:
        async with aconn.cursor() as acur:
            await acur.execute(
                "SELECT id, aud, role FROM auth.users WHERE (id=%s AND aud=%s AND role=%s)",
                (user_id, "authenticated", "authenticated"),
            )

            record = await acur.fetchone()
    return record


def _internal_get_role_from_service_key(
    *, user_id: str, service_role_name: str, api_key: str
) -> Role:
    if (
        not service_role_name
        or service_role_name not in config.TRACECAT__SERVICE_ROLES_WHITELIST
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Service-Role {service_role_name!r} invalid or not allowed",
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

if IS_AUTH_DISABLED:
    # Override the authentication functions with a dummy function
    _DEFAULT_TRACECAT_USER_ID = "default-tracecat-user"
    _DEFAULT_TRACECAT_JWT = "super-secret-jwt-token"

    async def _get_role_from_jwt(token: str | bytes) -> Role:
        if token != _DEFAULT_TRACECAT_JWT:
            raise HTTP_EXC(f"Auth disabled, please use {_DEFAULT_TRACECAT_JWT!r}.")
        role = Role(type="user", user_id=_DEFAULT_TRACECAT_USER_ID)
        ctx_role.set(role)
        return role

    async def _get_role_from_service_key(request: Request, api_key: str) -> Role:
        user_id = _DEFAULT_TRACECAT_USER_ID
        service_role_name = request.headers.get("Service-Role")
        role = _internal_get_role_from_service_key(
            user_id=user_id, service_role_name=service_role_name, api_key=api_key
        )
        ctx_role.set(role)
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
                    raise HTTP_EXC("Invalid JWT headers")
            if clerk_public_key is None:
                raise HTTP_EXC("Could not get public key")
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

        # TODO: Think about this again later
        # if await _validate_user_exists_in_db(user_id) is None:
        #     logger.error("User not authenticated")
        #     raise CREDENTIALS_EXCEPTION
        role = Role(type="user", user_id=user_id)
        ctx_role.set(role)
        return role

    async def _get_role_from_service_key(request: Request, api_key: str) -> Role:
        user_id = request.headers.get("Service-User-ID")
        service_role_name = request.headers.get("Service-Role")
        role = _internal_get_role_from_service_key(
            user_id=user_id, service_role_name=service_role_name, api_key=api_key
        )
        ctx_role.set(role)
        return role


async def authenticate_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> Role:
    """Authenticate a user JWT and return the 'sub' claim as the user_id."""
    if not token:
        raise CREDENTIALS_EXCEPTION
    return await _get_role_from_jwt(token)


async def authenticate_service(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
) -> Role:
    """Authenticate a service using an API key."""
    return await _get_role_from_service_key(request, api_key)


async def authenticate_user_or_service(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
    request: Request = None,
) -> Role:
    """Authenticate a user or service and return the role.

    Note: Don't have to set the session context here,
    we've already done that in the user/service checks."""
    if token:
        return await _get_role_from_jwt(token)
    if api_key:
        return await _get_role_from_service_key(request, api_key)
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
