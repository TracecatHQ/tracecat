from __future__ import annotations

import hashlib
import os
from typing import Annotated, Literal

import httpx
import psycopg
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import (
    APIKeyHeader,
    OAuth2PasswordBearer,
)
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel

import tracecat.config as cfg
from tracecat.config import TRACECAT__API_URL, TRACECAT__RUNNER_URL
from tracecat.contexts import ctx_session_role
from tracecat.logger import standard_logger

logger = standard_logger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


class AuthenticatedServiceClient(httpx.AsyncClient):
    """An authenticated service client. Typically used by internal services.

    Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    __default_service_id = "tracecat-service"

    def __init__(
        self,
        role: Role | None = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # Precedence: role > ctx_session_role > default role. Role is always set.
        self.role = role or ctx_session_role.get(
            Role(type="service", service_id="tracecat-service")
        )
        if self.role.type != "service":
            raise ValueError("AuthenticatedServiceClient can only be used by services")
        self.headers["Service-Role"] = self.role.service_id or self.__default_service_id
        self.headers["X-API-Key"] = os.environ["TRACECAT__SERVICE_KEY"]
        if self.role.user_id:
            self.headers["Service-User-ID"] = self.role.user_id


class AuthenticatedAPIClient(AuthenticatedServiceClient):
    """An authenticated httpx client to hit main API endpoints.

     Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    def __init__(self, role: Role | None = None, *args, **kwargs):
        kwargs["role"] = role
        kwargs["base_url"] = TRACECAT__API_URL
        super().__init__(*args, **kwargs)


class AuthenticatedRunnerClient(AuthenticatedServiceClient):
    """An authenticated httpx client to hit runner endpoints.

     Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    def __init__(self, role: Role | None = None, *args, **kwargs):
        kwargs["role"] = role
        kwargs["base_url"] = TRACECAT__RUNNER_URL
        super().__init__(*args, **kwargs)


class Role(BaseModel):
    """The role of the session.

    Params
    ------
    type : Literal["user", "service"]
        The type of role.
    user_id : str | None
        The user's JWT 'sub' claim, or the service's user_id.
        This can be None for internal services, or when a user hasn't been set for the role.
    service_id : str | None = None
        The service's role name, or None if the role is a user.


    User roles
    ----------
    - User roles are authenticated via JWT.
    - The `user_id` is the user's JWT 'sub' claim.
    - User roles do not have an associated `service_id`, this must be None.

    Service roles
    -------------
    - Service roles are authenticated via API key.
    - Used for internal services to authenticate with the API.
    - A service's `user_id` is the user it's acting on behalf of. This can be None for internal services.
    """

    type: Literal["user", "service"]
    user_id: str | None = None
    service_id: str | None = None


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


async def _get_role_from_jwt(token: str | bytes) -> Role:
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
    except ExpiredSignatureError as e:
        logger.error(f"ExpiredSignatureError: {e}")
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
        logger.error(f"JWT Error {e}")
        raise CREDENTIALS_EXCEPTION from e

    # Validate this against supabase
    if await _validate_user_exists_in_db(user_id) is None:
        logger.error("User not authenticated")
        raise CREDENTIALS_EXCEPTION
    role = Role(type="user", user_id=user_id)
    ctx_session_role.set(role)
    return role


async def _get_role_from_service_key(request: Request, api_key: str) -> Role:
    user_id = request.headers.get("Service-User-ID")
    service_role_name = request.headers.get("Service-Role")
    if (
        not service_role_name
        or service_role_name not in cfg.TRACECAT__SERVICE_ROLES_WHITELIST
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Service-Role {service_role_name!r} invalid or not allowed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if api_key != os.environ["TRACECAT__SERVICE_KEY"]:
        logger.error("Could not validate service key")
        raise CREDENTIALS_EXCEPTION
    role = Role(
        type="service",
        user_id=user_id,
        service_id=service_role_name,
    )
    ctx_session_role.set(role)
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
    raise CREDENTIALS_EXCEPTION
