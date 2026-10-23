"""Security key (FIDO2/WebAuthn) endpoints.

These routes accept MFA-pending sessions, which every other route treats as
signed out, so a user can complete a challenge or enroll a key at sign-in.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response, status

from tracecat import config
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditAction
from tracecat.auth.mfa.dependencies import (
    AnyMfaSession,
    MfaSession,
    RecentMfaSession,
    VerifiedMfaSession,
    require_recent_mfa,
    step_up_expires_at,
)
from tracecat.auth.mfa.policy import (
    SessionMfaState,
    mfa_required_by_policy,
    user_has_credentials,
)
from tracecat.auth.mfa.schemas import (
    MfaStatusRead,
    RecoveryCodesRead,
    RecoveryCodeVerify,
    WebAuthnAuthenticationVerify,
    WebAuthnCredentialRead,
    WebAuthnCredentialUpdate,
    WebAuthnOptionsRead,
    WebAuthnRegistrationResult,
    WebAuthnRegistrationVerify,
)
from tracecat.auth.mfa.service import MfaService, MfaVerificationError
from tracecat.auth.mfa.webauthn import (
    WebAuthnUnavailableError,
    webauthn_unavailable_reason,
)
from tracecat.auth.types import Role
from tracecat.auth.users import cookie_transport
from tracecat.db.models import User, WebAuthnCredential
from tracecat.logger import logger

router = APIRouter(prefix="/auth/mfa", tags=["auth"])


def _credential_read(credential: WebAuthnCredential) -> WebAuthnCredentialRead:
    return WebAuthnCredentialRead(
        id=credential.id,
        name=credential.name,
        aaguid=credential.aaguid,
        transports=list(credential.transports),
        backup_eligible=credential.backup_eligible,
        backup_state=credential.backup_state,
        created_at=credential.created_at,
        last_used_at=credential.last_used_at,
    )


def _ensure_webauthn_available() -> None:
    if reason := webauthn_unavailable_reason():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=reason)


def _verification_failed(e: MfaVerificationError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


async def _audit(
    user: User,
    action: AuditAction,
    *,
    resource_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> None:
    role = Role(type="user", user_id=actor_id or user.id, service_id="tracecat-api")
    try:
        async with AuditService.with_session(
            role=role, audit_sink="platform"
        ) as audit_svc:
            await audit_svc.create_event(
                resource_type="security_key",
                action=action,
                resource_id=resource_id or user.id,
                include_actor_label=False,
            )
    except Exception as exc:
        logger.warning(
            "Failed to emit security key audit event", action=action, error=str(exc)
        )


@router.get("/status", response_model=MfaStatusRead)
async def get_mfa_status(mfa_session: AnyMfaSession) -> MfaStatusRead:
    service = MfaService(mfa_session.db)
    user = mfa_session.user
    return MfaStatusRead(
        state=mfa_session.state,
        has_credentials=await user_has_credentials(mfa_session.db, user.id),
        required_by_policy=await mfa_required_by_policy(user),
        webauthn_available=webauthn_unavailable_reason() is None,
        webauthn_unavailable_reason=webauthn_unavailable_reason(),
        recovery_codes_remaining=await service.count_recovery_codes(user.id),
        step_up_expires_at=step_up_expires_at(
            mfa_session.access_token, config.TRACECAT__MFA_STEP_UP_MAX_AGE_SECONDS
        ),
    )


@router.post("/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_pending_session(mfa_session: AnyMfaSession) -> Response:
    """End the current session, including one that is waiting on MFA."""
    await mfa_session.db.delete(mfa_session.access_token)
    await mfa_session.db.commit()
    return await cookie_transport.get_logout_response()


# --- Registration ---


async def _ensure_can_register(mfa_session: MfaSession) -> None:
    if mfa_session.state is SessionMfaState.CHALLENGE_REQUIRED:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Verify with an existing security key first.",
        )
    if mfa_session.state is not SessionMfaState.ENROLLMENT_REQUIRED:
        await require_recent_mfa(mfa_session)


@router.post("/webauthn/register/options", response_model=WebAuthnOptionsRead)
async def begin_registration(mfa_session: AnyMfaSession) -> WebAuthnOptionsRead:
    _ensure_webauthn_available()
    await _ensure_can_register(mfa_session)
    try:
        options = await MfaService(mfa_session.db).begin_registration(
            mfa_session.user, mfa_session.access_token
        )
    except MfaVerificationError as e:
        raise _verification_failed(e) from e
    return WebAuthnOptionsRead(public_key=options)


@router.post(
    "/webauthn/register/verify",
    response_model=WebAuthnRegistrationResult,
    status_code=status.HTTP_201_CREATED,
)
async def complete_registration(
    mfa_session: AnyMfaSession, params: WebAuthnRegistrationVerify
) -> WebAuthnRegistrationResult:
    _ensure_webauthn_available()
    await _ensure_can_register(mfa_session)
    try:
        credential, recovery_codes = await MfaService(
            mfa_session.db
        ).complete_registration(
            mfa_session.user, mfa_session.access_token, params.credential, params.name
        )
    except MfaVerificationError as e:
        raise _verification_failed(e) from e
    await _audit(mfa_session.user, "create", resource_id=credential.id)
    return WebAuthnRegistrationResult(
        credential=_credential_read(credential), recovery_codes=recovery_codes
    )


# --- Authentication (sign-in challenge and step-up) ---


@router.post("/webauthn/authenticate/options", response_model=WebAuthnOptionsRead)
async def begin_authentication(mfa_session: AnyMfaSession) -> WebAuthnOptionsRead:
    try:
        options = await MfaService(mfa_session.db).begin_authentication(
            mfa_session.user, mfa_session.access_token
        )
    except WebAuthnUnavailableError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return WebAuthnOptionsRead(public_key=options)


@router.post("/webauthn/authenticate/verify", response_model=MfaStatusRead)
async def complete_authentication(
    mfa_session: AnyMfaSession, params: WebAuthnAuthenticationVerify
) -> MfaStatusRead:
    """Verify a security key. Satisfies both the sign-in challenge and step-up."""
    try:
        await MfaService(mfa_session.db).complete_authentication(
            mfa_session.user, mfa_session.access_token, params.credential
        )
    except (MfaVerificationError, WebAuthnUnavailableError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    mfa_session.state = SessionMfaState.VERIFIED
    return await get_mfa_status(mfa_session)


# --- Recovery codes ---


@router.post("/recovery-codes/verify", response_model=MfaStatusRead)
async def verify_recovery_code(
    mfa_session: AnyMfaSession, params: RecoveryCodeVerify
) -> MfaStatusRead:
    """Sign in with a one-time recovery code instead of a security key."""
    try:
        await MfaService(mfa_session.db).verify_recovery_code(
            mfa_session.user, mfa_session.access_token, params.code
        )
    except MfaVerificationError as e:
        raise _verification_failed(e) from e
    await _audit(mfa_session.user, "accept")
    mfa_session.state = SessionMfaState.VERIFIED
    return await get_mfa_status(mfa_session)


@router.post("/recovery-codes", response_model=RecoveryCodesRead)
async def regenerate_recovery_codes(
    mfa_session: RecentMfaSession,
) -> RecoveryCodesRead:
    """Replace all recovery codes. Previous codes stop working."""
    try:
        codes = await MfaService(mfa_session.db).regenerate_recovery_codes(
            mfa_session.user.id
        )
    except MfaVerificationError as e:
        raise _verification_failed(e) from e
    await _audit(mfa_session.user, "rotate")
    return RecoveryCodesRead(recovery_codes=codes)


# --- Credential management ---


@router.get("/credentials", response_model=list[WebAuthnCredentialRead])
async def list_credentials(
    mfa_session: VerifiedMfaSession,
) -> list[WebAuthnCredentialRead]:
    credentials = await MfaService(mfa_session.db).list_credentials(mfa_session.user.id)
    return [_credential_read(c) for c in credentials]


@router.patch("/credentials/{credential_id}", response_model=WebAuthnCredentialRead)
async def rename_credential(
    credential_id: uuid.UUID,
    mfa_session: VerifiedMfaSession,
    params: WebAuthnCredentialUpdate,
) -> WebAuthnCredentialRead:
    credential = await MfaService(mfa_session.db).rename_credential(
        mfa_session.user.id, credential_id, params.name
    )
    return _credential_read(credential)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    credential_id: uuid.UUID, mfa_session: RecentMfaSession
) -> None:
    user = mfa_session.user
    try:
        await MfaService(mfa_session.db).delete_credential(
            user.id,
            credential_id,
            allow_last=not await mfa_required_by_policy(user),
        )
    except MfaVerificationError as e:
        raise _verification_failed(e) from e
    await _audit(user, "delete", resource_id=credential_id)
