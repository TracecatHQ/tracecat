"""Helpers for serializing Alembic migrations with PostgreSQL advisory locks."""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Namespace used to derive a deterministic advisory lock key for migrations.
MIGRATION_ADVISORY_LOCK_NAMESPACE = "tracecat:alembic:migrations:v1"


def migration_advisory_lock_id(namespace: str = MIGRATION_ADVISORY_LOCK_NAMESPACE) -> int:
    """Derive a stable PostgreSQL advisory lock id from a namespace string."""
    digest = hashlib.blake2b(namespace.encode("utf-8"), digest_size=8).digest()
    lock_id = int.from_bytes(digest, byteorder="big", signed=False) & ((1 << 63) - 1)
    return lock_id or 1


MIGRATION_ADVISORY_LOCK_ID = migration_advisory_lock_id()


def acquire_migration_advisory_lock(connection: Connection) -> bool:
    """Acquire the migration lock for PostgreSQL connections.

    Returns True if a lock was acquired and should be released by caller.
    """
    if connection.dialect.name != "postgresql":
        return False

    connection.execute(
        text("SELECT pg_advisory_lock(:lock_id)"),
        {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
    )
    return True


def release_migration_advisory_lock(connection: Connection) -> None:
    """Release the migration advisory lock for PostgreSQL connections."""
    if connection.dialect.name != "postgresql":
        return

    connection.execute(
        text("SELECT pg_advisory_unlock(:lock_id)"),
        {"lock_id": MIGRATION_ADVISORY_LOCK_ID},
    )
