import base64
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Literal

import boto3
from botocore.exceptions import ClientError
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db import (
    session_events,  # noqa: F401  # pyright: ignore[reportUnusedImport] - side effect import to register listeners
)
from tracecat.db.rls import set_rls_context, set_rls_context_from_role

# Global so we don't create more than one engine per process.
# Outside of being best practice, this is needed so we can properly pool
# connections and not create a new pool on every request
_async_engine: AsyncEngine | None = None


async def _initialize_session_rls_context(session: AsyncSession) -> None:
    """Initialize RLS context for a newly opened request session."""
    rls_mode = config.TRACECAT__RLS_MODE

    if rls_mode == config.RLSMode.ENFORCE:
        # Enforce mode applies role-based context with deny-default fallback.
        await set_rls_context_from_role(session)
        return

    role = ctx_role.get()
    user_id = role.user_id if role is not None else None

    if rls_mode == config.RLSMode.SHADOW:
        logger.trace(
            "RLS shadow mode active (bypass context with telemetry)",
            has_role=role is not None,
            role_type=role.type if role is not None else None,
            has_org_context=bool(role and role.organization_id),
            has_workspace_context=bool(role and role.workspace_id),
            is_platform_superuser=bool(role and role.is_platform_superuser),
        )

    # Off/shadow modes use bypass by default so rollout is app-controlled.
    await set_rls_context(
        session,
        org_id=None,
        workspace_id=None,
        user_id=user_id,
        bypass=True,
    )


def get_connection_string(
    *,
    username: str,
    password: str,
    host: str = "postgres_db",
    port: int | str = 5432,
    database: str = "postgres",
    scheme: str = "postgresql",
    driver: Literal["asyncpg", "psycopg"] = "asyncpg",
    sslmode: str | None = None,
) -> str:
    base = f"{scheme}+{driver}://{username}:{password}@{host}:{port!s}/{database}"
    if sslmode:
        # asyncpg uses 'ssl' parameter, psycopg uses 'sslmode'
        if driver == "asyncpg":
            # Map PostgreSQL sslmode to asyncpg ssl parameter
            # asyncpg accepts: 'disable', 'prefer', 'require', 'verify-ca', 'verify-full'
            return f"{base}?ssl={sslmode}"
        return f"{base}?sslmode={sslmode}"
    return base


def _get_db_uri(driver: Literal["psycopg", "asyncpg"] = "psycopg") -> str:
    # Check if AWS environment
    if config.TRACECAT__DB_PASS__ARN:
        logger.info("Retrieving database password from AWS Secrets Manager...")
        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            response = client.get_secret_value(SecretId=config.TRACECAT__DB_PASS__ARN)
            secret_string = response.get("SecretString")
            if not secret_string and response.get("SecretBinary"):
                try:
                    secret_string = base64.b64decode(response["SecretBinary"]).decode(
                        "utf-8"
                    )
                except UnicodeDecodeError as e:
                    logger.error(
                        "Error decoding secret from AWS Secrets Manager."
                        " SecretBinary must be UTF-8 encoded text or JSON."
                        " Use SecretString for plain text credentials.",
                        error=e,
                    )
                    raise e
            if not secret_string:
                raise KeyError("SecretString")

            parsed_json = True
            try:
                secret_payload = json.loads(secret_string)
            except json.JSONDecodeError:
                parsed_json = False
                secret_payload = {}

            username = config.TRACECAT__DB_USER
            password = None
            if isinstance(secret_payload, dict):
                username = username or secret_payload.get("username")
                password = secret_payload.get("password")

            if not password:
                if not parsed_json and config.TRACECAT__DB_USER:
                    password = secret_string
                else:
                    raise KeyError("password")

            if not username:
                raise KeyError("username")
        except ClientError as e:
            logger.error(
                "Error retrieving secret from AWS secrets manager."
                " Please check that the ECS task has sufficient permissions to read the secret and that the secret exists.",
                error=e,
            )
            raise e
        except KeyError as e:
            logger.error(
                "Error retrieving secret from AWS secrets manager."
                " Please check that the database secret in AWS Secrets Manager is a valid JSON object"
                " with `username` and `password` (or set TRACECAT__DB_USER and store the password as the secret string)."
            )
            raise e

        # Get the password from AWS Secrets Manager
        if not config.TRACECAT__DB_ENDPOINT:
            raise ValueError(
                "TRACECAT__DB_ENDPOINT is required when using AWS Secrets Manager"
            )
        if not config.TRACECAT__DB_PORT:
            raise ValueError(
                "TRACECAT__DB_PORT is required when using AWS Secrets Manager"
            )
        if not config.TRACECAT__DB_NAME:
            raise ValueError(
                "TRACECAT__DB_NAME is required when using AWS Secrets Manager"
            )
        uri = get_connection_string(
            username=username,
            password=password,
            host=config.TRACECAT__DB_ENDPOINT,
            port=config.TRACECAT__DB_PORT,
            database=config.TRACECAT__DB_NAME,
            driver=driver,
            sslmode=config.TRACECAT__DB_SSLMODE,
        )
        logger.info("Successfully retrieved database password from AWS Secrets Manager")
    # Else check if the password is in the local environment
    elif config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS:
        if not config.TRACECAT__DB_ENDPOINT:
            raise ValueError(
                "TRACECAT__DB_ENDPOINT is required when using DB credentials"
            )
        if not config.TRACECAT__DB_PORT:
            raise ValueError("TRACECAT__DB_PORT is required when using DB credentials")
        if not config.TRACECAT__DB_NAME:
            raise ValueError("TRACECAT__DB_NAME is required when using DB credentials")
        uri = get_connection_string(
            username=config.TRACECAT__DB_USER,
            password=config.TRACECAT__DB_PASS,
            host=config.TRACECAT__DB_ENDPOINT,
            port=config.TRACECAT__DB_PORT,
            database=config.TRACECAT__DB_NAME,
            driver=driver,
            sslmode=config.TRACECAT__DB_SSLMODE,
        )
    # Else use the default URI
    else:
        uri = config.TRACECAT__DB_URI
        if driver == "asyncpg":
            uri = uri.replace("psycopg", "asyncpg")
    logger.trace("Using database URI", uri=uri)
    return uri


