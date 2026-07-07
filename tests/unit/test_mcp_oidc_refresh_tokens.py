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
from tracecat.mcp.oidc import config as oidc_config
from tracecat.mcp.oidc.refresh_tokens import (
    RefreshTokenError,
    _hash_token,
    consume_refresh_token,
    issue_refresh_token,
    revoke_family,
    rotate_refresh_token,
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
async def test_rotate_refresh_token_issues_replacement_and_marks_old_used(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    assert token_b
    assert token_b != token_a
    rows = (
        (
            await session.execute(
                select(MCPRefreshToken).where(
                    MCPRefreshToken.family_id == ctx.family_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    status_by_hash = {row.token_hash: row.status for row in rows}
    assert status_by_hash[_hash_token(token_a)] == "used"
    assert status_by_hash[_hash_token(token_b)] == "active"


@pytest.mark.anyio
async def test_rotate_refresh_token_renews_expiry_window(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
) -> None:
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    row_a = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token_a)
            )
        )
    ).scalar_one()
    original_expiry = datetime.now(UTC) + timedelta(minutes=5)
    row_a.expires_at = original_expiry
    await session.commit()

    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    row_b = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.family_id == ctx.family_id,
                MCPRefreshToken.token_hash == _hash_token(token_b),
            )
        )
    ).scalar_one()
    assert row_b.expires_at > original_expiry + timedelta(days=29)


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_revokes_replacement_token(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strict-mode contract: with the reuse grace window disabled, any replay
    # of a consumed token revokes the entire family.
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert "replay" in exc_info.value.description.lower()

    rows = (
        (
            await session.execute(
                select(MCPRefreshToken).where(
                    MCPRefreshToken.family_id == ctx.family_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert all(row.status == "revoked" for row in rows)

    with pytest.raises(RefreshTokenError):
        await consume_refresh_token(session, token=token_b, client_id=_TEST_CLIENT_ID)


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_revocation_survives_caller_rollback(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strict-mode contract: with the reuse grace window disabled, any replay
    # of a consumed token revokes the entire family.
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )
    await session.commit()

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert "replay" in exc_info.value.description.lower()

    await session.rollback()

    rows = (
        (
            await session.execute(
                select(MCPRefreshToken).where(
                    MCPRefreshToken.family_id == ctx.family_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert all(row.status == "revoked" for row in rows)

    with pytest.raises(RefreshTokenError):
        await consume_refresh_token(session, token=token_b, client_id=_TEST_CLIENT_ID)


# ---------------------------------------------------------------------------
# Replay grace window (REFRESH_TOKEN_REUSE_GRACE_SECONDS)
# ---------------------------------------------------------------------------


async def _family_rows(
    session: AsyncSession, family_id: uuid.UUID
) -> list[MCPRefreshToken]:
    """Return all refresh-token rows in a family."""
    result = await session.execute(
        select(MCPRefreshToken).where(MCPRefreshToken.family_id == family_id)
    )
    return list(result.scalars().all())


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_within_grace_issues_sibling(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A benign replay of the freshest used token within the grace window mints
    a fresh active sibling instead of revoking the family."""
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 60)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    # A -> B: A becomes 'used', B is the active successor.
    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    # Immediate replay of A (still the freshest used token) is treated as a
    # benign retry and mints a new active sibling C.
    _ctx_c, token_c = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )
    assert token_c
    assert token_c != token_b

    rows = await _family_rows(session, ctx.family_id)
    assert len(rows) == 3
    status_by_hash = {row.token_hash: row.status for row in rows}
    assert status_by_hash[_hash_token(token_a)] == "used"
    assert status_by_hash[_hash_token(token_b)] == "active"
    assert status_by_hash[_hash_token(token_c)] == "active"
    assert not any(row.status == "revoked" for row in rows)

    # The sibling is a working refresh token: rotating it succeeds.
    _ctx_d, token_d = await rotate_refresh_token(
        session, token=token_c, client_id=_TEST_CLIENT_ID
    )
    assert token_d
    assert token_d != token_c


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_outside_grace_revokes_family(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A used-token replay after the grace window has elapsed revokes the whole
    family, including the legitimate successor."""
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 60)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    # Age A's rotation timestamp well past the grace window. Explicit assignment
    # to updated_at suppresses the onupdate bump, so the backdated value sticks.
    grace = oidc_config.REFRESH_TOKEN_REUSE_GRACE_SECONDS
    row_a = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token_a)
            )
        )
    ).scalar_one()
    row_a.updated_at = datetime.now(UTC) - timedelta(seconds=grace + 3600)
    await session.commit()

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert exc_info.value.oauth_error == "invalid_grant"
    assert "replay" in exc_info.value.description.lower()

    rows = await _family_rows(session, ctx.family_id)
    assert len(rows) == 2
    assert all(row.status == "revoked" for row in rows)
    status_by_hash = {row.token_hash: row.status for row in rows}
    assert status_by_hash[_hash_token(token_b)] == "revoked"


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_strict_mode_revokes_family(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the grace window disabled (grace=0), any used-token replay revokes
    the entire family."""
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    ctx, _token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert exc_info.value.oauth_error == "invalid_grant"
    assert "replay" in exc_info.value.description.lower()

    rows = await _family_rows(session, ctx.family_id)
    assert len(rows) == 2
    assert all(row.status == "revoked" for row in rows)


@pytest.mark.anyio
async def test_rotate_refresh_token_used_token_client_mismatch_revokes_within_grace(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """client_id is verified before status handling, so a wrong-client replay of
    a used token revokes the family even inside the grace window."""
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 60)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    # A is now 'used' and still well within the grace window.
    ctx, _token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    # Presenting A from a different client is hostile: revoke, do not mint.
    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(
            session, token=token_a, client_id="some-other-client"
        )
    assert "client_id mismatch" in exc_info.value.description

    rows = await _family_rows(session, ctx.family_id)
    assert len(rows) == 2  # no sibling minted
    assert all(row.status == "revoked" for row in rows)


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_expired_used_within_grace_revokes(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A used token that sits inside the grace window but is past its own expiry
    is rejected (grace requires now < expires_at); no sibling is minted."""
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 60)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    ctx, _token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )

    # Freshly rotated (updated_at within grace) but already expired.
    now = datetime.now(UTC)
    row_a = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token_a)
            )
        )
    ).scalar_one()
    row_a.updated_at = now
    row_a.expires_at = now - timedelta(seconds=1)
    await session.commit()

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert exc_info.value.oauth_error == "invalid_grant"
    assert "replay" in exc_info.value.description.lower()

    rows = await _family_rows(session, ctx.family_id)
    assert len(rows) == 2  # no sibling minted
    assert all(row.status == "revoked" for row in rows)


