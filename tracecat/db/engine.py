import contextlib
import json
import os
from collections.abc import AsyncGenerator, Generator
from typing import Literal

import boto3
from botocore.exceptions import ClientError
from loguru import logger
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import Session, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
    wait_random,
)

from tracecat import config

# Global so we don't create more than one engine per process.
# Outside of being best practice, this is needed so we can properly pool
# connections and not create a new pool on every request
_engine: Engine | None = None
_async_engine: AsyncEngine | None = None


def get_connection_string(
    *,
    username: str,
    password: str,
    host: str = "postgres_db",
    port: int | str = 5432,
    database: str = "postgres",
    scheme: str = "postgresql",
    driver: Literal["asyncpg", "psycopg"] = "asyncpg",
) -> str:
    return f"{scheme}+{driver}://{username}:{password}@{host}:{port!s}/{database}"


def _get_db_uri(driver: Literal["psycopg", "asyncpg"] = "psycopg") -> str:
    # Check if AWS environment
    if config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS__ARN:
        logger.info("Retrieving database password from AWS Secrets Manager...")
        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            response = client.get_secret_value(SecretId=config.TRACECAT__DB_PASS__ARN)
            password = json.loads(response["SecretString"])["password"]
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
                " `password` not found in secret."
                " Please check that the database secret in AWS Secrets Manager is a valid JSON object"
                " with `username` and `password`"
            )
            raise e

        # Get the password from AWS Secrets Manager
        uri = get_connection_string(
            username=config.TRACECAT__DB_USER,
            password=password,
            host=config.TRACECAT__DB_ENDPOINT,
            port=config.TRACECAT__DB_PORT,
            database=config.TRACECAT__DB_NAME,
            driver=driver,
        )
        logger.info("Successfully retrieved database password from AWS Secrets Manager")
    # Else check if the password is in the local environment
    elif config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS:
        uri = get_connection_string(
            username=config.TRACECAT__DB_USER,
            password=config.TRACECAT__DB_PASS,
            host=config.TRACECAT__DB_ENDPOINT,
            port=config.TRACECAT__DB_PORT,
            database=config.TRACECAT__DB_NAME,
            driver=driver,
        )
    # Else use the default URI
    else:
        uri = config.TRACECAT__DB_URI
        if driver == "asyncpg":
            uri = uri.replace("psycopg", "asyncpg")
    logger.trace("Using database URI", uri=uri)
    return uri


def _create_db_engine() -> Engine:
    if config.TRACECAT__APP_ENV == "production":
        # Postgres
        sslmode = os.getenv("TRACECAT__DB_SSLMODE", "require")
        engine_kwargs = {
            "pool_timeout": 30,
            "pool_recycle": 3600,
            "connect_args": {"sslmode": sslmode},
        }
    else:
        # Postgres as default
        engine_kwargs = {
            "pool_timeout": 30,
            "pool_recycle": 3600,
            "connect_args": {"sslmode": "disable"},
        }

    uri = _get_db_uri(driver="psycopg")
    return create_engine(uri, **engine_kwargs)


def _create_async_db_engine() -> AsyncEngine:
    # Postgres as default
    engine_kwargs = {
        "pool_size": 50,
        "max_overflow": 10,
        "future": True,
        "pool_recycle": 3600,
    }

    uri = _get_db_uri(driver="asyncpg")
    return create_async_engine(uri, **engine_kwargs)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1) + wait_random(0, 1),
    retry=retry_if_exception_type(SQLAlchemyError),
    reraise=True,
)
def get_engine(force_recreate=False) -> Engine:
    """Get the db sync connection pool."""
    global _engine
    if _engine is None or force_recreate:
        if _engine is not None:
            _engine.dispose()
        _engine = _create_db_engine()
    try:
        # Test the connection
        with _engine.connect() as conn:
            conn.execute("SELECT 1")
        return _engine
    except SQLAlchemyError:
        _engine = None
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1) + wait_random(0, 1),
    retry=retry_if_exception_type(SQLAlchemyError),
    reraise=True,
)
async def get_async_engine(force_recreate=False) -> AsyncEngine:
    """Get the db async connection pool."""
    global _async_engine
    if _async_engine is None or force_recreate:
        if _async_engine is not None:
            await _async_engine.dispose()
        _async_engine = _create_async_db_engine()
    try:
        # Test the connection
        async with _async_engine.connect() as conn:
            await conn.execute("SELECT 1")
        return _async_engine
    except SQLAlchemyError:
        _async_engine = None
        raise


def get_session() -> Generator[Session, None, None]:
    engine = get_engine()
    with Session(engine) as session:
        try:
            # Test the connection
            session.exec(select(1)).one()
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async_engine = await get_async_engine()
    async with AsyncSession(async_engine, expire_on_commit=False) as async_session:
        try:
            # Test the connection
            await async_session.exec(select(1)).one()
            yield async_session
        except Exception:
            await async_session.rollback()
            raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1) + wait_random(0, 1),
    retry=retry_if_exception_type(SQLAlchemyError),
    reraise=True,
)
def get_session_context_manager() -> contextlib.AbstractContextManager[Session]:
    try:
        return contextlib.contextmanager(get_session)()
    except SQLAlchemyError:
        logger.warning(
            "Database error occurred. Attempting to recreate engine with potentially updated configuration."
        )
        get_engine(force_recreate=True)
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1) + wait_random(0, 1),
    retry=retry_if_exception_type(SQLAlchemyError),
    reraise=True,
)
async def get_async_session_context_manager() -> (
    contextlib.AbstractAsyncContextManager[AsyncSession]
):
    try:
        return contextlib.asynccontextmanager(get_async_session)()
    except SQLAlchemyError:
        logger.warning(
            "Database error occurred. Attempting to recreate async engine with potentially updated configuration."
        )
        await get_async_engine(force_recreate=True)
        raise
