"""Tests for local email/password auth policy enforcement."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.membership import (
    grant_org_membership,
    grant_workspace_membership,
)
from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.auth.enums import AuthType
from tracecat.auth.users import UserManager
from tracecat.authz.enums import ScimConnectionStatus
from tracecat.db.models import (
    ExternalUser,
    OAuthAccount,
    Organization,
    OrganizationDomain,
    OrganizationMembership,
    ScimConnection,
    User,
    Workspace,
)
from tracecat.organization.domains import normalize_domain
from tracecat.settings.schemas import SAMLSettingsUpdate
from tracecat.settings.service import SettingsService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def user_manager(session: AsyncSession) -> UserManager:
    user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
    return UserManager(user_db)


@pytest.fixture(autouse=True)
def patch_auth_session_context_manager(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )

    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(
        "tracecat.auth.users.get_async_session_auth_context_manager",
        _session_cm,
    )


async def _create_user_with_org_membership(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    saml_enforced: bool,
) -> tuple[User, Organization]:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.flush()

    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=PasswordHelper().hash(password),
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.flush()

    await grant_org_membership(
        session, user_id=user.id, organization_id=organization.id
    )

    normalized_domain = normalize_domain(email.rpartition("@")[2])
    session.add(
        OrganizationDomain(
            id=uuid.uuid4(),
            organization_id=organization.id,
            domain=normalized_domain.domain,
            normalized_domain=normalized_domain.normalized_domain,
            is_primary=True,
            is_active=True,
            verification_method="platform_admin",
        )
    )
    await session.commit()

    settings_service = SettingsService(session, role=bootstrap_role(organization.id))
    await settings_service.init_default_settings()
    await settings_service.update_saml_settings(
        SAMLSettingsUpdate(
            saml_enabled=True,
            saml_enforced=saml_enforced,
        )
    )
    await session.commit()
    return user, organization


async def _add_org_membership(
    session: AsyncSession,
    *,
    user: User,
    domain: str,
    saml_enforced: bool,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.flush()

    await grant_org_membership(
        session, user_id=user.id, organization_id=organization.id
    )

    normalized_domain = normalize_domain(domain)
    session.add(
        OrganizationDomain(
            id=uuid.uuid4(),
            organization_id=organization.id,
            domain=normalized_domain.domain,
            normalized_domain=normalized_domain.normalized_domain,
            is_primary=True,
            is_active=True,
            verification_method="platform_admin",
        )
    )
    await session.commit()

    settings_service = SettingsService(session, role=bootstrap_role(organization.id))
    await settings_service.init_default_settings()
    await settings_service.update_saml_settings(
        SAMLSettingsUpdate(
            saml_enabled=True,
            saml_enforced=saml_enforced,
        )
    )
    await session.commit()
    return organization


@pytest.mark.anyio
async def test_authenticate_rejects_password_when_platform_basic_disabled(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _create_user_with_org_membership(
        session,
        email="user@acme-basic-disabled.com",
        password="password-123456",
        saml_enforced=False,
    )
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.OIDC})

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is None


@pytest.mark.anyio
async def test_authenticate_rejects_password_for_saml_enforced_org(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _create_user_with_org_membership(
        session,
        email="user@acme-saml.com",
        password="password-123456",
        saml_enforced=True,
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is None


@pytest.mark.anyio
async def test_authenticate_allows_password_when_basic_enabled_and_not_saml_enforced(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _create_user_with_org_membership(
        session,
        email="user@acme-basic.com",
        password="password-123456",
        saml_enforced=False,
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is not None
    assert authenticated_user.id == user.id


@pytest.mark.anyio
async def test_authenticate_rejects_password_when_any_membership_enforces_saml(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _create_user_with_org_membership(
        session,
        email="user@acme-basic.com",
        password="password-123456",
        saml_enforced=False,
    )
    await _add_org_membership(
        session,
        user=user,
        domain="secure-acme.com",
        saml_enforced=True,
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is None


@pytest.mark.anyio
async def test_authenticate_rejects_password_for_workspace_only_saml_org(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace-scoped path alone binds the user to the org's login policy."""
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.flush()

    email = "user@acme-workspace.com"
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=PasswordHelper().hash("password-123456"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Acme Workspace",
        organization_id=organization.id,
    )
    session.add_all([user, workspace])
    await session.flush()

    await grant_workspace_membership(
        session,
        user_id=user.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )

    normalized_domain = normalize_domain(email.rpartition("@")[2])
    session.add(
        OrganizationDomain(
            id=uuid.uuid4(),
            organization_id=organization.id,
            domain=normalized_domain.domain,
            normalized_domain=normalized_domain.normalized_domain,
            is_primary=True,
            is_active=True,
            verification_method="platform_admin",
        )
    )
    await session.commit()

    settings_service = SettingsService(session, role=bootstrap_role(organization.id))
    await settings_service.init_default_settings()
    await settings_service.update_saml_settings(
        SAMLSettingsUpdate(saml_enabled=True, saml_enforced=True)
    )
    await session.commit()

    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is None


