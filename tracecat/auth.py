from __future__ import annotations

import hashlib
import os
from typing import Annotated

import httpx
import psycopg
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import (
    APIKeyHeader,
    OAuth2PasswordBearer,
)
from jose import JWTError, jwt

import tracecat.config as cfg
from tracecat.config import TRACECAT__API_URL
from tracecat.contexts import ctx_session_role
from tracecat.logger import standard_logger
from tracecat.types.session import Role

logger = standard_logger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


class AuthenticatedClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_url = TRACECAT__API_URL

    async def __aenter__(self):
        """Inject the service role and api key to the headers at query time."""
        self.headers["Service-Role"] = "tracecat-runner"
        self.headers["X-API-Key"] = os.environ["TRACECAT__SERVICE_KEY"]
        return await super().__aenter__()


def compute_hash(object_id: str) -> str:
    return hashlib.sha256(
        f"{object_id}{os.environ["TRACECAT__SIGNING_SECRET"]}".encode()
    ).hexdigest()


def encrypt_key(api_key: str) -> bytes:
    key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
    cipher_suite = Fernet(key)
    encrypted_api_key = cipher_suite.encrypt(api_key.encode())
    return encrypted_api_key


def decrypt_key(encrypted_api_key: bytes) -> str:
    key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
    cipher_suite = Fernet(key)
    decrypted_api_key = cipher_suite.decrypt(encrypted_api_key).decode()
    return decrypted_api_key


async def _validate_user_exists_in_db(user_id: str) -> tuple[str, ...] | None:
    """Check that a user exists in supabase and is authenticated."""
    conn_manager = await psycopg.AsyncConnection.connect(
        os.environ["SUPABASE_PSQL_URL"]
    )
    async with conn_manager as aconn:
        async with aconn.cursor() as acur:
            await acur.execute(
                "SELECT id, aud, role FROM auth.users WHERE (id=%s AND aud=%s AND role=%s)",
                (user_id, "authenticated", "authenticated"),
            )

            record = await acur.fetchone()
    return record


async def authenticate_user_session(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> Role:
    """Authenticate a JWT and return the 'sub' claim as the user_id."""

    try:
        payload = jwt.decode(
            token,
            key=os.environ["SUPABASE_JWT_SECRET"],
            algorithms=os.environ["SUPABASE_JWT_ALGORITHM"],
            # NOTE: Workaround, not sure if there are alternatives
            options={"verify_aud": False},
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            logger.error("No sub claim in JWT")
            raise CREDENTIALS_EXCEPTION
    except JWTError as e:
        logger.error(f"JWT Error {e}")
        raise CREDENTIALS_EXCEPTION from e

    # Validate this against supabase
    if await _validate_user_exists_in_db(user_id) is None:
        logger.error("User not authenticated")
        raise CREDENTIALS_EXCEPTION
    role = Role(id=user_id, variant="user")
    ctx_session_role.set(role)
    return role


async def authenticate_service(request: Request, api_key: str | None = None) -> Role:
    service_role_name = request.headers.get("Service-Role")
    if (
        not service_role_name
        or service_role_name not in cfg.TRACECAT__SERVICE_ROLES_WHITELIST
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Service-Role {service_role_name!r} invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if api_key != os.environ["TRACECAT__SERVICE_KEY"]:
        logger.error("Could not validate service key")
        raise CREDENTIALS_EXCEPTION
    role = Role(id=service_role_name, variant="service")
    ctx_session_role.set(role)
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
        return await authenticate_user_session(token)
    if api_key:
        return await authenticate_service(request, api_key)
    raise CREDENTIALS_EXCEPTION
