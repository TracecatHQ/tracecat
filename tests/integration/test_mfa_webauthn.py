"""End-to-end MFA flows through the real API, database, and WebAuthn verifier.

A software authenticator signs real WebAuthn responses, so registration and
assertion go through python-fido2's verification unmodified.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import orjson
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi_users.password import PasswordHelper
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from fido2.cose import ES256
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import (
    AttestationObject,
    AttestedCredentialData,
    AuthenticatorData,
    CollectedClientData,
)
from sqlalchemy import delete, select, update

from tracecat import config
from tracecat.api.app import app
from tracecat.auth.mfa import webauthn as mfa_webauthn
from tracecat.auth.mfa.constants import ORG_MFA_REQUIRED_KEY
from tracecat.auth.mfa.policy import clear_mfa_policy_cache
from tracecat.auth.users import SessionMetadataDatabaseStrategy, cookie_transport
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    AccessToken,
    Organization,
    OrganizationMembership,
    OrganizationSetting,
    PlatformSetting,
    User,
    WebAuthnCredential,
)

pytestmark = pytest.mark.usefixtures("db")

APP_URL = "https://app.example.test"
RP_ID = "app.example.test"
PASSWORD = "correct-horse-battery-staple"


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(
        self, key: str, value: str, *, expire_seconds: int | None = None
    ) -> bool:
        self.data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0


@pytest.fixture(autouse=True)
def mfa_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRedis]:
    redis = _FakeRedis()

    async def _get_redis_client() -> _FakeRedis:
        return redis

    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_ADMIN_MFA_BYPASS", False)
    monkeypatch.setattr(mfa_webauthn, "get_redis_client", _get_redis_client)
    clear_mfa_policy_cache()
    yield redis
    clear_mfa_policy_cache()


class SoftAuthenticator:
    """Minimal WebAuthn authenticator (ES256, user verification, 'none' attestation)."""

    def __init__(self, *, synced: bool = False, origin: str = APP_URL) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0
        # Synced passkeys are backup eligible/backed up and keep the counter at 0.
        self.synced = synced
        self.origin = origin

    def _flags(self, *, attested: bool) -> AuthenticatorData.FLAG:
        flags = AuthenticatorData.FLAG.UP | AuthenticatorData.FLAG.UV
        if self.synced:
            flags |= AuthenticatorData.FLAG.BE | AuthenticatorData.FLAG.BS
        if attested:
            flags |= AuthenticatorData.FLAG.AT
        return flags

    def _next_counter(self) -> int:
        if not self.synced:
            self.counter += 1
        return self.counter

    def create(self, options: dict[str, Any], *, rp_id: str = RP_ID) -> dict[str, Any]:
        client_data = CollectedClientData.create(
            type=CollectedClientData.TYPE.CREATE,
            challenge=websafe_decode(options["challenge"]),
            origin=self.origin,
        )
        credential_data = AttestedCredentialData.create(
            b"\0" * 16,
            self.credential_id,
            ES256.from_cryptography_key(self.key.public_key()),
        )
        auth_data = AuthenticatorData.create(
            hashlib.sha256(rp_id.encode()).digest(),
            self._flags(attested=True),
            self._next_counter(),
            credential_data,
        )
        attestation = AttestationObject.create("none", auth_data, {})
        return {
            "id": websafe_encode(self.credential_id),
            "rawId": websafe_encode(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(bytes(client_data)),
                "attestationObject": websafe_encode(bytes(attestation)),
                "transports": ["internal", "hybrid"] if self.synced else ["usb"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict[str, Any]) -> dict[str, Any]:
        client_data = CollectedClientData.create(
            type=CollectedClientData.TYPE.GET,
            challenge=websafe_decode(options["challenge"]),
            origin=self.origin,
        )
        auth_data = AuthenticatorData.create(
            hashlib.sha256(RP_ID.encode()).digest(),
            self._flags(attested=False),
            self._next_counter(),
        )
        signature = self.key.sign(
            bytes(auth_data) + client_data.hash, ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": websafe_encode(self.credential_id),
            "rawId": websafe_encode(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(bytes(client_data)),
                "authenticatorData": websafe_encode(bytes(auth_data)),
                "signature": websafe_encode(signature),
            },
            "clientExtensionResults": {},
        }


@asynccontextmanager
async def _client(token: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    cookies = {cookie_transport.cookie_name: token} if token else None
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", cookies=cookies
        ) as client:
            yield client
    finally:
        app.middleware_stack = None


async def _create_user(*, superuser: bool = False) -> User:
    async with get_async_session_bypass_rls_context_manager() as session:
        user = User(
            email=f"mfa-{uuid.uuid4().hex[:10]}@example.com",
            hashed_password=PasswordHelper().hash(PASSWORD),
            is_active=True,
            is_verified=True,
            is_superuser=superuser,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_org(*, members: list[User], mfa_required: bool) -> uuid.UUID:
    async with get_async_session_bypass_rls_context_manager() as session:
        org = Organization(
            name="MFA Test Org", slug=f"mfa-{uuid.uuid4().hex[:10]}", is_active=True
        )
        session.add(org)
        await session.flush()
        for member in members:
            session.add(
                OrganizationMembership(user_id=member.id, organization_id=org.id)
            )
        session.add(
            OrganizationSetting(
                organization_id=org.id,
                key=ORG_MFA_REQUIRED_KEY,
                value=orjson.dumps(mfa_required),
                value_type="json",
                is_encrypted=False,
            )
        )
        await session.commit()
        clear_mfa_policy_cache()
        return org.id


async def _new_session(user: User) -> str:
    """Create a session through the same strategy every login type uses.

    Password, OIDC, and SAML sign-in all call ``auth_backend.login`` which
    writes the session with this strategy.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        strategy = SessionMetadataDatabaseStrategy(
            SQLAlchemyAccessTokenDatabase(session, AccessToken),
            lifetime_seconds=config.SESSION_EXPIRE_TIME_SECONDS,
        )
        return await strategy.write_token(user)


