import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Literal

import boto3
from botocore.exceptions import ClientError
from loguru import logger
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
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
    # Check if AWS environment
    if config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS__ARN:
        logger.info("Retrieving database password from AWS Secrets Manager...")
        try:
            session = boto3.session.Session()  # type: ignore
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
            host=config.TRACECAT__DB_ENDPOINT,  # type: ignore
            port=config.TRACECAT__DB_PORT,  # type: ignore
            database=config.TRACECAT__DB_NAME,  # type: ignore
            driver=driver,
        )
        logger.info("Successfully retrieved database password from AWS Secrets Manager")
    # Else check if the password is in the local environment
    elif config.TRACECAT__DB_USER and config.TRACECAT__DB_PASS:
        uri = get_connection_string(
            username=config.TRACECAT__DB_USER,
            password=config.TRACECAT__DB_PASS,
            host=config.TRACECAT__DB_ENDPOINT,  # type: ignore
            port=config.TRACECAT__DB_PORT,  # type: ignore
            database=config.TRACECAT__DB_NAME,  # type: ignore
            driver=driver,
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


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async_engine = get_async_engine()
    async with AsyncSession(async_engine, expire_on_commit=False) as async_session:
        yield async_session


def get_async_session_context_manager() -> contextlib.AbstractAsyncContextManager[
    AsyncSession
]:
    return contextlib.asynccontextmanager(get_async_session)()
