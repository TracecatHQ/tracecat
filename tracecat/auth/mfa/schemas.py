from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.auth.mfa.policy import SessionMfaState


class MfaStatusRead(BaseModel):
    state: SessionMfaState
    has_credentials: bool
    required_by_policy: bool
    webauthn_available: bool
    webauthn_unavailable_reason: str | None = None
    recovery_codes_remaining: int
    step_up_expires_at: datetime | None = Field(
        default=None,
        description="When the current step-up verification stops satisfying checks.",
    )


class WebAuthnOptionsRead(BaseModel):
    public_key: dict[str, Any] = Field(
        description="PublicKeyCredential{Creation,Request}Options as JSON (base64url)."
    )


class WebAuthnRegistrationVerify(BaseModel):
    credential: dict[str, Any] = Field(
        description="PublicKeyCredential.toJSON() output from navigator.credentials.create()."
    )
    name: str = Field(min_length=1, max_length=100)


class WebAuthnRegistrationResult(BaseModel):
    credential: WebAuthnCredentialRead
    recovery_codes: list[str] | None = Field(
        default=None,
        description="One-time recovery codes, returned only for the first security key.",
    )


AuthenticationPurpose = Literal["sign_in", "step_up"]


class WebAuthnAuthenticationVerify(BaseModel):
    credential: dict[str, Any] = Field(
        description="PublicKeyCredential.toJSON() output from navigator.credentials.get()."
    )


class RecoveryCodeVerify(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class RecoveryCodesRead(BaseModel):
    recovery_codes: list[str]


class WebAuthnCredentialRead(BaseModel):
    id: uuid.UUID
    name: str
    aaguid: uuid.UUID
    transports: list[str]
    backup_eligible: bool
    backup_state: bool
    created_at: datetime
    last_used_at: datetime | None


class WebAuthnCredentialUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


WebAuthnRegistrationResult.model_rebuild()


class MfaPolicyRead(BaseModel):
    mfa_required: bool
    webauthn_available: bool
    webauthn_unavailable_reason: str | None = None


class MfaPolicyUpdate(BaseModel):
    mfa_required: bool = Field(
        description=(
            "Require a security key or passkey at sign-in. Users without one "
            "must register one at their next sign-in."
        )
    )