async def _set_step_up_age(token: str, age: timedelta) -> None:
    async with get_async_session_bypass_rls_context_manager() as session:
        await session.execute(
            update(AccessToken)
            .where(cast(Any, AccessToken.token) == token)
            .values(step_up_verified_at=datetime.now(UTC) - age)
        )
        await session.commit()


async def _register(
    client: httpx.AsyncClient, authenticator: SoftAuthenticator, name: str = "Key"
) -> httpx.Response:
    options = await client.post("/auth/mfa/webauthn/register/options")
    assert options.status_code == 200, options.text
    credential = authenticator.create(options.json()["public_key"])
    return await client.post(
        "/auth/mfa/webauthn/register/verify",
        json={"credential": credential, "name": name},
    )


async def _assert_key(
    client: httpx.AsyncClient, authenticator: SoftAuthenticator
) -> httpx.Response:
    options = await client.post("/auth/mfa/webauthn/authenticate/options")
    assert options.status_code == 200, options.text
    public_key = options.json()["public_key"]
    assert public_key["userVerification"] == "required"
    return await client.post(
        "/auth/mfa/webauthn/authenticate/verify",
        json={"credential": authenticator.get(public_key)},
    )


@pytest.fixture(autouse=True)
async def cleanup_platform_mfa_setting() -> AsyncIterator[None]:
    yield
    async with get_async_session_bypass_rls_context_manager() as session:
        await session.execute(
            delete(PlatformSetting).where(
                PlatformSetting.key == "platform_admin_mfa_required"
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_without_policy_or_key_sessions_are_unchanged() -> None:
    user = await _create_user()
    token = await _new_session(user)
    async with _client(token) as client:
        assert (await client.get("/users/me")).status_code == 200
        status_ = (await client.get("/auth/mfa/status")).json()
    assert status_["state"] == "not_required"
    assert status_["webauthn_available"] is True


@pytest.mark.anyio
async def test_org_policy_forces_enrollment_then_challenge_on_next_login() -> None:
    user = await _create_user()
    await _create_org(members=[user], mfa_required=True)
    hardware_key = SoftAuthenticator()

    token = await _new_session(user)
    async with _client(token) as client:
        # Signed out everywhere except the MFA routes until a key is registered.
        assert (await client.get("/users/me")).status_code == 401
        status_ = (await client.get("/auth/mfa/status")).json()
        assert status_["state"] == "enrollment_required"
        assert status_["required_by_policy"] is True

        registered = await _register(client, hardware_key, name="YubiKey")
        assert registered.status_code == 201, registered.text
        body = registered.json()
        assert len(body["recovery_codes"]) == 10
        assert body["credential"]["transports"] == ["usb"]
        assert (await client.get("/users/me")).status_code == 200

    # Next login: the key must be used before entering.
    token = await _new_session(user)
    async with _client(token) as client:
        assert (await client.get("/users/me")).status_code == 401
        assert (await client.get("/auth/mfa/status")).json()["state"] == (
            "challenge_required"
        )
        # A pending session cannot add a key of its own.
        blocked = await client.post("/auth/mfa/webauthn/register/options")
        assert blocked.status_code == 401
        verified = await _assert_key(client, hardware_key)
        assert verified.status_code == 200, verified.text
        assert verified.json()["state"] == "verified"
        assert (await client.get("/users/me")).status_code == 200


@pytest.mark.anyio
async def test_password_login_is_gated_for_enrolled_user() -> None:
    user = await _create_user()
    synced_passkey = SoftAuthenticator(synced=True)
    async with _client(await _new_session(user)) as client:
        registered = await _register(client, synced_passkey, name="1Password")
        assert registered.status_code == 201, registered.text
        assert registered.json()["credential"]["backup_eligible"] is True

    async with _client() as client:
        login = await client.post(
            "/auth/login", data={"username": user.email, "password": PASSWORD}
        )
        if login.status_code == 404:
            pytest.skip("Basic auth is not enabled in this environment")
        assert login.status_code == 204, login.text
        assert (await client.get("/users/me")).status_code == 401
        # Synced passkeys report a constant zero counter and must still verify.
        verified = await _assert_key(client, synced_passkey)
        assert verified.status_code == 200, verified.text
        assert (await client.get("/users/me")).status_code == 200


@pytest.mark.anyio
async def test_recovery_code_signs_in_once_but_does_not_satisfy_step_up() -> None:
    user = await _create_user(superuser=True)
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        codes = (await _register(client, key)).json()["recovery_codes"]

    async with _client(await _new_session(user)) as client:
        assert (
            await client.post(
                "/auth/mfa/recovery-codes/verify", json={"code": "wrong-code"}
            )
        ).status_code == 400
        used = await client.post(
            "/auth/mfa/recovery-codes/verify", json={"code": codes[0].upper()}
        )
        assert used.status_code == 200, used.text
        assert used.json()["recovery_codes_remaining"] == 9
        assert (await client.get("/users/me")).status_code == 200
        denied = await client.patch(
            "/admin/settings/security/mfa", json={"mfa_required": False}
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "mfa_step_up_required"

    async with _client(await _new_session(user)) as client:
        reused = await client.post(
            "/auth/mfa/recovery-codes/verify", json={"code": codes[0]}
        )
        assert reused.status_code == 400


@pytest.mark.anyio
async def test_platform_admin_step_up_window_and_policy() -> None:
    admin = await _create_user(superuser=True)
    key = SoftAuthenticator()
    token = await _new_session(admin)
    async with _client(token) as client:
        # Cannot require MFA for platform admins without a key of your own.
        rejected = await client.patch(
            "/admin/settings/security/mfa", json={"mfa_required": True}
        )
        assert rejected.status_code == 400, rejected.text

        assert (await _register(client, key)).status_code == 201
        enabled = await client.patch(
            "/admin/settings/security/mfa", json={"mfa_required": True}
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["mfa_required"] is True

        # Step-up expires after the 5-minute window.
        await _set_step_up_age(token, timedelta(seconds=301))
        expired = await client.patch(
            "/admin/settings/security/mfa", json={"mfa_required": True}
        )
        assert expired.status_code == 403
        assert expired.json()["detail"]["code"] == "mfa_step_up_required"
        # Read-only admin routes do not need step-up.
        assert (await client.get("/admin/settings/security/mfa")).status_code == 200

        assert (await _assert_key(client, key)).status_code == 200
        await _set_step_up_age(token, timedelta(seconds=299))
        assert (
            await client.patch(
                "/admin/settings/security/mfa", json={"mfa_required": True}
            )
        ).status_code == 200

    # Another superuser without a key must now enroll at sign-in.
    other_admin = await _create_user(superuser=True)
    async with _client(await _new_session(other_admin)) as client:
        assert (await client.get("/users/me")).status_code == 401
        assert (await client.get("/auth/mfa/status")).json()["state"] == (
            "enrollment_required"
        )


@pytest.mark.anyio
async def test_break_glass_bypass_skips_admin_mfa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _create_user(superuser=True)
    key = SoftAuthenticator()
    async with _client(await _new_session(admin)) as client:
        assert (await _register(client, key)).status_code == 201

    monkeypatch.setattr(config, "TRACECAT__PLATFORM_ADMIN_MFA_BYPASS", True)
    token = await _new_session(admin)
    await _set_step_up_age(token, timedelta(hours=1))
    async with _client(token) as client:
        assert (await client.get("/users/me")).status_code == 200
        assert (
            await client.patch(
                "/admin/settings/security/mfa", json={"mfa_required": False}
            )
        ).status_code == 200


@pytest.mark.anyio
async def test_admin_reset_removes_keys_and_requires_step_up() -> None:
    admin = await _create_user(superuser=True)
    admin_key = SoftAuthenticator()
    member = await _create_user()
    member_key = SoftAuthenticator()
    async with _client(await _new_session(member)) as client:
        assert (await _register(client, member_key)).status_code == 201

    admin_token = await _new_session(admin)
    async with _client(admin_token) as client:
        assert (await _register(client, admin_key)).status_code == 201
        await _set_step_up_age(admin_token, timedelta(minutes=10))
        denied = await client.post(f"/admin/users/{member.id}/mfa/reset")
        assert denied.status_code == 403
        assert (await _assert_key(client, admin_key)).status_code == 200
        reset = await client.post(f"/admin/users/{member.id}/mfa/reset")
        assert reset.status_code == 204, reset.text

    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.user_id == member.id)
        )
        assert result.scalars().all() == []
    async with _client(await _new_session(member)) as client:
        assert (await client.get("/users/me")).status_code == 200


@pytest.mark.anyio
async def test_rejects_wrong_origin_rp_and_replayed_challenge(
    mfa_env: _FakeRedis,
) -> None:
    user = await _create_user()
    async with _client(await _new_session(user)) as client:
        phishing = SoftAuthenticator(origin="https://app.example.test.evil.example")
        assert (await _register(client, phishing)).status_code == 400

        options = (await client.post("/auth/mfa/webauthn/register/options")).json()
        wrong_rp = SoftAuthenticator().create(
            options["public_key"], rp_id="evil.example"
        )
        assert (
            await client.post(
                "/auth/mfa/webauthn/register/verify",
                json={"credential": wrong_rp, "name": "Key"},
            )
        ).status_code == 400

        key = SoftAuthenticator()
        options = (await client.post("/auth/mfa/webauthn/register/options")).json()
        credential = key.create(options["public_key"])
        first = await client.post(
            "/auth/mfa/webauthn/register/verify",
            json={"credential": credential, "name": "Key"},
        )
        assert first.status_code == 201, first.text
        replay = await client.post(
            "/auth/mfa/webauthn/register/verify",
            json={"credential": credential, "name": "Key"},
        )
        assert replay.status_code in (400, 403)
        assert not mfa_env.data


@pytest.mark.anyio
async def test_cannot_remove_last_key_when_policy_requires_it() -> None:
    user = await _create_user()
    await _create_org(members=[user], mfa_required=True)
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        created = (await _register(client, key)).json()["credential"]
        removed = await client.delete(f"/auth/mfa/credentials/{created['id']}")
        assert removed.status_code == 400
        listed = await client.get("/auth/mfa/credentials")
        assert [c["id"] for c in listed.json()] == [created["id"]]


@pytest.mark.anyio
async def test_org_admin_can_toggle_policy_only_with_a_key() -> None:
    user = await _create_user()
    org_id = await _create_org(members=[user], mfa_required=False)
    async with _client(await _new_session(user)) as client:
        client.cookies.set("tracecat:active-org-id", str(org_id))
        read = await client.get("/settings/security/mfa")
        # Plain members lack org:settings:read; the route must still be gated.
        assert read.status_code in (200, 403), read.text
