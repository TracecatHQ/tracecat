import json
import logging
import os
from logging.config import fileConfig

import alembic_postgresql_enum
import boto3
from sqlalchemy import URL, engine_from_config, make_url, pool

from alembic import context
from tracecat import config as tracecat_config
from tracecat.db import models  # noqa: F401
from tracecat.db.iam.tokens import mint_token
from tracecat.db.iam.types import DbPasswordAuth
from tracecat.db.migration_lock import (
    acquire_migration_advisory_lock,
    release_migration_advisory_lock,
)
from tracecat.db.rls import enable_migration_rls_bypass
from tracecat.db.roles import MIGRATION_SEARCH_PATH, pin_migration_search_path

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

    if not isinstance(auth := tracecat_config.TRACECAT__DB_AUTH, DbPasswordAuth):
        # One token for this run: alembic opens a single connection at start,
        # and Postgres checks the token only at login.
        logging.info("Signing a database token (%s)", auth.mode)
        password = mint_token(auth, str(username))
    # Check if in AWS environment
    elif arn := os.getenv("TRACECAT__DB_PASS__ARN"):
        logging.info("Fetching database password from AWS Secrets Manager")
        session = boto3.Session()
        client = session.client("secretsmanager")
        response = client.get_secret_value(SecretId=arn)
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

    # URL.create escapes the credentials: quote_plus would turn a space in an
    # Entra role name into "+", which SQLAlchemy doesn't decode.
    TRACECAT__DB_URI = URL.create(
        "postgresql+psycopg",
        username="" if username is None else str(username),
        password="" if password is None else str(password),
        host=host,
        port=int(port),
        database=database,
        query={"sslmode": sslmode},
    ).render_as_string(hide_password=False)


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
    # Unqualified names must not resolve to a schema another role could create
    # ("$user" is tracecat_owner for migrator), from the first statement on,
    # including SQLAlchemy's cached default schema. An explicit options value
    # replaces libpq's (the URI's, else PGOPTIONS), so append to it.
    uri_options = (
        make_url(TRACECAT__DB_URI).query.get("options") if TRACECAT__DB_URI else None
    )
    operator_options = (
        uri_options if isinstance(uri_options, str) else os.getenv("PGOPTIONS", "")
    )
    connectable = engine_from_config(
        configuration=config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=TRACECAT__DB_URI,
        connect_args={
            "options": f"{operator_options} -c search_path={MIGRATION_SEARCH_PATH}".strip()
        },
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="public",
        )
        # After configure(): alembic must see the connection's first transaction.
        pin_migration_search_path(connection)
        # Forced RLS on dynamic tables also filters their owner; migrations
        # must see every row.
        enable_migration_rls_bypass(connection)

        has_advisory_lock = acquire_migration_advisory_lock(connection)
        try:
            with context.begin_transaction():
                context.run_migrations()
        finally:
            if has_advisory_lock:
                try:
                    release_migration_advisory_lock(connection)
                except Exception:
                    logging.exception(
                        "Failed to release Alembic advisory lock; continuing cleanup"
                    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