def _create_async_db_engine() -> AsyncEngine:
    # Postgres as default
    engine_kwargs = {
        "max_overflow": config.TRACECAT__DB_MAX_OVERFLOW,
        "pool_recycle": config.TRACECAT__DB_POOL_RECYCLE,
        "pool_size": config.TRACECAT__DB_POOL_SIZE,
        "pool_pre_ping": True,
        "pool_use_lifo": True,  # Better for burst workloads
    }
    uri = _get_db_uri(driver="asyncpg")
    return create_async_engine(uri, **engine_kwargs)


def get_async_engine() -> AsyncEngine:
    """Get the db async connection pool."""
    global _async_engine
    if _async_engine is None:
        _async_engine = _create_async_db_engine()
    return _async_engine


def reset_async_engine() -> None:
    """Reset the global async engine.

    This should only be used in tests to ensure clean state between tests.
    """
    global _async_engine
    _async_engine = None


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async SQLAlchemy database session with RLS context.

    Behavior depends on TRACECAT__RLS_MODE:
    - off: bypass context by default
    - shadow: bypass context + rollout telemetry
    - enforce: role-derived context with deny-default fallback
    """
    async with AsyncSession(get_async_engine(), expire_on_commit=False) as session:
        await _initialize_session_rls_context(session)
        yield session


async def get_async_session_bypass_rls() -> AsyncGenerator[AsyncSession, None]:
    """Get an async SQLAlchemy database session with explicit RLS bypass context.

    Use this only for system operations that need unrestricted access:
    - Database migrations
    - Background jobs without user context
    - Administrative operations

    WARNING: Use sparingly and only when necessary. Prefer get_async_session()
    with proper role context for most operations.
    """
    async with AsyncSession(get_async_engine(), expire_on_commit=False) as session:
        await set_rls_context(
            session,
            org_id=None,
            workspace_id=None,
            user_id=None,
            bypass=True,
        )
        yield session


def get_async_session_context_manager() -> contextlib.AbstractAsyncContextManager[
    AsyncSession
]:
    """Get a context manager for an async SQLAlchemy database session with RLS context."""
    return contextlib.asynccontextmanager(get_async_session)()


def get_async_session_bypass_rls_context_manager() -> (
    contextlib.AbstractAsyncContextManager[AsyncSession]
):
    """Get a context manager for an async session with explicit RLS bypass.

    Use this for system operations that need unrestricted database access.
    """
    return contextlib.asynccontextmanager(get_async_session_bypass_rls)()
