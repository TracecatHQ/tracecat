"""Who must use a security key, and whether a session has satisfied it.

Policy is evaluated per session rather than per login route, so password,
OIDC, and SAML sign-ins are all covered: every interactive session is read
through ``SessionMetadataDatabaseStrategy.read_token``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import orjson
from async_lru import alru_cache
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.mfa.constants import (
    MFA_POLICY_CACHE_TTL_SECONDS,
    ORG_MFA_REQUIRED_KEY,
    PLATFORM_ADMIN_MFA_REQUIRED_KEY,
)
from tracecat.db.engine import get_async_session_auth_context_manager
from tracecat.db.models import (
    AccessToken,
    OrganizationMembership,
    PlatformSetting,
    User,
    WebAuthnCredential,
)
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.settings.service import get_setting_from_bypass_session


class SessionMfaState(StrEnum):
    NOT_REQUIRED = "not_required"
    """No security key is enrolled and no policy requires one."""
    VERIFIED = "verified"
    """The session completed a security key or recovery code challenge."""
    CHALLENGE_REQUIRED = "challenge_required"
    """The user has a security key and must use it before continuing."""
    ENROLLMENT_REQUIRED = "enrollment_required"
    """A policy requires a security key and the user has none yet."""

    @property
    def is_pending(self) -> bool:
        return self in (
            SessionMfaState.CHALLENGE_REQUIRED,
            SessionMfaState.ENROLLMENT_REQUIRED,
        )


@alru_cache(ttl=MFA_POLICY_CACHE_TTL_SECONDS)
async def org_requires_mfa(organization_id: OrganizationID) -> bool:
    async with get_async_session_auth_context_manager() as session:
        value = await get_setting_from_bypass_session(
            ORG_MFA_REQUIRED_KEY,
            organization_id=organization_id,
            session=session,
            default=False,
        )
    return value is True


@alru_cache(ttl=MFA_POLICY_CACHE_TTL_SECONDS)
async def platform_admin_requires_mfa() -> bool:
    async with get_async_session_auth_context_manager() as session:
        result = await session.execute(
            select(PlatformSetting.value).where(
                PlatformSetting.key == PLATFORM_ADMIN_MFA_REQUIRED_KEY
            )
        )
        raw = result.scalar_one_or_none()
    if raw is None:
        return False
    try:
        return orjson.loads(raw) is True
    except orjson.JSONDecodeError:
        logger.warning("Ignoring malformed platform admin MFA setting")
        return False


@alru_cache(ttl=MFA_POLICY_CACHE_TTL_SECONDS, maxsize=4096)
async def _user_organization_ids(user_id: uuid.UUID) -> tuple[OrganizationID, ...]:
    async with get_async_session_auth_context_manager() as session:
        result = await session.execute(
            select(OrganizationMembership.organization_id).where(
                OrganizationMembership.user_id == user_id
            )
        )
        return tuple(result.scalars().all())


def clear_mfa_policy_cache() -> None:
    """Make committed policy or membership changes visible to the next request."""
    org_requires_mfa.cache_clear()
    platform_admin_requires_mfa.cache_clear()
    _user_organization_ids.cache_clear()


async def mfa_required_by_policy(user: User) -> bool:
    """Whether any policy applying to ``user`` requires a security key.

    A user in several organizations must use a security key at sign-in if any
    one of them requires it.
    """
    if user.is_superuser and await platform_admin_requires_mfa():
        return True
    for organization_id in await _user_organization_ids(user.id):
        if await org_requires_mfa(organization_id):
            return True
    return False


def platform_admin_mfa_bypassed(user: User) -> bool:
    return user.is_superuser and config.TRACECAT__PLATFORM_ADMIN_MFA_BYPASS


async def user_has_credentials(session: AsyncSession, user_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(exists().where(WebAuthnCredential.user_id == user_id))
    )
    return bool(result.scalar())


async def get_session_mfa_state(
    session: AsyncSession, user: User, access_token: AccessToken
) -> SessionMfaState:
    if access_token.mfa_verified_at is not None:
        return SessionMfaState.VERIFIED
    if platform_admin_mfa_bypassed(user):
        return SessionMfaState.NOT_REQUIRED
    if await user_has_credentials(session, user.id):
        return SessionMfaState.CHALLENGE_REQUIRED
    if await mfa_required_by_policy(user):
        return SessionMfaState.ENROLLMENT_REQUIRED
    return SessionMfaState.NOT_REQUIRED


def is_recent(
    verified_at: datetime | None, *, max_age_seconds: int, now: datetime | None = None
) -> bool:
    if verified_at is None:
        return False
    now = now or datetime.now(UTC)
    return now - verified_at <= timedelta(seconds=max_age_seconds)


async def ensure_can_enable_mfa_policy(
    session: AsyncSession, user_id: uuid.UUID
) -> None:
    """Reject enabling an MFA policy the caller could not satisfy themselves.

    Raises:
        TracecatValidationError: WebAuthn is unavailable on this deployment or
            the caller has no security key.
    """
    from tracecat.auth.mfa.webauthn import webauthn_unavailable_reason

    if reason := webauthn_unavailable_reason():
        raise TracecatValidationError(reason)
    if not await user_has_credentials(session, user_id):
        raise TracecatValidationError(
            "Register a security key in Settings > Security before requiring one."
        )
