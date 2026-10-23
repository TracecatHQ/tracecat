"""End-to-end MFA flows through the real API, database, Redis, and WebAuthn verifier.

A software authenticator signs real WebAuthn responses, so registration and
assertion go through python-fido2's verification unmodified.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast, get_args

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

from tests._route_utils import iter_effective_api_routes
from tests.database import TEST_DB_CONFIG
from tests.support.membership import grant_org_membership
from tracecat import config
from tracecat.api.app import app
from tracecat.audit.service import AuditService
from tracecat.auth.credentials import SuperuserStepUpRole
from tracecat.auth.mfa import webauthn as mfa_webauthn
from tracecat.auth.mfa.constants import (
    MFA_STEP_UP_REQUIRED_CODE,
    ORG_MFA_REQUIRED_KEY,
)
from tracecat.auth.mfa.policy import clear_mfa_policy_cache
from tracecat.auth.mfa.service import MfaService
from tracecat.auth.users import SessionMetadataDatabaseStrategy, cookie_transport
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.engine import (
    get_async_session_bypass_rls_context_manager,
    reset_async_engine,
)
from tracecat.db.models import (
    AccessToken,
    MfaRecoveryCode,
    Organization,
    OrganizationSetting,
    PlatformSetting,
    User,
    WebAuthnCredential,
)
from tracecat.redis.client import RedisClient

pytestmark = pytest.mark.usefixtures("db")

APP_URL = "https://app.example.test"
RP_ID = "app.example.test"
PASSWORD = "correct-horse-battery-staple"
ACTIVE_ORG_COOKIE = "tracecat:active-org-id"

_STEP_UP_DEPENDENCY = get_args(SuperuserStepUpRole)[1].dependency

# Mutating platform admin routes that change no customer data, so they skip
# step-up. Every other mutating /admin route must require it.
_STEP_UP_EXEMPT_ADMIN_ROUTES = {
    ("POST", "/admin/maintenance/case-agent-session-interactions/backfill"),
    ("POST", "/admin/registry/versions/artifacts/backfill"),
    ("POST", "/admin/settings/audit/test"),
    ("POST", "/admin/settings/security/ip-allowlist/check"),
}


@pytest.fixture(autouse=True)
def mfa_env(monkeypatch: pytest.MonkeyPatch, env_sandbox: None) -> Iterator[None]:
    # Each xdist worker has its own database, so platform-wide settings such as
    # the platform admin MFA policy cannot leak between parallel tests.
    monkeypatch.setattr(config, "TRACECAT__DB_URI", TEST_DB_CONFIG.test_url_sync)
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", APP_URL)
    monkeypatch.setattr(config, "TRACECAT__PLATFORM_ADMIN_MFA_BYPASS", False)
    reset_async_engine()
    clear_mfa_policy_cache()
    yield
    clear_mfa_policy_cache()
    reset_async_engine()


@pytest.fixture(autouse=True)
async def close_redis_after_test() -> AsyncIterator[None]:
    yield
    await RedisClient().close()


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture audit events instead of delivering them to a webhook."""
    events: list[dict[str, Any]] = []

    async def _record(self: AuditService, **kwargs: Any) -> None:
        events.append(kwargs)

    monkeypatch.setattr(AuditService, "create_event", _record)
    return events


class SoftAuthenticator:
    """Minimal WebAuthn authenticator (ES256, user verification, 'none' attestation)."""

    def __init__(self, *, synced: bool = False, origin: str = APP_URL) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0
        # Synced passkeys are backup eligible/backed up and keep the counter at 0.
        self.synced = synced
        self.origin = origin
        self.user_verified = True

    def _flags(self, *, attested: bool) -> AuthenticatorData.FLAG:
        flags = AuthenticatorData.FLAG.UP
        if self.user_verified:
            flags |= AuthenticatorData.FLAG.UV
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


