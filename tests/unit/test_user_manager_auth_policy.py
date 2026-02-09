"""Tests for local email/password auth policy enforcement."""

from __future__ import annotations

import uuid

import pytest
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.password import PasswordHelper
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.api.common import bootstrap_role
from tracecat.auth.enums import AuthType
from tracecat.auth.users import UserManager
from tracecat.db.models import (
    OAuthAccount,
    Organization,
    OrganizationDomain,
    OrganizationMembership,
    User,
)
from tracecat.organization.domains import normalize_domain
from tracecat.settings.schemas import SAMLSettingsUpdate
from tracecat.settings.service import SettingsService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def user_manager(session: AsyncSession) -> UserManager:
    user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
    return UserManager(user_db)


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

    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=organization.id,
        )
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
