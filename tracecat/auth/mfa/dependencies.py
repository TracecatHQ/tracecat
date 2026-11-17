"""FastAPI dependencies for MFA sessions and step-up verification.

``require_step_up`` is the reusable building block: any route can demand a
security key verification within the last N seconds by depending on it. The
platform admin API composes it into ``SuperuserStepUpRole``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.mfa.constants import (
    MFA_RECENT_VERIFICATION_REQUIRED_CODE,
    MFA_STEP_UP_REQUIRED_CODE,
)
from tracecat.auth.mfa.policy import (
    SessionMfaState,
    get_session_mfa_state,
    has_recent_mfa,
    is_recent,
    platform_admin_mfa_bypassed,
    user_has_credentials,
)
from tracecat.auth.users import cookie_transport
from tracecat.db.engine import get_async_session
from tracecat.db.models import AccessToken, User


@dataclass(slots=True)
class MfaSession:
    """The cookie session behind a request, including MFA-pending sessions."""

    user: User
    access_token: AccessToken
    state: SessionMfaState
    db: AsyncSession


async def _load_mfa_session(request: Request, db: AsyncSession) -> MfaSession:
    token = request.cookies.get(cookie_transport.cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    max_age = datetime.now(UTC) - timedelta(seconds=config.SESSION_EXPIRE_TIME_SECONDS)
    access_token = await SQLAlchemyAccessTokenDatabase(db, AccessToken).get_by_token(
        token, max_age
    )
    if access_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    user = await db.get(User, access_token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    state = await get_session_mfa_state(db, user, access_token)
    return MfaSession(user=user, access_token=access_token, state=state, db=db)


async def get_mfa_session(
    request: Request, db: Annotated[AsyncSession, Depends(get_async_session)]
) -> MfaSession:
    """Any live cookie session, including one still waiting on MFA."""
    return await _load_mfa_session(request, db)


async def get_verified_mfa_session(
    request: Request, db: Annotated[AsyncSession, Depends(get_async_session)]
) -> MfaSession:
    """A fully signed-in cookie session (MFA satisfied or not required)."""
    mfa_session = await _load_mfa_session(request, db)
    if mfa_session.state.is_pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return mfa_session


AnyMfaSession = Annotated[MfaSession, Depends(get_mfa_session)]
VerifiedMfaSession = Annotated[MfaSession, Depends(get_verified_mfa_session)]


def step_up_expires_at(
    access_token: AccessToken, max_age_seconds: int
) -> datetime | None:
    if access_token.step_up_verified_at is None:
        return None
    return access_token.step_up_verified_at + timedelta(seconds=max_age_seconds)


async def enforce_step_up(
    mfa_session: MfaSession,
    *,
    max_age_seconds: int | None = None,
    allow_platform_admin_bypass: bool = False,
) -> None:
    """Require a security key verification within ``max_age_seconds``.

    Users with no security key enrolled pass, so enabling step-up on a route
    never locks out accounts that have not opted in and are not covered by a
    policy (policy-covered users cannot reach here without a key).

    Raises:
        HTTPException(403): ``detail.code == "mfa_step_up_required"``. Clients
            verify a security key via ``/auth/mfa/webauthn/authenticate`` and retry.
    """
    if allow_platform_admin_bypass and platform_admin_mfa_bypassed(mfa_session.user):
        return
    max_age = max_age_seconds or config.TRACECAT__MFA_STEP_UP_MAX_AGE_SECONDS
    if is_recent(mfa_session.access_token.step_up_verified_at, max_age_seconds=max_age):
        return
    if not await user_has_credentials(mfa_session.db, mfa_session.user.id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": MFA_STEP_UP_REQUIRED_CODE,
            "message": "Verify with your security key to continue.",
        },
    )


def require_step_up(
    max_age_seconds: int | None = None,
) -> object:
    """Dependency factory: ``dependencies=[require_step_up()]`` on any route."""

    async def _dependency(mfa_session: VerifiedMfaSession) -> MfaSession:
        await enforce_step_up(mfa_session, max_age_seconds=max_age_seconds)
        return mfa_session

    return Depends(_dependency)


async def require_recent_mfa(mfa_session: VerifiedMfaSession) -> MfaSession:
    """Require MFA (security key or recovery code) within the step-up window.

    Guards changes to a user's own security keys and recovery codes. Users
    without any key can add their first one without this check.
    """
    if has_recent_mfa(mfa_session.access_token):
        return mfa_session
    if not await user_has_credentials(mfa_session.db, mfa_session.user.id):
        return mfa_session
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": MFA_RECENT_VERIFICATION_REQUIRED_CODE,
            "message": "Verify with your security key to manage security keys.",
        },
    )


RecentMfaSession = Annotated[MfaSession, Depends(require_recent_mfa)]
