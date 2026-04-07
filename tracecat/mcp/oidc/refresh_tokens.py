"""Persistence and rotation logic for MCP OIDC refresh tokens.

Refresh tokens are stored in Postgres (table ``mcp_refresh_token``) so they
survive Redis flushes and admin restarts. Lookups are by SHA-256 hash of the
opaque token — the plaintext token never lives at rest.

Rotation is single-use: every successful exchange transitions the consumed
token to ``status='used'`` and issues a fresh one. Replay of a consumed
token revokes the entire family (all tokens descended from the same
authorization-code exchange).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import orjson
from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.db.models import MCPRefreshToken
from tracecat.logger import logger
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc.schemas import RefreshTokenContext, RefreshTokenMetadata
from tracecat.secrets.encryption import decrypt_value, encrypt_value


class RefreshTokenError(Exception):
    """Raised when a refresh token operation fails.

    Carries the OAuth 2.0 error code and human-readable description so the
    token endpoint can translate the failure into the standard JSON response.
    """

    def __init__(self, oauth_error: str, description: str) -> None:
        super().__init__(f"{oauth_error}: {description}")
        self.oauth_error = oauth_error
        self.description = description


def _hash_token(token: str) -> str:
    """SHA-256 hex digest of the opaque refresh token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _encode_metadata(metadata: RefreshTokenMetadata) -> bytes:
    """Fernet-encrypt the metadata JSON blob."""
    payload = orjson.dumps(metadata.model_dump(mode="json"))
    return encrypt_value(payload, key=get_db_encryption_key())


def _decode_metadata(blob: bytes) -> RefreshTokenMetadata:
    """Decrypt and parse the metadata JSON blob."""
    payload = decrypt_value(blob, key=get_db_encryption_key())
    return RefreshTokenMetadata.model_validate_json(payload)


async def _revoke_family_rows(session: AsyncSession, family_id: uuid.UUID) -> None:
    """Mark every non-revoked token in a family as revoked within a transaction."""
    await session.execute(
        update(MCPRefreshToken)
        .where(
            MCPRefreshToken.family_id == family_id,
            MCPRefreshToken.status != "revoked",
        )
        .values(status="revoked")
    )


