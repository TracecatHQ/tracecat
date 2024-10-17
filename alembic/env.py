import asyncio
import os
from logging.config import fileConfig

import alembic_postgresql_enum  # noqa: F401
from sqlalchemy import engine_from_config, pool
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel

from alembic import context
from tracecat.db import schemas  # noqa: F401
from tracecat.db.engine import fetch_db_password, get_connection_string
from tracecat.logger import logger


def _get_db_pass() -> str:
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(fetch_db_password())


def _get_db_uri() -> str:
    username = os.getenv("TRACECAT__DB_USER", "postgres")
    host = os.getenv("TRACECAT__DB_ENDPOINT")
    port = os.getenv("TRACECAT__DB_PORT", 5432)
    database = os.getenv("TRACECAT__DB_NAME", "postgres")
    password = _get_db_pass()

    return get_connection_string(
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    )


TRACECAT__DB_URI = os.getenv("TRACECAT__DB_URI") or _get_db_uri()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=TRACECAT__DB_URI,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    global TRACECAT__DB_URI

    connectable = engine_from_config(
        configuration=config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=TRACECAT__DB_URI,
    )

    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)

            with context.begin_transaction():
                context.run_migrations()
    except OperationalError as e:
        logger.error("Error connecting to database, password may have rotated", error=e)
        # Perhaps the database key has rotated. Try to get the password again.
        password = _get_db_pass()
        # If password hasn't changed, raise
        if password in TRACECAT__DB_URI:
            msg = "Database password has rotated, but new password not found in DB URI"
            logger.error(msg)
            raise ValueError(msg) from e
        # Try to run migrations again with new password
        TRACECAT__DB_URI = _get_db_uri()
        return run_migrations_online()
    except Exception as e:
        logger.error("Unexpected error connecting to database", error=e)
        raise


if context.is_offline_mode():
    logger.info("Running migrations offline")
    run_migrations_offline()
else:
    logger.info("Running migrations online")
    run_migrations_online()
