"""Security key registration, verification, and recovery codes."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from fastapi_users.password import PasswordHelper
from fido2 import cbor
from fido2.cose import CoseKey
from fido2.webauthn import (
    AttestedCredentialData,
    AuthenticationResponse,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
    PublicKeyCredentialUserEntity,
    RegistrationResponse,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.mfa.constants import MAX_CREDENTIALS_PER_USER, RECOVERY_CODE_COUNT
from tracecat.auth.mfa.policy import has_recent_mfa
from tracecat.auth.mfa.webauthn import (
    build_server,
    consume_challenge_state,
    store_challenge_state,
)
from tracecat.db.models import AccessToken, MfaRecoveryCode, User, WebAuthnCredential
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger

_RECOVERY_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# A code is a public lookup ID followed by a secret. The ID selects the one
# stored hash to check, so a wrong code almost never costs an Argon2 check.
# The 20-character secret from a 31-character alphabet is about 99 bits.
_RECOVERY_CODE_LOOKUP_LENGTH = 5
_RECOVERY_CODE_SECRET_LENGTH = 20
_RECOVERY_CODE_LENGTH = _RECOVERY_CODE_LOOKUP_LENGTH + _RECOVERY_CODE_SECRET_LENGTH
_RECOVERY_CODE_GROUP = 5
_recovery_code_hasher = PasswordHelper()


class MfaVerificationError(Exception):
    """A WebAuthn response or recovery code failed verification."""


def _normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _hash_recovery_codes(codes: list[str]) -> list[str]:
    """Hash codes one at a time, so a batch holds one Argon2 buffer at once."""
    return [_recovery_code_hasher.hash(_normalize_recovery_code(c)) for c in codes]


def _recovery_code_matches(normalized_code: str, code_hash: str) -> bool:
    try:
        verified, _ = _recovery_code_hasher.verify_and_update(
            normalized_code, code_hash
        )
    except Exception:
        return False
    return verified


def _generate_recovery_codes() -> list[str]:
    """Generate a set of recovery codes with distinct lookup IDs."""
    raw_codes: dict[str, str] = {}
    while len(raw_codes) < RECOVERY_CODE_COUNT:
        raw = "".join(
            secrets.choice(_RECOVERY_CODE_ALPHABET)
            for _ in range(_RECOVERY_CODE_LENGTH)
        )
        raw_codes.setdefault(raw[:_RECOVERY_CODE_LOOKUP_LENGTH], raw)
    return [
        "-".join(
            raw[i : i + _RECOVERY_CODE_GROUP]
            for i in range(0, _RECOVERY_CODE_LENGTH, _RECOVERY_CODE_GROUP)
        )
        for raw in raw_codes.values()
    ]


def _to_attested(credential: WebAuthnCredential) -> AttestedCredentialData:
    return AttestedCredentialData.create(
        credential.aaguid.bytes,
        credential.credential_id,
        CoseKey.parse(cast(Mapping[int, Any], cbor.decode(credential.public_key))),
    )


def _descriptor(credential: WebAuthnCredential) -> PublicKeyCredentialDescriptor:
    return PublicKeyCredentialDescriptor(
        type=PublicKeyCredentialType.PUBLIC_KEY,
        id=credential.credential_id,
        transports=_known_transports(credential.transports) or None,
    )


def _known_transports(values: list[str]) -> list[AuthenticatorTransport]:
    known = {t.value for t in AuthenticatorTransport}
    return [AuthenticatorTransport(v) for v in values if v in known]


def _transports(response: Mapping[str, Any]) -> list[str]:
    inner = response.get("response")
    if not isinstance(inner, Mapping):
        return []
    transports = inner.get("transports")
    if not isinstance(transports, list):
        return []
    return [str(t)[:32] for t in transports if isinstance(t, str)][:10]


class MfaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _lock_user(self, user_id: uuid.UUID) -> None:
        """Serialize security key and recovery code changes for one user.

        The user row lock is held until the transaction ends, so concurrent
        requests cannot both pass a credential count check (for example, two
        deletions each leaving "one" key).
        """
        user_id_column = cast(Any, User.id)
        await self.session.execute(
            select(user_id_column)
            .where(user_id_column == user_id)
            .with_for_update(key_share=True)
        )

    # --- Credentials ---

    async def list_credentials(self, user_id: uuid.UUID) -> list[WebAuthnCredential]:
        result = await self.session.execute(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user_id)
            .order_by(WebAuthnCredential.created_at)
        )
        return list(result.scalars().all())

    async def get_credential(
        self, user_id: uuid.UUID, credential_id: uuid.UUID
    ) -> WebAuthnCredential:
        result = await self.session.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.id == credential_id,
                WebAuthnCredential.user_id == user_id,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            raise TracecatNotFoundError("Security key not found")
        return credential

    async def rename_credential(
        self, user_id: uuid.UUID, credential_id: uuid.UUID, name: str
    ) -> WebAuthnCredential:
        credential = await self.get_credential(user_id, credential_id)
        credential.name = name.strip()
        await self.session.commit()
        await self.session.refresh(credential)
        return credential

    async def delete_credential(
        self,
        user_id: uuid.UUID,
        credential_id: uuid.UUID,
        *,
        allow_last: bool,
    ) -> None:
        """Delete a security key.

        Removing the last key also removes recovery codes, since they only
        exist to recover access to an enrolled account, and clears every
        session's MFA verification. A later policy then asks those sessions to
        enroll instead of treating them as verified by a key that is gone.
        """
        await self._lock_user(user_id)
        credential = await self.get_credential(user_id, credential_id)
        remaining = await self.count_credentials(user_id) - 1
        if remaining == 0 and not allow_last:
            raise MfaVerificationError(
                "A security key is required by your organization or platform policy. "
                "Add another security key before removing this one."
            )
        await self.session.delete(credential)
        if remaining == 0:
            await self.session.execute(
                delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
            )
            await self.session.execute(
                update(AccessToken)
                .where(cast(Any, AccessToken.user_id) == user_id)
                .values(mfa_verified_at=None, step_up_verified_at=None)
            )
        await self.session.commit()

    async def count_credentials(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user_id)
        )
        return int(result.scalar_one())

    async def reset_user_mfa(self, user_id: uuid.UUID) -> int:
        """Remove every security key, recovery code, and session for a user.

        Sessions are revoked so an already verified session cannot outlive the
        keys that verified it.
        """
        await self._lock_user(user_id)
        result = await self.session.execute(
            delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
        )
        await self.session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )
        await self.session.execute(
            delete(AccessToken).where(cast(Any, AccessToken.user_id) == user_id)
        )
        await self.session.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    # --- Registration ---

    async def begin_registration(
        self, user: User, access_token: AccessToken
    ) -> dict[str, Any]:
        existing = await self.list_credentials(user.id)
        if len(existing) >= MAX_CREDENTIALS_PER_USER:
            raise MfaVerificationError(
                f"You can register at most {MAX_CREDENTIALS_PER_USER} security keys."
            )
        display_name = (
            " ".join(p for p in (user.first_name, user.last_name) if p) or user.email
        )
        options, state = build_server().register_begin(
            PublicKeyCredentialUserEntity(
                name=user.email, id=user.id.bytes, display_name=display_name
            ),
            credentials=[_descriptor(c) for c in existing],
            resident_key_requirement=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        await store_challenge_state("register", access_token.id, dict(state))
        return dict(options.public_key)

    async def complete_registration(
        self,
        user: User,
        access_token: AccessToken,
        response: Mapping[str, Any],
        name: str,
    ) -> tuple[WebAuthnCredential, list[str] | None]:
        state = await consume_challenge_state("register", access_token.id)
        if state is None:
            raise MfaVerificationError("Registration expired. Try again.")
        try:
            registration = RegistrationResponse.from_dict(response)
            auth_data = build_server().register_complete(state, registration)
        except Exception as e:
            logger.info("Security key registration failed", error=str(e))
            raise MfaVerificationError("Security key registration failed.") from e
        credential_data = auth_data.credential_data
        if credential_data is None:
            raise MfaVerificationError("Security key registration failed.")

        await self._lock_user(user.id)
        count = await self.count_credentials(user.id)
        if count >= MAX_CREDENTIALS_PER_USER:
            raise MfaVerificationError(
                f"You can register at most {MAX_CREDENTIALS_PER_USER} security keys."
            )
        # Another session may have enrolled the first key after this request
        # passed the router's checks. Adding a further key needs this session
        # to have verified with an existing one.
        if count > 0 and not has_recent_mfa(access_token):
            raise MfaVerificationError("Verify with an existing security key first.")
        is_first = count == 0
        existing_owner = await self.session.execute(
            select(WebAuthnCredential.user_id).where(
                WebAuthnCredential.credential_id == credential_data.credential_id
            )
        )
        match existing_owner.scalar_one_or_none():
            case None:
                pass
            case owner_id if owner_id == user.id:
                raise MfaVerificationError("This security key is already registered.")
            case _:
                # Don't reveal that another account registered this credential.
                raise MfaVerificationError("Security key registration failed.")

        now = datetime.now(UTC)
        credential = WebAuthnCredential(
            user_id=user.id,
            credential_id=credential_data.credential_id,
            public_key=cbor.encode(credential_data.public_key),
            sign_count=auth_data.counter,
            aaguid=uuid.UUID(bytes=bytes(credential_data.aaguid)),
            transports=_transports(response),
            name=name.strip(),
            backup_eligible=auth_data.is_backup_eligible(),
            backup_state=auth_data.is_backed_up(),
            last_used_at=now,
        )
        self.session.add(credential)
        recovery_codes = (
            await self._replace_recovery_codes(user.id) if is_first else None
        )
        # Registering a key proves possession of it for this session.
        self._mark_verified(access_token, now=now, step_up=True)
        await self.session.commit()
        await self.session.refresh(credential)
        return credential, recovery_codes

    # --- Authentication ---

    async def begin_authentication(
        self, user: User, access_token: AccessToken
    ) -> dict[str, Any]:
        credentials = await self.list_credentials(user.id)
        if not credentials:
            raise MfaVerificationError("No security keys are registered.")
        options, state = build_server().authenticate_begin(
            credentials=[_descriptor(c) for c in credentials],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        await store_challenge_state("authenticate", access_token.id, dict(state))
        return dict(options.public_key)

    async def complete_authentication(
        self, user: User, access_token: AccessToken, response: Mapping[str, Any]
    ) -> WebAuthnCredential:
        """Verify an assertion and mark the session MFA and step-up verified."""
        state = await consume_challenge_state("authenticate", access_token.id)
        if state is None:
            raise MfaVerificationError("Verification expired. Try again.")
        # Serialize with other verifications so the signature counter check
        # and update see each other's writes.
        await self._lock_user(user.id)
        credentials = await self.list_credentials(user.id)
        try:
            authentication = AuthenticationResponse.from_dict(response)
            matched = build_server().authenticate_complete(
                state, [_to_attested(c) for c in credentials], authentication
            )
        except Exception as e:
            logger.info("Security key verification failed", error=str(e))
            raise MfaVerificationError("Security key verification failed.") from e

        credential = next(
            c for c in credentials if c.credential_id == matched.credential_id
        )
        auth_data = authentication.response.authenticator_data
        new_count = auth_data.counter
        # Synced passkeys always report 0. A non-increasing non-zero counter
        # indicates a cloned authenticator.
        if (new_count or credential.sign_count) and new_count <= credential.sign_count:
            logger.warning(
                "Security key signature counter did not increase",
                credential_id=str(credential.id),
            )
            raise MfaVerificationError("Security key verification failed.")
        now = datetime.now(UTC)
        credential.sign_count = new_count
        credential.backup_state = auth_data.is_backed_up()
        credential.last_used_at = now
        self._mark_verified(access_token, now=now, step_up=True)
        await self.session.commit()
        await self.session.refresh(credential)
        return credential

    # --- Recovery codes ---

    async def count_recovery_codes(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user_id,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        return int(result.scalar_one())

    async def regenerate_recovery_codes(self, user_id: uuid.UUID) -> list[str]:
        await self._lock_user(user_id)
        if await self.count_credentials(user_id) == 0:
            raise MfaVerificationError(
                "Add a security key before generating recovery codes."
            )
        codes = await self._replace_recovery_codes(user_id)
        await self.session.commit()
        return codes

    async def verify_recovery_code(
        self, user: User, access_token: AccessToken, code: str
    ) -> None:
        """Consume a recovery code and mark the session MFA verified.

        Recovery codes do not satisfy step-up checks: those need a security key.
        """
        normalized = _normalize_recovery_code(code)
        if len(normalized) != _RECOVERY_CODE_LENGTH:
            raise MfaVerificationError("Invalid recovery code.")
        rows = (
            (
                await self.session.execute(
                    select(MfaRecoveryCode.id, MfaRecoveryCode.code_hash).where(
                        MfaRecoveryCode.user_id == user.id,
                        MfaRecoveryCode.lookup
                        == normalized[:_RECOVERY_CODE_LOOKUP_LENGTH],
                        MfaRecoveryCode.used_at.is_(None),
                    )
                )
            )
            .tuples()
            .all()
        )
        match_id: uuid.UUID | None = None
        for row_id, code_hash in rows:
            if await asyncio.to_thread(_recovery_code_matches, normalized, code_hash):
                match_id = row_id
                break
        if match_id is None:
            raise MfaVerificationError("Invalid recovery code.")
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.id == match_id,
                MfaRecoveryCode.used_at.is_(None),
            )
            .values(used_at=now)
        )
        if not getattr(result, "rowcount", 0):
            await self.session.rollback()
            raise MfaVerificationError("Invalid recovery code.")
        self._mark_verified(access_token, now=now, step_up=False)
        await self.session.commit()

    async def _replace_recovery_codes(self, user_id: uuid.UUID) -> list[str]:
        await self.session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )
        codes = _generate_recovery_codes()
        hashes = await asyncio.to_thread(_hash_recovery_codes, codes)
        self.session.add_all(
            [
                MfaRecoveryCode(
                    user_id=user_id,
                    lookup=_normalize_recovery_code(code)[
                        :_RECOVERY_CODE_LOOKUP_LENGTH
                    ],
                    code_hash=code_hash,
                )
                for code, code_hash in zip(codes, hashes, strict=True)
            ]
        )
        return codes

    def _mark_verified(
        self, access_token: AccessToken, *, now: datetime, step_up: bool
    ) -> None:
        access_token.mfa_verified_at = now
        if step_up:
            access_token.step_up_verified_at = now
        self.session.add(access_token)