async def _create_org(
    *, members: list[User], mfa_required: bool, admins: list[User] | None = None
) -> uuid.UUID:
    async with get_async_session_bypass_rls_context_manager() as session:
        await seed_system_scopes(session)
        org = Organization(
            name="MFA Test Org", slug=f"mfa-{uuid.uuid4().hex[:10]}", is_active=True
        )
        session.add(org)
        await session.flush()
        for member in members:
            await grant_org_membership(
                session, user_id=member.id, organization_id=org.id
            )
        for admin in admins or []:
            await grant_org_membership(
                session,
                user_id=admin.id,
                organization_id=org.id,
                slug="organization-admin",
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


async def _credential_count(user: User) -> int:
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.execute(
            select(WebAuthnCredential.id).where(WebAuthnCredential.user_id == user.id)
        )
        return len(result.scalars().all())


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


async def _authentication_options(client: httpx.AsyncClient) -> dict[str, Any]:
    options = await client.post("/auth/mfa/webauthn/authenticate/options")
    assert options.status_code == 200, options.text
    public_key = options.json()["public_key"]
    assert public_key["userVerification"] == "required"
    return public_key


async def _verify(
    client: httpx.AsyncClient, assertion: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        "/auth/mfa/webauthn/authenticate/verify", json={"credential": assertion}
    )


async def _assert_key(
    client: httpx.AsyncClient, authenticator: SoftAuthenticator
) -> httpx.Response:
    public_key = await _authentication_options(client)
    return await _verify(client, authenticator.get(public_key))


def _is_step_up_denial(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    detail = response.json().get("detail")
    return isinstance(detail, dict) and detail.get("code") == MFA_STEP_UP_REQUIRED_CODE


def _depends_on(dependant: Any, call: object) -> bool:
    return any(
        sub.call is call or _depends_on(sub, call) for sub in dependant.dependencies
    )


def _mutating_routes() -> list[tuple[str, str, bool]]:
    """(method, path, requires step-up) for every non-GET API route."""
    return sorted(
        (method, route.path, _depends_on(route.dependant, _STEP_UP_DEPENDENCY))
        for route in iter_effective_api_routes(app)
        for method in route.methods - {"GET", "HEAD", "OPTIONS"}
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
async def test_org_admin_toggles_policy_only_with_a_key() -> None:
    owner = await _create_user()
    member = await _create_user()
    org_id = await _create_org(members=[member], admins=[owner], mfa_required=False)
    owner_token = await _new_session(owner)

    async with _client(await _new_session(member)) as client:
        client.cookies.set(ACTIVE_ORG_COOKIE, str(org_id))
        forbidden = await client.patch(
            "/settings/security/mfa", json={"mfa_required": True}
        )
        assert forbidden.status_code == 403, forbidden.text

    async with _client(owner_token) as client:
        client.cookies.set(ACTIVE_ORG_COOKIE, str(org_id))
        # The admin must be able to satisfy the policy before enabling it.
        rejected = await client.patch(
            "/settings/security/mfa", json={"mfa_required": True}
        )
        assert rejected.status_code == 400, rejected.text
        assert (await _register(client, SoftAuthenticator())).status_code == 201
        enabled = await client.patch(
            "/settings/security/mfa", json={"mfa_required": True}
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["mfa_required"] is True
        read = await client.get("/settings/security/mfa")
        assert read.json()["mfa_required"] is True

    async with _client(await _new_session(member)) as client:
        assert (await client.get("/users/me")).status_code == 401
        assert (await client.get("/auth/mfa/status")).json()["state"] == (
            "enrollment_required"
        )

    async with _client(owner_token) as client:
        client.cookies.set(ACTIVE_ORG_COOKIE, str(org_id))
        disabled = await client.patch(
            "/settings/security/mfa", json={"mfa_required": False}
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["mfa_required"] is False

    async with _client(await _new_session(member)) as client:
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
    assert all(len(c) == 23 and c.count("-") == 3 for c in codes)

    async with get_async_session_bypass_rls_context_manager() as session:
        stored = (
            (
                await session.execute(
                    select(MfaRecoveryCode.code_hash).where(
                        MfaRecoveryCode.user_id == user.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(stored) == 10
    assert all(h.startswith("$argon2id$") for h in stored)
    assert len(set(stored)) == len(stored)

    async with _client(await _new_session(user)) as client:
        for wrong in (
            "wrong-code",
            codes[0][:-1] + ("a" if codes[0][-1] != "a" else "b"),
        ):
            assert (
                await client.post(
                    "/auth/mfa/recovery-codes/verify", json={"code": wrong}
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
        assert _is_step_up_denial(denied), denied.text

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
        assert _is_step_up_denial(expired), expired.text
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


def test_every_mutating_admin_route_requires_step_up() -> None:
    routes = _mutating_routes()
    admin_without_step_up = {
        (method, path)
        for method, path, step_up in routes
        if path.startswith("/admin") and not step_up
    }
    assert admin_without_step_up == _STEP_UP_EXEMPT_ADMIN_ROUTES
    # Editing or deleting any user is also a privileged platform admin action.
    step_up_routes = {(method, path) for method, path, step_up in routes if step_up}
    assert {("PATCH", "/users/{id}"), ("DELETE", "/users/{id}")} <= step_up_routes


@pytest.mark.anyio
async def test_privileged_admin_routes_reject_a_stale_step_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Organization create and delete sit behind this deployment gate.
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)
    step_up_routes = [
        (method, path) for method, path, step_up in _mutating_routes() if step_up
    ]
    admin = await _create_user(superuser=True)
    token = await _new_session(admin)
    async with _client(token) as client:
        assert (await _register(client, SoftAuthenticator())).status_code == 201
        await _set_step_up_age(token, timedelta(minutes=10))
        not_enforced: list[tuple[str, str, int]] = []
        for method, path in step_up_routes:
            # The step-up dependency runs before path and body validation, so
            # placeholder IDs and an empty body reach it without side effects.
            url = re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)
            response = await client.request(method, url, json={})
            if not _is_step_up_denial(response):
                not_enforced.append((method, path, response.status_code))
    assert not_enforced == []


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
async def test_break_glass_bypass_keeps_organization_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _create_user(superuser=True)
    await _create_org(members=[admin], mfa_required=True)
    key = SoftAuthenticator()
    async with _client(await _new_session(admin)) as client:
        assert (await _register(client, key)).status_code == 201

    monkeypatch.setattr(config, "TRACECAT__PLATFORM_ADMIN_MFA_BYPASS", True)
    async with _client(await _new_session(admin)) as client:
        assert (await client.get("/users/me")).status_code == 401
        assert (await client.get("/auth/mfa/status")).json()["state"] == (
            "challenge_required"
        )
        assert (await _assert_key(client, key)).status_code == 200
        assert (await client.get("/users/me")).status_code == 200


@pytest.mark.anyio
async def test_admin_reset_removes_keys_revokes_sessions_and_requires_step_up() -> None:
    admin = await _create_user(superuser=True)
    admin_key = SoftAuthenticator()
    member = await _create_user()
    member_key = SoftAuthenticator()
    member_token = await _new_session(member)
    async with _client(member_token) as client:
        assert (await _register(client, member_key)).status_code == 201
        assert (await client.get("/users/me")).status_code == 200

    admin_token = await _new_session(admin)
    async with _client(admin_token) as client:
        assert (await _register(client, admin_key)).status_code == 201
        await _set_step_up_age(admin_token, timedelta(minutes=10))
        denied = await client.post(f"/admin/users/{member.id}/mfa/reset")
        assert _is_step_up_denial(denied), denied.text
        assert (await _assert_key(client, admin_key)).status_code == 200
        reset = await client.post(f"/admin/users/{member.id}/mfa/reset")
        assert reset.status_code == 204, reset.text

    async with _client(member_token) as client:
        assert (await client.get("/users/me")).status_code == 401

    assert await _credential_count(member) == 0
    async with _client(await _new_session(member)) as client:
        assert (await client.get("/users/me")).status_code == 200


@pytest.mark.anyio
async def test_rejects_wrong_origin_rp_and_replayed_registration() -> None:
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

        unverified = SoftAuthenticator()
        unverified.user_verified = False
        assert (await _register(client, unverified)).status_code == 400

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
        assert replay.status_code == 400
    assert await _credential_count(user) == 1


@pytest.mark.anyio
async def test_assertion_without_user_verification_is_rejected() -> None:
    user = await _create_user()
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        assert (await _register(client, key)).status_code == 201

    key.user_verified = False
    async with _client(await _new_session(user)) as client:
        assert (await _assert_key(client, key)).status_code == 400
        assert (await client.get("/users/me")).status_code == 401


@pytest.mark.anyio
async def test_assertion_from_another_users_key_is_rejected() -> None:
    alice, bob = await _create_user(), await _create_user()
    alice_key, bob_key = SoftAuthenticator(), SoftAuthenticator()
    async with _client(await _new_session(alice)) as client:
        assert (await _register(client, alice_key)).status_code == 201
    async with _client(await _new_session(bob)) as client:
        assert (await _register(client, bob_key)).status_code == 201

    async with _client(await _new_session(bob)) as client:
        assert (await _assert_key(client, alice_key)).status_code == 400
        assert (await client.get("/users/me")).status_code == 401


@pytest.mark.anyio
async def test_challenge_is_bound_to_the_session_that_requested_it() -> None:
    user = await _create_user()
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        assert (await _register(client, key)).status_code == 201

    first_token, second_token = await _new_session(user), await _new_session(user)
    async with _client(first_token) as first, _client(second_token) as second:
        assertion = key.get(await _authentication_options(first))
        # The second session has no ceremony of its own.
        assert (await _verify(second, assertion)).status_code == 400
        # Its own ceremony uses a different challenge.
        await _authentication_options(second)
        assert (await _verify(second, assertion)).status_code == 400
        assert (await second.get("/users/me")).status_code == 401

        assert (await _verify(first, assertion)).status_code == 200
        assert (await first.get("/users/me")).status_code == 200


@pytest.mark.anyio
async def test_signature_counter_regression_is_rejected() -> None:
    user = await _create_user()
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        assert (await _register(client, key)).status_code == 201
    async with _client(await _new_session(user)) as client:
        assert (await _assert_key(client, key)).status_code == 200

    # A cloned authenticator replays an older counter value.
    key.counter = 0
    async with _client(await _new_session(user)) as client:
        assert (await _assert_key(client, key)).status_code == 400
        assert (await client.get("/users/me")).status_code == 401


@pytest.mark.anyio
async def test_authentication_challenge_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _create_user()
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        assert (await _register(client, key)).status_code == 201

    monkeypatch.setattr(mfa_webauthn, "CHALLENGE_TTL_SECONDS", 1)
    async with _client(await _new_session(user)) as client:
        public_key = await _authentication_options(client)
        await asyncio.sleep(1.5)
        expired = await _verify(client, key.get(public_key))
        assert expired.status_code == 400
        assert expired.json()["detail"] == "Verification expired. Try again."
        assert (await client.get("/users/me")).status_code == 401


@pytest.mark.anyio
async def test_concurrent_submissions_of_one_assertion_verify_once() -> None:
    user = await _create_user()
    # Synced passkeys keep a zero counter, so only challenge consumption stops
    # the same assertion from verifying twice.
    passkey = SoftAuthenticator(synced=True)
    async with _client(await _new_session(user)) as client:
        assert (await _register(client, passkey)).status_code == 201

    async with _client(await _new_session(user)) as client:
        assertion = passkey.get(await _authentication_options(client))
        responses = await asyncio.gather(
            *(_verify(client, assertion) for _ in range(4))
        )
    assert sorted(r.status_code for r in responses) == [200, 400, 400, 400]


@pytest.mark.anyio
async def test_slow_consumer_cannot_take_a_newer_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _create_user()
    passkey = SoftAuthenticator(synced=True)
    async with _client(await _new_session(user)) as client:
        assert (await _register(client, passkey)).status_code == 201

    # Delay the first verification after its Redis read returns, until the
    # same session has started a newer ceremony.
    read_returned, newer_stored = asyncio.Event(), asyncio.Event()

    def delay_first_challenge_read(read: Any) -> Any:
        async def _read(self: RedisClient, key: str) -> str | None:
            value = await read(self, key)
            if key.startswith("mfa:webauthn:authenticate:") and not (
                read_returned.is_set()
            ):
                read_returned.set()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(newer_stored.wait(), timeout=2)
            return value

        return _read

    monkeypatch.setattr(RedisClient, "get", delay_first_challenge_read(RedisClient.get))
    monkeypatch.setattr(
        RedisClient, "getdel", delay_first_challenge_read(RedisClient.getdel)
    )
    async with _client(await _new_session(user)) as client:
        stale = passkey.get(await _authentication_options(client))
        slow = asyncio.create_task(_verify(client, stale))
        await asyncio.wait_for(read_returned.wait(), timeout=2)
        newer = await _authentication_options(client)
        newer_stored.set()
        assert (await slow).status_code == 200
        # The slow request consumed only its own challenge.
        assert (await _verify(client, passkey.get(newer))).status_code == 200


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
async def test_concurrent_deletions_keep_the_last_required_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _create_user()
    await _create_org(members=[user], mfa_required=True)
    async with _client(await _new_session(user)) as client:
        first = (await _register(client, SoftAuthenticator())).json()["credential"]
        second = (await _register(client, SoftAuthenticator())).json()["credential"]

        # Hold each deletion after it counts keys until the other one has
        # counted too (or a second passes). Without per-user serialization both
        # see two keys and both delete.
        count_credentials = MfaService.count_credentials
        counted = 0
        both_counted = asyncio.Event()

        async def count_after_peer(self: MfaService, user_id: uuid.UUID) -> int:
            nonlocal counted
            counted += 1
            if counted >= 2:
                both_counted.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_counted.wait(), timeout=1)
            return await count_credentials(self, user_id)

        monkeypatch.setattr(MfaService, "count_credentials", count_after_peer)
        responses = await asyncio.gather(
            client.delete(f"/auth/mfa/credentials/{first['id']}"),
            client.delete(f"/auth/mfa/credentials/{second['id']}"),
        )
    assert sorted(r.status_code for r in responses) == [204, 400]
    assert await _credential_count(user) == 1


@pytest.mark.anyio
async def test_security_key_and_recovery_code_use_is_audited(
    audit_events: list[dict[str, Any]],
) -> None:
    user = await _create_user()
    key = SoftAuthenticator()
    async with _client(await _new_session(user)) as client:
        registered = (await _register(client, key)).json()
    credential_id = uuid.UUID(registered["credential"]["id"])

    async with _client(await _new_session(user)) as client:
        assert (await _assert_key(client, key)).status_code == 200
        # A second verification on a signed-in session is a step-up.
        assert (await _assert_key(client, key)).status_code == 200
    async with _client(await _new_session(user)) as client:
        used = await client.post(
            "/auth/mfa/recovery-codes/verify",
            json={"code": registered["recovery_codes"][0]},
        )
        assert used.status_code == 200, used.text

    accepted = [
        (event["resource_type"], event["resource_id"], event["data"])
        for event in audit_events
        if event["action"] == "accept"
    ]
    assert accepted == [
        (
            "security_key",
            credential_id,
            {"operation": "sign_in", "auth_method": "security_key"},
        ),
        (
            "security_key",
            credential_id,
            {"operation": "step_up", "auth_method": "security_key"},
        ),
        (
            "security_key",
            user.id,
            {"operation": "sign_in", "auth_method": "recovery_code"},
        ),
    ]


@pytest.mark.parametrize(
    ("public_app_url", "expected_origin"),
    [
        ("http://localhost:80", "http://localhost"),
        ("https://App.Example.test:443/", "https://app.example.test"),
        ("http://localhost:3000", "http://localhost:3000"),
        ("https://app.example.test:8443", "https://app.example.test:8443"),
    ],
)
def test_relying_party_origin_matches_browser_origin(
    monkeypatch: pytest.MonkeyPatch, public_app_url: str, expected_origin: str
) -> None:
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", public_app_url)
    rp = mfa_webauthn.get_relying_party()
    assert rp.origin == expected_origin
    assert rp.id == expected_origin.split("://", 1)[1].split(":", 1)[0]
