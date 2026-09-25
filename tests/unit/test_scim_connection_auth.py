"""Unit tests for SCIM connection bearer authentication."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.scim.connections import ScimConnectionService
from tracecat_ee.scim.credentials import (
    SCIM_ROLE_SCOPES,
    authenticate_scim_connection,
)
from tracecat_ee.scim.service import SCIMService

from tracecat.auth import ip_allowlist_enforcement
from tracecat.auth.api_keys import SCIM_API_KEY_PREFIX
from tracecat.auth.ip_allowlist import OrgIPAllowlist, compile_allowlist
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.contexts import RequestAuditContext, ctx_request_audit
from tracecat.db.models import Organization, User
from tracecat.tiers import defaults as tier_defaults


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


@pytest.fixture(autouse=True)
def enable_rbac_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"rbac_addons": True}),
    )


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="SCIM Org", slug=f"scim-org-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    return org


@pytest.fixture
def auth_session(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the auth bulkhead session at the test session."""

    @asynccontextmanager
    async def _session_cm() -> AsyncIterator[AsyncSession]:
        yield session

    monkeypatch.setattr(
        "tracecat_ee.scim.credentials.get_async_session_auth_context_manager",
        _session_cm,
    )


@pytest.fixture
async def admin_user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
def admin_role(org: Organization, admin_user: User) -> Role:
    return Role(
        type="user",
        user_id=admin_user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=ORG_ADMIN_SCOPES,
    )


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.anyio
async def test_valid_token_yields_scim_role(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    auth_session: None,
) -> None:
    """A verified token resolves to a scim role bound to its organization."""
    issued = await ScimConnectionService(session, role=admin_role).issue_token()
    connection_id = issued.connection.id

    role = await authenticate_scim_connection(_bearer(issued.token))

    assert role.type == "scim"
    assert role.organization_id == org.id
    assert role.scim_connection_id == connection_id
    assert role.user_id is None
    # The connection id is the auditable actor, not a user.
    assert role.actor_id == connection_id
    assert role.scopes == SCIM_ROLE_SCOPES
    assert role.scopes is not None
    assert "org:scim:manage" not in role.scopes


@pytest.mark.anyio
async def test_scim_role_holds_only_the_removal_scope(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    auth_session: None,
) -> None:
    """The role reaches delete_member and nothing else."""
    issued = await ScimConnectionService(session, role=admin_role).issue_token()
    role = await authenticate_scim_connection(_bearer(issued.token))

    assert role.scopes is not None
    assert has_scope(role.scopes, "org:member:remove")
    for denied in (
        "org:member:invite",
        "org:rbac:create",
        "org:settings:update",
        "org:delete",
    ):
        assert not has_scope(role.scopes, denied), denied


@pytest.mark.anyio
async def test_missing_credentials_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        await authenticate_scim_connection(None)
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_malformed_token_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        await authenticate_scim_connection(_bearer("tc_org_sk_abc_def"))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_unknown_key_id_rejected(auth_session: None) -> None:
    token = f"{SCIM_API_KEY_PREFIX}{uuid.uuid4().hex[:12]}_{uuid.uuid4().hex}"
    with pytest.raises(HTTPException) as exc:
        await authenticate_scim_connection(_bearer(token))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_wrong_secret_rejected(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    auth_session: None,
) -> None:
    """A real key_id with the wrong secret fails hash verification."""
    issued = await ScimConnectionService(session, role=admin_role).issue_token()
    forged = f"{SCIM_API_KEY_PREFIX}{issued.connection.key_id}_{uuid.uuid4().hex}"

    with pytest.raises(HTTPException) as exc:
        await authenticate_scim_connection(_bearer(forged))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_disconnected_token_rejected(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    auth_session: None,
) -> None:
    service = ScimConnectionService(session, role=admin_role)
    issued = await service.issue_token()
    await SCIMService(session, role=admin_role).disconnect()

    with pytest.raises(HTTPException) as exc:
        await authenticate_scim_connection(_bearer(issued.token))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_unentitled_org_rejected(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    auth_session: None,
) -> None:
    """An organization without the RBAC entitlement cannot authenticate."""
    issued = await ScimConnectionService(session, role=admin_role).issue_token()
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(update={"rbac_addons": False}),
    )

    with pytest.raises(HTTPException) as exc:
        await authenticate_scim_connection(_bearer(issued.token))
    assert exc.value.status_code == 401


@pytest.mark.anyio
async def test_last_used_at_is_recorded(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    auth_session: None,
) -> None:
    service = ScimConnectionService(session, role=admin_role)
    issued = await service.issue_token()
    assert issued.connection.last_used_at is None

    await authenticate_scim_connection(_bearer(issued.token))

    await session.refresh(issued.connection)
    assert issued.connection.last_used_at is not None


@pytest.mark.anyio
async def test_org_ip_allowlist_applies_to_scim(
    session: AsyncSession,
    org: Organization,
    admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    auth_session: None,
) -> None:
    """An enforced allowlist rejects an IdP push from outside it, before use is recorded."""
    issued = await ScimConnectionService(session, role=admin_role).issue_token()

    async def allowlist(organization_id: uuid.UUID) -> OrgIPAllowlist:
        assert organization_id == org.id
        return compile_allowlist(enabled=True, cidrs=["203.0.113.0/24"])

    monkeypatch.setattr(ip_allowlist_enforcement, "get_org_ip_allowlist", allowlist)
    token = ctx_request_audit.set(
        RequestAuditContext(
            client_ip="198.51.100.9", user_agent=None, raw_user_agent=None
        )
    )
    try:
        with pytest.raises(HTTPException) as exc:
            await authenticate_scim_connection(_bearer(issued.token))
    finally:
        ctx_request_audit.reset(token)
    assert exc.value.status_code == 403
    await session.refresh(issued.connection)
    assert issued.connection.last_used_at is None
