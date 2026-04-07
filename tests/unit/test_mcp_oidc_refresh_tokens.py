"""Tests for tracecat.mcp.oidc.refresh_tokens — Postgres-backed refresh tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.db.models import MCPRefreshToken, Organization, User
from tracecat.mcp.oidc.refresh_tokens import (
    RefreshTokenError,
    _hash_token,
    consume_refresh_token,
    issue_refresh_token,
    revoke_family,
)
from tracecat.mcp.oidc.schemas import RefreshTokenMetadata

pytestmark = pytest.mark.usefixtures("db")

_TEST_CLIENT_ID = "tracecat-mcp-oidc-internal"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set up Fernet encryption key for the metadata blob."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "TRACECAT__DB_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
async def test_user(session: AsyncSession, svc_organization: Organization) -> User:
    """Create a test user for refresh token tests."""
    user = User(
        id=uuid.uuid4(),
        email="refresh-test@example.com",
        hashed_password="hashed_password_placeholder",
        last_login_at=None,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _make_metadata(**overrides) -> RefreshTokenMetadata:
    defaults = {
        "email": "refresh-test@example.com",
        "is_platform_superuser": False,
        "scope": "openid profile email offline_access",
        "resource": "https://app.example.com/mcp",
    }
    return RefreshTokenMetadata(**(defaults | overrides))


# ---------------------------------------------------------------------------
# issue_refresh_token
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_issue_refresh_token_creates_active_row(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    metadata = _make_metadata()

    token = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=metadata,
    )

    assert token  # opaque, non-empty
    row = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token)
            )
        )
    ).scalar_one()
    assert row.status == "active"
    assert row.user_id == test_user.id
    assert row.organization_id == svc_organization.id
    assert row.client_id == _TEST_CLIENT_ID
    assert row.expires_at > datetime.now(UTC) + timedelta(days=29)


@pytest.mark.anyio
async def test_issue_refresh_token_does_not_store_plaintext(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    """The plaintext token must never be persisted — only its hash."""
    token = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    rows = (await session.execute(select(MCPRefreshToken))).scalars().all()
    for row in rows:
        assert row.token_hash != token
        assert token.encode() not in row.encrypted_metadata


# ---------------------------------------------------------------------------
# consume_refresh_token — happy path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consume_refresh_token_returns_context_and_marks_used(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    metadata = _make_metadata()
    token = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=metadata,
    )

    ctx = await consume_refresh_token(session, token=token, client_id=_TEST_CLIENT_ID)

    assert ctx.user_id == test_user.id
    assert ctx.organization_id == svc_organization.id
    assert ctx.client_id == _TEST_CLIENT_ID
    assert ctx.metadata.email == metadata.email
    assert ctx.metadata.scope == metadata.scope

    # Underlying row should now be 'used'.
    row = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token)
            )
        )
    ).scalar_one()
    assert row.status == "used"


# ---------------------------------------------------------------------------
# consume_refresh_token — failure modes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consume_refresh_token_rejects_unknown(
    session: AsyncSession,
) -> None:
    with pytest.raises(RefreshTokenError) as exc_info:
        await consume_refresh_token(
            session, token="never-issued-token", client_id=_TEST_CLIENT_ID
        )
    assert exc_info.value.oauth_error == "invalid_grant"


@pytest.mark.anyio
async def test_consume_refresh_token_rejects_expired(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    token = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    # Backdate expiry by hand.
    row = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token)
            )
        )
    ).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    with pytest.raises(RefreshTokenError) as exc_info:
        await consume_refresh_token(session, token=token, client_id=_TEST_CLIENT_ID)
    assert exc_info.value.oauth_error == "invalid_grant"
    assert "expired" in exc_info.value.description.lower()


@pytest.mark.anyio
async def test_consume_refresh_token_rejects_client_id_mismatch_and_revokes_family(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    token = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    with pytest.raises(RefreshTokenError) as exc_info:
        await consume_refresh_token(session, token=token, client_id="some-other-client")
    assert exc_info.value.oauth_error == "invalid_grant"

    # The token's family should now be revoked.
    row = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token)
            )
        )
    ).scalar_one()
    assert row.status == "revoked"


# ---------------------------------------------------------------------------
# Replay detection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_consume_refresh_token_replay_revokes_entire_family(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    """Reusing an already-consumed token revokes the whole family."""
    metadata = _make_metadata()
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=metadata,
    )

    # Normal rotation: A -> B
    ctx_a = await consume_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )
    token_b = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=metadata,
        family_id=ctx_a.family_id,
    )

    # Attacker presents A again — replay.
    with pytest.raises(RefreshTokenError) as exc_info:
        await consume_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert "replay" in exc_info.value.description.lower()

    # Both A and B should now be revoked.
    rows = (
        (
            await session.execute(
                select(MCPRefreshToken).where(
                    MCPRefreshToken.family_id == ctx_a.family_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert all(row.status == "revoked" for row in rows)

    # B is no longer usable even by the legitimate client.
    with pytest.raises(RefreshTokenError):
        await consume_refresh_token(session, token=token_b, client_id=_TEST_CLIENT_ID)


@pytest.mark.anyio
async def test_consume_refresh_token_rejects_already_revoked(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    token = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    row = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token)
            )
        )
    ).scalar_one()
    await revoke_family(session, row.family_id)

    with pytest.raises(RefreshTokenError) as exc_info:
        await consume_refresh_token(session, token=token, client_id=_TEST_CLIENT_ID)
    assert exc_info.value.oauth_error == "invalid_grant"


# ---------------------------------------------------------------------------
# revoke_family
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_revoke_family_marks_all_family_tokens(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    metadata = _make_metadata()
    family_id = uuid.uuid4()
    for _ in range(3):
        await issue_refresh_token(
            session,
            user_id=test_user.id,
            organization_id=svc_organization.id,
            client_id=_TEST_CLIENT_ID,
            metadata=metadata,
            family_id=family_id,
        )

    await revoke_family(session, family_id)

    rows = (
        (
            await session.execute(
                select(MCPRefreshToken).where(MCPRefreshToken.family_id == family_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3
    assert all(row.status == "revoked" for row in rows)


@pytest.mark.anyio
async def test_revoke_family_does_not_touch_other_families(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    metadata = _make_metadata()
    family_a = uuid.uuid4()
    family_b = uuid.uuid4()

    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=metadata,
        family_id=family_a,
    )
    token_b = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=metadata,
        family_id=family_b,
    )

    await revoke_family(session, family_a)

    row_a = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token_a)
            )
        )
    ).scalar_one()
    row_b = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token_b)
            )
        )
    ).scalar_one()
    assert row_a.status == "revoked"
    assert row_b.status == "active"
