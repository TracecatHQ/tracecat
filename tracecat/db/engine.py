import contextlib
import json
import os
from collections.abc import AsyncGenerator, Generator
from typing import Literal

import boto3
from botocore.exceptions import ClientError
from loguru import logger
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import Session, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession

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
    if config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS__ARN:
        # If AWS environment, get the password from AWS Secrets Manager
        logger.info("Using database username and password from AWS Secrets Manager...")
        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            response = client.get_secret_value(SecretId=config.TRACECAT__DB_PASS__ARN)
            password = json.loads(response["SecretString"])["password"]
        except ClientError as e:
            msg = "Error retrieving secret from AWS secrets manager."
            raise ClientError(msg) from e
        except KeyError as e:
            msg = "Error retrieving secret from AWS secrets manager.`password` not found in secret."
            raise KeyError(msg) from e

        uri = get_connection_string(
            username=config.TRACECAT__DB_USER,
            password=password,
            host=config.TRACECAT__DB_ENDPOINT,
            port=config.TRACECAT__DB_PORT,
            database=config.TRACECAT__DB_NAME,
            driver=driver,
        )
    elif config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS:
        # Else check if the password is in the local environment
        logger.info("Using database username and password from local environment...")
        uri = get_connection_string(
            username=config.TRACECAT__DB_USER,
            password=config.TRACECAT__DB_PASS,
            host=config.TRACECAT__DB_ENDPOINT,
            port=config.TRACECAT__DB_PORT,
            database=config.TRACECAT__DB_NAME,
            driver=driver,
        )
    else:
        # Else use the default URI
        logger.info("Using default database URI...")
        uri = config.TRACECAT__DB_URI
        if driver == "asyncpg":
            uri = uri.replace("psycopg", "asyncpg")

    logger.trace("Created database URI", uri=uri)
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


def get_engine() -> Engine:
    """Get the db sync connection pool."""
    global _engine
    if _engine is None:
        _engine = _create_db_engine()
    return _engine


def get_async_engine() -> AsyncEngine:
    """Get the db async connection pool."""
    global _async_engine
    if _async_engine is None:
        _async_engine = _create_async_db_engine()
    return _async_engine


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async_engine = get_async_engine()
    async with AsyncSession(async_engine, expire_on_commit=False) as async_session:
        yield async_session


def get_session_context_manager() -> contextlib.AbstractContextManager[Session]:
    return contextlib.contextmanager(get_session)()


def get_async_session_context_manager() -> (
    contextlib.AbstractAsyncContextManager[AsyncSession]
):
    return contextlib.asynccontextmanager(get_async_session)()
