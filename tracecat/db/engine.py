import contextlib
import json
import os
from collections.abc import AsyncGenerator
from typing import Literal

import aioboto3
import asyncpg.exceptions
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
    wait_random,
)

from tracecat import config
from tracecat.logger import logger

# Global so we don't create more than one engine per process.
# Outside of being best practice, this is needed so we can properly pool
# connections and not create a new pool on every request
_async_engine: AsyncEngine | None = None

DBDriver = Literal["asyncpg", "psycopg"]


def get_connection_string(
    *,
    username: str,
    password: str,
    host: str = "postgres_db",
    port: int | str = 5432,
    database: str = "postgres",
    scheme: str = "postgresql",
    driver: DBDriver = "asyncpg",
) -> str:
    return f"{scheme}+{driver}://{username}:{password}@{host}:{port!s}/{database}"


def get_db_uri(driver: DBDriver = "asyncpg") -> str:
    username = os.getenv("TRACECAT__DB_USER", "postgres")
    host = os.getenv("TRACECAT__DB_ENDPOINT", "postgres_db")
    port = os.getenv("TRACECAT__DB_PORT", 5432)
    database = os.getenv("TRACECAT__DB_NAME", "postgres")

    if password := os.getenv("TRACECAT__DB_PASS"):
        logger.trace("Using database password from environment variable")
        uri = get_connection_string(
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
            driver=driver,
        )
    else:
        logger.trace("Using full database URI from environment variable")
        uri = config.TRACECAT__DB_URI
        if driver == "asyncpg":
            uri = uri.replace("psycopg", "asyncpg")
    return uri


def _create_async_db_engine() -> AsyncEngine:
    # Postgres as default
    engine_kwargs = {
        "pool_size": 50,
        "max_overflow": 10,
        "future": True,
        "pool_recycle": 3600,
    }

    uri = get_db_uri("asyncpg")
    return create_async_engine(uri, **engine_kwargs)


async def get_db_password_from_secrets_manager(secret_arn: str) -> str:
    logger.info("Retrieving database password from AWS Secrets Manager")
    try:
        async with aioboto3.Session().client(service_name="secretsmanager") as client:
            response = await client.get_secret_value(SecretId=secret_arn)
    except ClientError as e:
        logger.error(
            "Error retrieving secret from AWS secrets manager. "
            "Please check that the ECS task has sufficient permissions to read the secret and that the secret exists.",
            error=e,
        )
        raise

    try:
        secret_string = response["SecretString"]
        secret_dict = json.loads(secret_string)
        return secret_dict["password"]
    except json.JSONDecodeError:
        logger.error(
            "Error decoding secret from AWS secrets manager. "
            "Please check that the secret is a valid JSON object "
            "with `username` and `password`"
        )
        raise
    except KeyError:
        logger.error(
            "Error retrieving secret from AWS secrets manager. "
            "`password` not found in secret. "
            "Please check that the database secret in AWS Secrets Manager is a valid JSON object "
            "with `username` and `password`"
        )
        raise


async def fetch_db_password() -> str:
    if password_arn := os.getenv("TRACECAT__DB_PASS__ARN"):
        return await get_db_password_from_secrets_manager(password_arn)
    if password := os.getenv("TRACECAT__DB_PASS"):
        return password
    raise ValueError("Database password not found")


async def recreate_db_engine() -> AsyncEngine:
    """Recreate the db async connection pool."""
    global _async_engine
    if _async_engine is not None:
        logger.info("Disposing of existing async engine")
        await _async_engine.dispose()
    #  We need to update the password in the environment
    # so we don't have to fetch it from AWS again
    password = await fetch_db_password()
    os.environ["TRACECAT__DB_PASS"] = password
    _async_engine = _create_async_db_engine()
    return _async_engine


async def db_engine_ok(engine: AsyncEngine) -> bool:
    """Ping the database connection, returning True if successful."""
    try:
        async with AsyncSession(engine) as session:
            await session.exec(select(1))
        return True
    except asyncpg.exceptions.InvalidPasswordError as e:
        logger.error("Invalid database password", error=e)
        return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1) + wait_random(0, 1),
    retry=retry_if_exception_type(asyncpg.exceptions.InvalidPasswordError),
    reraise=True,
)
async def get_async_engine() -> AsyncEngine:
    """Get the db async connection pool."""
    global _async_engine
    if _async_engine is None:
        _async_engine = _create_async_db_engine()
    elif not (engine_ok := await db_engine_ok(_async_engine)):
        logger.info("Recreating async engine", engine_ok=engine_ok)
        _async_engine = await recreate_db_engine()
    return _async_engine


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async_engine = await get_async_engine()
    async with AsyncSession(async_engine, expire_on_commit=False) as async_session:
        yield async_session


def get_async_session_context_manager() -> (
    contextlib.AbstractAsyncContextManager[AsyncSession]
):
    return contextlib.asynccontextmanager(get_async_session)()