async def issue_refresh_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    client_id: str,
    metadata: RefreshTokenMetadata,
    family_id: uuid.UUID | None = None,
) -> str:
    """Mint a new refresh token and persist it to the database.

    Returns the plaintext opaque token. This is the only place the plaintext
    exists in memory at the API — only its SHA-256 hash is stored.
    """
    token = secrets.token_urlsafe(32)
    row = MCPRefreshToken(
        token_hash=_hash_token(token),
        family_id=family_id or uuid.uuid4(),
        user_id=user_id,
        organization_id=organization_id,
        client_id=client_id,
        encrypted_metadata=_encode_metadata(metadata),
        status="active",
        expires_at=datetime.now(UTC)
        + timedelta(seconds=oidc_config.REFRESH_TOKEN_LIFETIME_SECONDS),
    )
    session.add(row)
    await session.commit()
    return token


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    token: str,
    client_id: str,
) -> tuple[RefreshTokenContext, str]:
    """Atomically consume and replace a refresh token.

    This keeps the replay-detection transition and replacement-token insertion
    in a single database transaction so concurrent replays cannot slip a fresh
    descendant token into the family after revocation. The caller owns the
    transaction boundary for successful rotations and is responsible for the
    final commit/rollback. Hostile replay and client-mismatch paths commit
    family revocation before raising so the security side effect survives any
    caller rollback.
    """
    token_hash = _hash_token(token)
    row = (
        await session.execute(
            select(MCPRefreshToken)
            .where(MCPRefreshToken.token_hash == token_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise RefreshTokenError("invalid_grant", "Refresh token is invalid or expired")

    if row.status == "used":
        family_id = row.family_id
        user_id = row.user_id
        await revoke_family(session, family_id)
        logger.warning(
            "MCP OIDC: refresh token replay detected, revoking family",
            family_id=str(family_id),
            user_id=str(user_id),
        )
        raise RefreshTokenError(
            "invalid_grant", "Refresh token replay detected; family revoked"
        )

    if row.status != "active":
        raise RefreshTokenError("invalid_grant", "Refresh token is invalid or expired")

    if datetime.now(UTC) >= row.expires_at:
        raise RefreshTokenError("invalid_grant", "Refresh token has expired")

    if row.client_id != client_id:
        family_id = row.family_id
        expected_client_id = row.client_id
        await revoke_family(session, family_id)
        logger.warning(
            "MCP OIDC: refresh token client_id mismatch, revoking family",
            family_id=str(family_id),
            expected=expected_client_id,
            actual=client_id,
        )
        raise RefreshTokenError("invalid_grant", "client_id mismatch")

    try:
        metadata = _decode_metadata(row.encrypted_metadata)
    except (InvalidToken, ValidationError, ValueError) as exc:
        logger.warning(
            "MCP OIDC: refresh token metadata decode failed",
            error=str(exc),
            family_id=str(row.family_id),
            user_id=str(row.user_id),
        )
        raise RefreshTokenError(
            "invalid_grant", "Refresh token is invalid or expired"
        ) from exc
    row.status = "used"
    new_refresh_token = secrets.token_urlsafe(32)
    session.add(
        MCPRefreshToken(
            token_hash=_hash_token(new_refresh_token),
            family_id=row.family_id,
            user_id=row.user_id,
            organization_id=row.organization_id,
            client_id=row.client_id,
            encrypted_metadata=row.encrypted_metadata,
            status="active",
            # Rotation uses a rolling expiry window: each successful refresh
            # renews the family for another REFRESH_TOKEN_LIFETIME_SECONDS.
            expires_at=datetime.now(UTC)
            + timedelta(seconds=oidc_config.REFRESH_TOKEN_LIFETIME_SECONDS),
        )
    )

    ctx = RefreshTokenContext(
        family_id=row.family_id,
        user_id=row.user_id,
        organization_id=row.organization_id,
        client_id=row.client_id,
        metadata=metadata,
    )
    if new_refresh_token is None:  # pragma: no cover - defensive
        raise RuntimeError("Refresh token rotation completed without a result")
    return ctx, new_refresh_token


async def consume_refresh_token(
    session: AsyncSession,
    *,
    token: str,
    client_id: str,
) -> RefreshTokenContext:
    """Atomically consume a refresh token, returning its session context.

    Raises:
        RefreshTokenError: token is unknown, expired, already consumed,
            revoked, or bound to a different client. On replay (presenting
            an already-consumed token) the entire token family is revoked
            before the error is raised.
    """
    token_hash = _hash_token(token)

    stmt = select(MCPRefreshToken).where(MCPRefreshToken.token_hash == token_hash)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise RefreshTokenError("invalid_grant", "Refresh token is invalid or expired")

    # Replay: a token already marked 'used' is being presented again.
    # Revoke the whole family — both the original chain and any rotated
    # successors — and reject this request.
    if row.status == "used":
        await revoke_family(session, row.family_id)
        logger.warning(
            "MCP OIDC: refresh token replay detected, revoking family",
            family_id=str(row.family_id),
            user_id=str(row.user_id),
        )
        raise RefreshTokenError(
            "invalid_grant", "Refresh token replay detected; family revoked"
        )

    if row.status != "active":
        raise RefreshTokenError("invalid_grant", "Refresh token is invalid or expired")

    if datetime.now(UTC) >= row.expires_at:
        raise RefreshTokenError("invalid_grant", "Refresh token has expired")

    if row.client_id != client_id:
        # Treat client mismatch as a hostile reuse attempt — revoke the
        # family rather than just refusing this one request.
        await revoke_family(session, row.family_id)
        logger.warning(
            "MCP OIDC: refresh token client_id mismatch, revoking family",
            family_id=str(row.family_id),
            expected=row.client_id,
            actual=client_id,
        )
        raise RefreshTokenError("invalid_grant", "client_id mismatch")

    # Atomic compare-and-swap from 'active' -> 'used'. If two requests
    # present the same token simultaneously, exactly one wins. Keep expiry
    # in the WHERE clause so a token cannot be consumed after its deadline.
    now = datetime.now(UTC)
    consumed_token_id = await session.scalar(
        update(MCPRefreshToken)
        .where(
            MCPRefreshToken.id == row.id,
            MCPRefreshToken.status == "active",
            MCPRefreshToken.client_id == client_id,
            MCPRefreshToken.expires_at > now,
        )
        .values(status="used")
        .returning(MCPRefreshToken.id)
    )
    if consumed_token_id is None:
        raise RefreshTokenError(
            "invalid_grant", "Refresh token was concurrently consumed"
        )
    await session.commit()

    return RefreshTokenContext(
        family_id=row.family_id,
        user_id=row.user_id,
        organization_id=row.organization_id,
        client_id=row.client_id,
        metadata=_decode_metadata(row.encrypted_metadata),
    )


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    """Mark every non-revoked token in a family as revoked."""
    await _revoke_family_rows(session, family_id)
    await session.commit()
