"""Tests for local email/password auth policy enforcement."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserNotExists
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


@pytest.fixture(autouse=True)
def patch_bypass_session_context_manager(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> None:
    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(
        "tracecat.auth.users.get_async_session_bypass_rls_context_manager",
        _session_cm,
    )

    async def _get_setting_with_test_session(*args, **kwargs):  # noqa: ANN002, ANN003
        from tracecat.settings.service import get_setting as get_setting_impl

        kwargs.setdefault("session", session)
        return await get_setting_impl(*args, **kwargs)

    monkeypatch.setattr(
        "tracecat.auth.users.get_setting",
        _get_setting_with_test_session,
    )
    monkeypatch.setattr(
        "tracecat.settings.service.get_db_encryption_key",
        lambda: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
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

    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=organization.id,
        )
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


@pytest.mark.parametrize(
    "case",
    ["basic-disabled", "unknown", "single-saml", "allowed", "cross-org"],
)
@pytest.mark.anyio
async def test_password_auth_policy_and_failure_audit_cases(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: str,
) -> None:
    password = "password-123456"
    user: User | None = None
    expected_org_ids: set[uuid.UUID] = set()
    email = "unknown@example.com"
    if case != "unknown":
        user, primary_org = await _create_user_with_org_membership(
            session,
            email=f"user@{case}.example.com",
            password=password,
            saml_enforced=case == "single-saml",
        )
        email = user.email
        if case == "single-saml":
            expected_org_ids.add(primary_org.id)
        if case == "cross-org":
            enforced_org = await _add_org_membership(
                session, user=user, domain="secure.example.com", saml_enforced=True
            )
            expected_org_ids.add(enforced_org.id)
    auth_types = (
        {AuthType.OIDC} if case == "basic-disabled" else {AuthType.BASIC, AuthType.SAML}
    )
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", auth_types)
    audit_mock = AsyncMock()
    monkeypatch.setattr(user_manager, "_emit_auth_failure_audit", audit_mock)

    authenticated = await user_manager.authenticate(
        OAuth2PasswordRequestForm(username=email, password=password)
    )

    assert (authenticated is not None) is (case == "allowed")
    if case == "allowed":
        assert authenticated is not None
        assert user is not None and authenticated.id == user.id
    reason = {
        "basic-disabled": "basic_auth_disabled",
        "single-saml": "saml_enforced",
        "cross-org": "saml_enforced",
    }.get(case)
    if reason:
        assert user is not None
        audit_mock.assert_awaited_once_with(
            user=user,
            auth_method="password",
            reason=reason,
            org_ids=expected_org_ids,
        )
    else:
        audit_mock.assert_not_awaited()


@pytest.mark.parametrize("case", ["membership", "domain-only", "unknown"])
@pytest.mark.anyio
async def test_oauth_policy_failure_audit_attribution_cases(
    session: AsyncSession,
    user_manager: UserManager,
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: str,
) -> None:
    user: User | None
    expected_org_ids: set[uuid.UUID]
    if case == "membership":
        user, organization = await _create_user_with_org_membership(
            session,
            email="user@oauth-membership.example.com",
            password="password-123456",
            saml_enforced=True,
        )
        expected_org_ids = {organization.id}
    else:
        user = (
            None
            if case == "unknown"
            else User(
                id=uuid.uuid4(),
                email="user@oauth-domain.example.com",
                hashed_password=None,
                is_active=True,
                is_verified=True,
                is_superuser=False,
                last_login_at=None,
            )
        )
        org_id = uuid.uuid4()
        monkeypatch.setattr(
            user_manager,
            "get_by_email",
            AsyncMock(return_value=user)
            if user
            else AsyncMock(side_effect=UserNotExists()),
        )
        monkeypatch.setattr(
            user_manager, "_list_user_org_ids", AsyncMock(return_value=set())
        )
        monkeypatch.setattr(
            user_manager, "_get_org_id_for_email_domain", AsyncMock(return_value=org_id)
        )
        monkeypatch.setattr(
            user_manager, "_is_org_saml_enforced", AsyncMock(return_value=True)
        )
        expected_org_ids = set()
    monkeypatch.setattr(config, "TRACECAT__AUTH_TYPES", {AuthType.OIDC, AuthType.SAML})
    monkeypatch.setattr(user_manager, "validate_email", AsyncMock())
    audit_mock = AsyncMock()
    monkeypatch.setattr(user_manager, "_emit_auth_failure_audit", audit_mock)
    email = user.email if user else "unknown@example.com"

    with pytest.raises(HTTPException) as exc_info:
        await user_manager.oauth_callback(
            oauth_name="okta",
            access_token="access-token",
            account_id="account-id",
            account_email=email,
        )

    assert exc_info.value.status_code == 403
    if user:
        audit_mock.assert_awaited_once_with(
            user=user,
            auth_method="okta",
            reason="saml_enforced",
            org_ids=expected_org_ids,
        )
    else:
        audit_mock.assert_not_awaited()