async def _link_external_user(
    session: AsyncSession, *, user: User, organization: Organization
) -> ExternalUser:
    external_user = ExternalUser(
        id=uuid.uuid4(),
        organization_id=organization.id,
        user_id=user.id,
        external_id=uuid.uuid4().hex,
    )
    session.add(external_user)
    session.add(
        ScimConnection(
            organization_id=organization.id,
            key_id=uuid.uuid4().hex,
            hashed="x",
            salt="x",
            preview="scim_test",
            status=ScimConnectionStatus.ACTIVE,
        )
    )
    await session.commit()
    return external_user


@pytest.mark.anyio
async def test_authenticate_rejects_password_for_scim_provisioned_user(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, organization = await _create_user_with_org_membership(
        session,
        email="user@acme-scim.com",
        password="password-123456",
        saml_enforced=False,
    )
    await _link_external_user(session, user=user, organization=organization)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is None


@pytest.mark.anyio
async def test_authenticate_allows_password_without_external_user_row(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _create_user_with_org_membership(
        session,
        email="user@acme-no-scim.com",
        password="password-123456",
        saml_enforced=False,
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )

    authenticated_user = await user_manager.authenticate(
        OAuth2PasswordRequestForm(
            username=user.email,
            password="password-123456",
        )
    )

    assert authenticated_user is not None
    assert authenticated_user.id == user.id


@pytest.mark.anyio
async def test_forgot_password_blocked_for_scim_provisioned_user(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, organization = await _create_user_with_org_membership(
        session,
        email="user@acme-scim-reset.com",
        password="password-123456",
        saml_enforced=False,
    )
    await _link_external_user(session, user=user, organization=organization)
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )
    on_after = AsyncMock()
    monkeypatch.setattr(user_manager, "on_after_forgot_password", on_after)

    await user_manager.forgot_password(user)

    on_after.assert_not_awaited()


@pytest.mark.anyio
async def test_forgot_password_allowed_without_external_user_row(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = await _create_user_with_org_membership(
        session,
        email="user@acme-no-scim-reset.com",
        password="password-123456",
        saml_enforced=False,
    )
    monkeypatch.setattr(
        config,
        "TRACECAT__AUTH_TYPES",
        {AuthType.BASIC, AuthType.SAML},
    )
    on_after = AsyncMock()
    monkeypatch.setattr(user_manager, "on_after_forgot_password", on_after)

    await user_manager.forgot_password(user)

    on_after.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["pending", "inactive", "unadmitted"])
async def test_historical_scim_link_does_not_block_password_or_reset(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    user, org = await _create_user_with_org_membership(
        session,
        email="historical@example.com",
        password="password-123456",
        saml_enforced=False,
    )
    external = await _link_external_user(session, user=user, organization=org)
    peer = Organization(
        id=uuid.uuid4(), name="Basic auth peer", slug=f"peer-{uuid.uuid4().hex}"
    )
    session.add(peer)
    await session.flush()
    await grant_org_membership(session, user_id=user.id, organization_id=peer.id)
    if state == "pending":
        connection = (
            await session.execute(
                select(ScimConnection).where(ScimConnection.organization_id == org.id)
            )
        ).scalar_one()
        connection.status = ScimConnectionStatus.PENDING
    elif state == "inactive":
        external.active = False
    else:
        await session.execute(
            delete(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id
            )
        )
    await session.commit()
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.BASIC, AuthType.SAML})
    assert (
        await user_manager.authenticate(
            OAuth2PasswordRequestForm(username=user.email, password="password-123456")
        )
        is not None
    )
    after_reset = AsyncMock()
    monkeypatch.setattr(user_manager, "on_after_forgot_password", after_reset)
    await user_manager.forgot_password(user)
    after_reset.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("auth_types", "saml_enabled", "allowed"),
    [
        ({AuthType.BASIC}, True, True),
        ({AuthType.BASIC, AuthType.SAML}, False, True),
        ({AuthType.BASIC, AuthType.SAML}, True, False),
        ({AuthType.BASIC, AuthType.OIDC}, False, False),
        ({AuthType.SAML}, False, False),
    ],
)
async def test_scim_password_policy_requires_available_external_login(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
    auth_types: set[AuthType],
    saml_enabled: bool,
    allowed: bool,
) -> None:
    user, org = await _create_user_with_org_membership(
        session,
        email="scim-login@example.com",
        password="password-123456",
        saml_enforced=False,
    )
    await _link_external_user(session, user=user, organization=org)
    settings = SettingsService(session, role=bootstrap_role(org.id))
    await settings.update_saml_settings(SAMLSettingsUpdate(saml_enabled=saml_enabled))
    await session.commit()
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", auth_types)
    on_after = AsyncMock()
    monkeypatch.setattr(user_manager, "on_after_forgot_password", on_after)

    result = await user_manager.authenticate(
        OAuth2PasswordRequestForm(username=user.email, password="password-123456")
    )
    assert (result is not None) is allowed
    await user_manager.forgot_password(user)
    assert on_after.await_count == int(allowed)


@pytest.mark.anyio
async def test_saml_would_not_admit_off_env_allowlist_domain(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-tenant with no org domains still honours the env allowlist."""
    organization = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(organization)
    await session.commit()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(config, "TRACECAT__AUTH_ALLOWED_DOMAINS", {"acme.com"})

    admitted = await user_manager._saml_would_admit_email(
        session, organization.id, "user@other.com"
    )

    assert admitted is False
