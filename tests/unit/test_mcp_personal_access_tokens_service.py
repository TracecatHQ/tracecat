"""Tests for MCP personal access token service behavior."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tracecat.mcp.personal_access_tokens.service as mcp_pat_service
from tracecat.auth.api_keys import generate_managed_api_key
from tracecat.auth.credentials import _authenticate_api_key
from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPPersonalAccessToken,
    Membership,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.mcp.personal_access_tokens.constants import MCP_PAT_PREFIX
from tracecat.mcp.personal_access_tokens.service import (
    MCPPersonalAccessTokenService,
)
from tracecat.pagination import CursorPaginationParams

pytestmark = pytest.mark.anyio


async def _create_user_org_workspace(
    session: AsyncSession,
    *,
    org_membership: bool = True,
    workspace_membership: bool = True,
) -> tuple[User, Organization, Workspace]:
    test_id = uuid.uuid4()
    user = User(
        id=uuid.uuid4(),
        email=f"mcp-pat-{test_id.hex}@example.com",
        hashed_password="not-used",
        role=UserRole.BASIC,
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    organization = Organization(
        id=uuid.uuid4(),
        name=f"MCP PAT test {test_id.hex}",
        slug=f"mcp-pat-test-{test_id.hex}",
        is_active=True,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"MCP PAT workspace {test_id.hex}",
    )
    session.add_all([user, organization, workspace])
    await session.flush()

    if org_membership:
        session.add(
            OrganizationMembership(
                user_id=user.id,
                organization_id=organization.id,
            )
        )
    if workspace_membership:
        session.add(Membership(user_id=user.id, workspace_id=workspace.id))
    await session.commit()
    return user, organization, workspace


def _role_for(user: User, organization: Organization) -> Role:
    return Role(
        type="user",
        user_id=user.id,
        organization_id=organization.id,
        service_id="tracecat-api",
        scopes=frozenset({"org:read", "org:workspace:read"}),
    )


async def _store_token(
    session: AsyncSession,
    *,
    user: User,
    organization: Organization,
    workspace_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> tuple[MCPPersonalAccessToken, str]:
    generated = generate_managed_api_key(prefix=MCP_PAT_PREFIX)
    token = MCPPersonalAccessToken(
        id=uuid.uuid4(),
        user_id=user.id,
        organization_id=organization.id,
        workspace_id=workspace_id,
        name="Claude Desktop",
        key_id=generated.key_id,
        hashed=generated.hashed,
        salt=generated.salt_b64,
        preview=generated.preview(),
        expires_at=expires_at,
        created_by=user.id,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return token, generated.raw


def _use_verifier_session(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    @asynccontextmanager
    async def same_session() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        mcp_pat_service,
        "get_async_session_bypass_rls_context_manager",
        same_session,
    )


async def test_create_list_and_revoke_mcp_personal_access_token(
    session: AsyncSession,
) -> None:
    user, organization, workspace = await _create_user_org_workspace(session)
    service = MCPPersonalAccessTokenService(
        session,
        role=_role_for(user, organization),
    )

    issued = await service.create_token(
        name="Claude Desktop",
        workspace_id=workspace.id,
        expires_at=None,
    )

    assert issued.raw_token.startswith(MCP_PAT_PREFIX)
    assert issued.token.user_id == user.id
    assert issued.token.organization_id == organization.id
    assert issued.token.workspace_id == workspace.id

    page = await service.list_tokens(CursorPaginationParams(limit=10))
    assert [token.id for token in page.items] == [issued.token.id]

    await service.revoke_token(issued.token.id)
    await session.refresh(issued.token)
    assert issued.token.revoked_at is not None
    assert issued.token.revoked_by == user.id


async def test_create_mcp_personal_access_token_allows_org_member_without_workspace_membership(
    session: AsyncSession,
) -> None:
    user, organization, workspace = await _create_user_org_workspace(
        session,
        workspace_membership=False,
    )
    service = MCPPersonalAccessTokenService(
        session,
        role=_role_for(user, organization),
    )

    issued = await service.create_token(
        name="Claude Desktop",
        workspace_id=workspace.id,
        expires_at=None,
    )

    assert issued.token.workspace_id == workspace.id


async def test_create_mcp_personal_access_token_rejects_workspace_in_other_org(
    session: AsyncSession,
) -> None:
    user, organization, _workspace = await _create_user_org_workspace(session)
    other_org = Organization(
        id=uuid.uuid4(),
        name="Other org",
        slug=f"other-org-{uuid.uuid4().hex}",
        is_active=True,
    )
    other_workspace = Workspace(
        id=uuid.uuid4(),
        organization_id=other_org.id,
        name="Other workspace",
    )
    session.add_all([other_org, other_workspace])
    await session.commit()
    service = MCPPersonalAccessTokenService(
        session,
        role=_role_for(user, organization),
    )

    with pytest.raises(
        TracecatAuthorizationError, match="User cannot access workspace"
    ):
        await service.create_token(
            name="Claude Desktop",
            workspace_id=other_workspace.id,
            expires_at=None,
        )


async def test_verify_mcp_personal_access_token_updates_last_used(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    _use_verifier_session(monkeypatch, session)
    user, organization, workspace = await _create_user_org_workspace(session)
    token, raw_token = await _store_token(
        session,
        user=user,
        organization=organization,
        workspace_id=workspace.id,
    )

    identity = await mcp_pat_service.verify_mcp_personal_access_token(raw_token)

    assert identity is not None
    assert identity.user_id == user.id
    assert identity.email == user.email
    assert identity.organization_id == organization.id
    assert identity.workspace_id == workspace.id

    refreshed = await session.scalar(
        select(MCPPersonalAccessToken).where(MCPPersonalAccessToken.id == token.id)
    )
    assert refreshed is not None
    assert refreshed.last_used_at is not None


async def test_verify_mcp_personal_access_token_rejects_revoked_token(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    _use_verifier_session(monkeypatch, session)
    user, organization, _workspace = await _create_user_org_workspace(session)
    token, raw_token = await _store_token(
        session,
        user=user,
        organization=organization,
    )
    token.revoked_at = datetime.now(UTC)
    await session.commit()

    assert await mcp_pat_service.verify_mcp_personal_access_token(raw_token) is None


async def test_verify_mcp_personal_access_token_rejects_expired_token(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    _use_verifier_session(monkeypatch, session)
    user, organization, _workspace = await _create_user_org_workspace(session)
    _token, raw_token = await _store_token(
        session,
        user=user,
        organization=organization,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    assert await mcp_pat_service.verify_mcp_personal_access_token(raw_token) is None


async def test_verify_mcp_personal_access_token_rejects_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    _use_verifier_session(monkeypatch, session)
    user, organization, _workspace = await _create_user_org_workspace(session)
    _token, raw_token = await _store_token(
        session,
        user=user,
        organization=organization,
    )

    assert (
        await mcp_pat_service.verify_mcp_personal_access_token(f"{raw_token}wrong")
        is None
    )


async def test_verify_mcp_personal_access_token_requires_org_membership(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    _use_verifier_session(monkeypatch, session)
    user, organization, _workspace = await _create_user_org_workspace(
        session,
        org_membership=False,
    )
    _token, raw_token = await _store_token(
        session,
        user=user,
        organization=organization,
    )

    assert await mcp_pat_service.verify_mcp_personal_access_token(raw_token) is None


async def test_regular_api_key_auth_ignores_mcp_personal_access_tokens(
    session: AsyncSession,
) -> None:
    user, organization, _workspace = await _create_user_org_workspace(session)
    _token, raw_token = await _store_token(
        session,
        user=user,
        organization=organization,
    )

    assert await _authenticate_api_key(api_key=raw_token, workspace_id=None) is None