@pytest.mark.anyio
async def test_rotate_refresh_token_replay_older_used_token_revokes_within_grace(
    session: AsyncSession,
    svc_organization: Organization,
    test_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaying a used token that has a younger used sibling (the family rotated
    again after it) revokes the family even inside the grace window."""
    monkeypatch.setattr(oidc_config, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 60)
    token_a = await issue_refresh_token(
        session,
        user_id=test_user.id,
        organization_id=svc_organization.id,
        client_id=_TEST_CLIENT_ID,
        metadata=_make_metadata(),
    )

    # A -> B -> C: A and B are both 'used', C is active.
    ctx, token_b = await rotate_refresh_token(
        session, token=token_a, client_id=_TEST_CLIENT_ID
    )
    _ctx_c, _token_c = await rotate_refresh_token(
        session, token=token_b, client_id=_TEST_CLIENT_ID
    )

    # func.now() is constant across a single test transaction, so set the
    # rotation timestamps explicitly: both used and within grace, but B was
    # rotated after A (B is the younger used sibling).
    now = datetime.now(UTC)
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
    row_a.updated_at = now - timedelta(seconds=10)
    row_b.updated_at = now - timedelta(seconds=5)
    await session.commit()

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token_a, client_id=_TEST_CLIENT_ID)
    assert "replay" in exc_info.value.description.lower()

    rows = await _family_rows(session, ctx.family_id)
    assert len(rows) == 3  # A, B, C — no fresh sibling minted
    assert all(row.status == "revoked" for row in rows)


@pytest.mark.anyio
async def test_rotate_refresh_token_client_mismatch_revocation_survives_caller_rollback(
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
        await rotate_refresh_token(session, token=token, client_id="some-other-client")
    assert "client_id mismatch" in exc_info.value.description

    await session.rollback()

    row = (
        await session.execute(
            select(MCPRefreshToken).where(
                MCPRefreshToken.token_hash == _hash_token(token)
            )
        )
    ).scalar_one()
    assert row.status == "revoked"


@pytest.mark.anyio
async def test_rotate_refresh_token_maps_metadata_decode_failure_to_invalid_grant(
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
    row.encrypted_metadata = b"corrupted"
    await session.commit()

    with pytest.raises(RefreshTokenError) as exc_info:
        await rotate_refresh_token(session, token=token, client_id=_TEST_CLIENT_ID)
    assert exc_info.value.oauth_error == "invalid_grant"
    assert "invalid or expired" in exc_info.value.description


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
