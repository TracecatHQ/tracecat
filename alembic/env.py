import json
import logging
import os
from logging.config import fileConfig
from urllib.parse import quote_plus

import alembic_postgresql_enum
import boto3
from sqlalchemy import engine_from_config, pool

from alembic import context
from tracecat.db import models  # noqa: F401

alembic_postgresql_enum.set_configuration(
    alembic_postgresql_enum.Config(
        add_type_ignore=True,
    )
)

TRACECAT__DB_URI = os.getenv("TRACECAT__DB_URI")
if not TRACECAT__DB_URI:
    username = os.getenv("TRACECAT__DB_USER", "postgres")
    host = os.getenv("TRACECAT__DB_ENDPOINT")
    port = os.getenv("TRACECAT__DB_PORT", 5432)
    database = os.getenv("TRACECAT__DB_NAME", "postgres")
    sslmode = os.getenv("TRACECAT__DB_SSLMODE", "require")

    # Check if in AWS environment
    if os.getenv("TRACECAT__DB_PASS__ARN"):
        logging.info("Fetching database password from AWS Secrets Manager")
        session = boto3.Session()
        client = session.client("secretsmanager")
        response = client.get_secret_value(SecretId=os.getenv("TRACECAT__DB_PASS__ARN"))
        secret_string = response["SecretString"]
        try:
            payload = json.loads(secret_string)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            password = payload["password"]
            username = payload.get("username") or username
        else:
            password = secret_string
    else:
        logging.info("Fetching database password from environment variable")
        password = os.getenv("TRACECAT__DB_PASS")

    user = quote_plus("" if username is None else str(username))
    pwd = quote_plus("" if password is None else str(password))
    TRACECAT__DB_URI = (
        f"postgresql+psycopg://{user}:{pwd}@{host}:{port!s}/{database}"
        f"?sslmode={sslmode}"
    )


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
target_metadata = models.Base.metadata

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
    connectable = engine_from_config(
        configuration=config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=TRACECAT__DB_URI,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
