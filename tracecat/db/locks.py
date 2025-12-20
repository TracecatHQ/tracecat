"""PostgreSQL advisory lock helpers for distributed coordination."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.identifiers import WorkspaceID


@asynccontextmanager
async def pg_advisory_lock(session: AsyncSession, key: int) -> AsyncIterator[None]:
    """Acquire a PostgreSQL advisory lock with the given key.

    Args:
        session: Database session
        key: 64-bit integer key for the lock

    Yields:
        None while lock is held

    Raises:
        ValueError: If key is out of valid range for PostgreSQL advisory locks

    Note:
        Key must be a 64-bit signed integer (-2^63 to 2^63-1)
    """
    if not (-(2**63) <= key < 2**63):
        raise ValueError(f"Lock key {key} out of range for PostgreSQL advisory locks")

    try:
        # Acquire the lock (blocks until available)
        await session.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        yield
    finally:
        # Always release the lock
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


async def try_pg_advisory_lock(session: AsyncSession, key: int) -> bool:
    """Try to acquire a PostgreSQL advisory lock without blocking.

    Args:
        session: Database session
        key: 64-bit integer key for the lock

    Returns:
        True if lock was acquired, False if already held

    Raises:
        ValueError: If key is out of valid range for PostgreSQL advisory locks
    """
    if not (-(2**63) <= key < 2**63):
        raise ValueError(f"Lock key {key} out of range for PostgreSQL advisory locks")

    result = await session.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
    )
    return result.scalar() is True


async def pg_advisory_unlock(session: AsyncSession, key: int) -> bool:
    """Release a PostgreSQL advisory lock.

    Args:
        session: Database session
        key: 64-bit integer key for the lock

    Returns:
        True if lock was released, False if not held by this session

    Raises:
        ValueError: If key is out of valid range for PostgreSQL advisory locks
    """
    if not (-(2**63) <= key < 2**63):
        raise ValueError(f"Lock key {key} out of range for PostgreSQL advisory locks")

    result = await session.execute(
        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
    )
    return result.scalar() is True


def derive_lock_key(workspace_id: WorkspaceID, repo_url: str) -> int:
    """Derive a stable 64-bit lock key from workspace_id and repo_url.

    Args:
        workspace_id: UUID string of the workspace
        repo_url: Git repository URL

    Returns:
        64-bit signed integer key suitable for PostgreSQL advisory locks

    Raises:
        ValueError: If inputs are invalid (empty, None, or too long)
    """
    ws_id = str(workspace_id)
    if not isinstance(ws_id, str) or not isinstance(repo_url, str):
        raise ValueError("workspace_id and repo_url must be strings")
    if not ws_id or not repo_url:
        raise ValueError("workspace_id and repo_url must be non-empty")
    if len(ws_id) > 100 or len(repo_url) > 500:
        raise ValueError(
            "Input strings too long (workspace_id max 100, repo_url max 500 chars)"
        )

    combined = f"{ws_id}:{repo_url}"
    digest = hashlib.sha256(combined.encode("utf-8")).digest()
    # Take first 8 bytes and convert to signed 64-bit int
    raw_int = int.from_bytes(digest[:8], "big")
    # Ensure it fits in signed 64-bit range
    return raw_int % (2**63)


def derive_lock_key_from_parts(*parts: str) -> int:
    """Derive a stable 64-bit lock key from arbitrary string parts.

    This is useful for distributed coordination where you want a stable key
    without having a workspace_id/repo_url pair.
    """
    if not parts:
        raise ValueError("parts must be non-empty")

    if any(not isinstance(part, str) for part in parts):
        raise ValueError("parts must all be strings")

    if any(not part for part in parts):
        raise ValueError("parts must all be non-empty")

    # Guard against accidentally hashing huge strings (e.g. full payloads)
    if any(len(part) > 500 for part in parts):
        raise ValueError("part strings too long (max 500 chars each)")

    combined = ":".join(parts)
    digest = hashlib.sha256(combined.encode("utf-8")).digest()
    raw_int = int.from_bytes(digest[:8], "big")
    return raw_int % (2**63)
