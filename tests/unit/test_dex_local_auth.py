from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import InvalidPasswordException
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.dex import api_pb2
from tracecat.auth.dex.mode import MCPDexMode
from tracecat.auth.dex.service import (
    DexLocalAuthProvisioningError,
    DexLocalAuthProvisioningService,
)
from tracecat.auth.enums import AuthType
from tracecat.auth.schemas import UserCreate, UserUpdate
from tracecat.auth.types import Role
from tracecat.auth.users import (
    PendingDexSyncState,
    UserManager,
    get_user_db_context,
    get_user_manager_context,
)
from tracecat.contexts import ctx_role
from tracecat.db.models import Organization, OrganizationInvitation, User
from tracecat.db.models import Role as DBRole
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization.service import accept_invitation_for_user

pytestmark = pytest.mark.usefixtures("db")


class RecordingDexStub:
    def __init__(
        self,
        *,
        create_responses: list[api_pb2.CreatePasswordResp] | None = None,
        update_responses: list[api_pb2.UpdatePasswordResp] | None = None,
        delete_responses: list[api_pb2.DeletePasswordResp] | None = None,
    ) -> None:
        self.create_responses = create_responses or [api_pb2.CreatePasswordResp()]
        self.update_responses = update_responses or [api_pb2.UpdatePasswordResp()]
        self.delete_responses = delete_responses or [api_pb2.DeletePasswordResp()]
        self.create_requests: list[api_pb2.CreatePasswordReq] = []
        self.update_requests: list[api_pb2.UpdatePasswordReq] = []
        self.delete_requests: list[api_pb2.DeletePasswordReq] = []

    async def CreatePassword(
        self,
        request: api_pb2.CreatePasswordReq,
        *,
        timeout: float | None = None,
    ) -> api_pb2.CreatePasswordResp:
        assert timeout is not None
        self.create_requests.append(request)
        return self.create_responses.pop(0)

    async def UpdatePassword(
        self,
        request: api_pb2.UpdatePasswordReq,
        *,
        timeout: float | None = None,
    ) -> api_pb2.UpdatePasswordResp:
        assert timeout is not None
        self.update_requests.append(request)
        return self.update_responses.pop(0)

    async def DeletePassword(
        self,
        request: api_pb2.DeletePasswordReq,
        *,
        timeout: float | None = None,
    ) -> api_pb2.DeletePasswordResp:
        assert timeout is not None
        self.delete_requests.append(request)
        return self.delete_responses.pop(0)


class RecordingProvisioningService:
    def __init__(self, *, local_auth_enabled: bool = True) -> None:
        self.upserts: list[dict[str, str | None]] = []
        self.deletes: list[str] = []
        self.local_auth_enabled = local_auth_enabled

    async def is_local_auth_enabled(self) -> bool:
        return self.local_auth_enabled

    async def upsert_password(
        self,
        *,
        email: str,
        password_hash: str,
        username: str,
        user_id: str,
        previous_email: str | None = None,
    ) -> None:
        self.upserts.append(
            {
                "email": email,
                "password_hash": password_hash,
                "username": username,
                "user_id": user_id,
                "previous_email": previous_email,
            }
        )

    async def delete_password(self, email: str) -> None:
        self.deletes.append(email)


class FailingProvisioningService(RecordingProvisioningService):
    def __init__(self) -> None:
        super().__init__()
        self.upsert_attempts = 0
        self.delete_attempts = 0

    async def upsert_password(
        self,
        *,
        email: str,
        password_hash: str,
        username: str,
        user_id: str,
        previous_email: str | None = None,
    ) -> None:
        self.upsert_attempts += 1
        raise DexLocalAuthProvisioningError("Dex unavailable")

    async def delete_password(self, email: str) -> None:
        self.delete_attempts += 1
        raise DexLocalAuthProvisioningError("Dex unavailable")


@asynccontextmanager
async def user_manager_context(
    session: AsyncSession,
) -> AsyncGenerator[
    tuple[SQLAlchemyUserDatabase[User, uuid.UUID], UserManager],
    None,
]:
    async with get_user_db_context(session) as user_db:
        async with get_user_manager_context(user_db) as user_manager:
            yield user_db, user_manager


def patch_dex_service(monkeypatch: pytest.MonkeyPatch, service: object) -> None:
    monkeypatch.setattr(
        "tracecat.auth.users.get_dex_local_auth_service", lambda: service
    )


def set_pending_dex_state(
    user_manager: UserManager,
    *,
    invitation_token: str | None = None,
    password_hash: str | None = None,
    previous_email: str | None = None,
) -> None:
    user_manager._pending_state = PendingDexSyncState(
        invitation_token=invitation_token,
        dex_password_hash=password_hash,
        previous_email=previous_email,
    )


#
# Dex provisioning service
#


@pytest.mark.anyio
async def test_dex_local_auth_service_updates_existing_password() -> None:
    stub = RecordingDexStub(
        create_responses=[api_pb2.CreatePasswordResp(already_exists=True)],
        update_responses=[api_pb2.UpdatePasswordResp(not_found=False)],
    )
    service = DexLocalAuthProvisioningService(target="dex:5557", timeout_seconds=5.0)
    service._stub = stub

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.auth.dex.service.get_mcp_dex_mode",
        lambda: MCPDexMode.BASIC,
    )
    try:
        await service.upsert_password(
            email="user@example.com",
            password_hash="$2b$10$hash",
            username="user@example.com",
            user_id=str(uuid.uuid4()),
        )
    finally:
        monkeypatch.undo()

    assert len(stub.create_requests) == 1
    assert len(stub.update_requests) == 1
    assert stub.update_requests[0].email == "user@example.com"
    assert stub.update_requests[0].new_hash == b"$2b$10$hash"


@pytest.mark.anyio
async def test_dex_local_auth_service_replaces_previous_email_mapping() -> None:
    stub = RecordingDexStub()
    service = DexLocalAuthProvisioningService(target="dex:5557", timeout_seconds=5.0)
    service._stub = stub

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.auth.dex.service.get_mcp_dex_mode",
        lambda: MCPDexMode.BASIC,
    )
    try:
        await service.upsert_password(
            email="new@example.com",
            password_hash="$2b$10$hash",
            username="new@example.com",
            user_id=str(uuid.uuid4()),
            previous_email="old@example.com",
        )
    finally:
        monkeypatch.undo()

    assert [request.email for request in stub.delete_requests] == ["old@example.com"]
    assert [request.password.email for request in stub.create_requests] == [
        "new@example.com"
    ]


@pytest.mark.anyio
async def test_dex_local_auth_service_skips_federated_mode() -> None:
    stub = RecordingDexStub()
    service = DexLocalAuthProvisioningService(target="dex:5557", timeout_seconds=5.0)
    service._stub = stub

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.auth.dex.service.get_mcp_dex_mode",
        lambda: MCPDexMode.OIDC,
    )
    try:
        await service.upsert_password(
            email="user@example.com",
            password_hash="$2b$10$hash",
            username="user@example.com",
            user_id=str(uuid.uuid4()),
        )
    finally:
        monkeypatch.undo()

    assert stub.create_requests == []


#
# User manager lifecycle
#


@pytest.mark.anyio
async def test_user_manager_create_syncs_dex_local_auth(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService(local_auth_enabled=False)

    async with user_manager_context(session) as (_, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "validate_email",
            AsyncMock(return_value=None),
        )

        user = await user_manager.create(
            UserCreate(
                email="register@example.com",
                password="this-is-a-strong-password",
            )
        )

    assert len(service.upserts) == 1
    assert service.upserts[0]["email"] == "register@example.com"
    assert service.upserts[0]["user_id"] == str(user.id)
    password_hash = service.upserts[0]["password_hash"]
    assert password_hash is not None
    assert password_hash != user.hashed_password
    assert password_hash.startswith("$2")


@pytest.mark.anyio
async def test_user_manager_email_update_syncs_previous_email(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService(local_auth_enabled=False)
    role = Role(
        type="service",
        service_id="tracecat-api",
        is_platform_superuser=True,
    )

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "validate_email",
            AsyncMock(return_value=None),
        )
        user = await user_db.create(
            {
                "email": "before@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        token = ctx_role.set(role)
        try:
            await user_manager.update(
                UserUpdate(
                    email="after@example.com",
                    password="this-is-a-new-strong-password",
                ),
                user,
                safe=False,
            )
        finally:
            ctx_role.reset(token)

    assert len(service.upserts) == 1
    assert service.upserts[0]["email"] == "after@example.com"
    assert service.upserts[0]["previous_email"] == "before@example.com"


@pytest.mark.anyio
async def test_user_manager_email_update_requires_password_in_local_auth_mode(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService(local_auth_enabled=True)
    role = Role(
        type="service",
        service_id="tracecat-api",
        is_platform_superuser=True,
    )

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "validate_email",
            AsyncMock(return_value=None),
        )
        user = await user_db.create(
            {
                "email": "before@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        token = ctx_role.set(role)
        try:
            with pytest.raises(InvalidPasswordException, match="Password is required"):
                await user_manager.update(
                    UserUpdate(email="after@example.com"),
                    user,
                    safe=False,
                )
        finally:
            ctx_role.reset(token)

    assert service.upserts == []


@pytest.mark.anyio
async def test_user_manager_email_update_allows_sso_only_user_without_password(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService(local_auth_enabled=True)
    role = Role(
        type="service",
        service_id="tracecat-api",
        is_platform_superuser=True,
    )

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "validate_email",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            user_manager,
            "is_local_password_login_allowed",
            AsyncMock(return_value=False),
        )
        user = await user_db.create(
            {
                "email": "before@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        token = ctx_role.set(role)
        try:
            updated_user = await user_manager.update(
                UserUpdate(email="after@example.com"),
                user,
                safe=False,
            )
        finally:
            ctx_role.reset(token)

    assert updated_user.email == "after@example.com"
    assert service.upserts == []
    assert service.deletes == ["after@example.com", "before@example.com"]


@pytest.mark.anyio
async def test_user_manager_non_identity_update_skips_dex_sync(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()
    role = Role(
        type="service",
        service_id="tracecat-api",
        is_platform_superuser=True,
    )

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "validate_email",
            AsyncMock(return_value=None),
        )
        user = await user_db.create(
            {
                "email": "person@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        token = ctx_role.set(role)
        try:
            await user_manager.update(
                UserUpdate(first_name="Updated"),
                user,
                safe=False,
            )
        finally:
            ctx_role.reset(token)

    assert service.upserts == []


@pytest.mark.anyio
async def test_user_manager_on_after_reset_password_syncs_dex_local_auth(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        user = await user_db.create(
            {
                "email": "reset@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$local-auth-reset-hash",
        )
        await user_manager.on_after_reset_password(user)

    assert len(service.upserts) == 1
    assert service.upserts[0]["email"] == "reset@example.com"
    assert service.upserts[0]["password_hash"] == "$2b$12$local-auth-reset-hash"


#
# Policy reconciliation
#


@pytest.mark.anyio
async def test_user_manager_on_after_register_removes_dex_local_auth_when_policy_blocks_password_login(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "is_local_password_login_allowed",
            AsyncMock(return_value=False),
        )
        user = await user_db.create(
            {
                "email": "register-blocked@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$post-commit-hash",
        )
        await user_manager.on_after_register(user)

    assert service.upserts == []
    assert service.deletes == ["register-blocked@example.com"]


@pytest.mark.anyio
async def test_user_manager_on_after_update_removes_dex_local_auth_aliases_when_policy_blocks_password_login(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "is_local_password_login_allowed",
            AsyncMock(return_value=False),
        )
        user = await user_db.create(
            {
                "email": "new@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$post-commit-hash",
            previous_email="old@example.com",
        )
        await user_manager.on_after_update(
            user,
            {"email": "new@example.com", "password": "new-password"},
        )

    assert service.upserts == []
    assert service.deletes == ["new@example.com", "old@example.com"]


@pytest.mark.anyio
async def test_user_manager_on_after_reset_password_removes_dex_local_auth_when_policy_blocks_password_login(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            user_manager,
            "is_local_password_login_allowed",
            AsyncMock(return_value=False),
        )
        user = await user_db.create(
            {
                "email": "reset-blocked@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$post-commit-hash",
        )
        await user_manager.on_after_reset_password(user)

    assert service.upserts == []
    assert service.deletes == ["reset-blocked@example.com"]


@pytest.mark.anyio
async def test_accept_invitation_reconciles_dex_local_auth_policy(
    session: AsyncSession,
) -> None:
    org = Organization(
        id=uuid.uuid4(),
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    session.add(org)
    await session.flush()

    role = DBRole(
        id=uuid.uuid4(),
        name="Organization Member",
        slug=f"org-member-{uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    session.add(role)

    user = User(
        id=uuid.uuid4(),
        email="invited-saml@example.com",
        hashed_password="hashed-password",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role="basic",
    )
    session.add(user)
    await session.flush()

    invitation = OrganizationInvitation(
        organization_id=org.id,
        email=user.email,
        role_id=role.id,
        status=InvitationStatus.PENDING,
        invited_by=None,
        token=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    session.add(invitation)
    await session.commit()

    with patch.object(
        UserManager,
        "reconcile_dex_local_auth_policy",
        new=AsyncMock(return_value=True),
    ) as reconcile_policy:
        membership = await accept_invitation_for_user(
            session,
            user_id=user.id,
            token=invitation.token,
        )

    invitation_result = await session.execute(
        select(OrganizationInvitation).where(OrganizationInvitation.id == invitation.id)
    )
    updated_invitation = invitation_result.scalar_one()

    assert membership.organization_id == org.id
    assert updated_invitation.status == InvitationStatus.ACCEPTED
    reconcile_policy.assert_awaited_once()
    assert reconcile_policy.await_args is not None
    assert reconcile_policy.await_args.kwargs == {"raise_on_error": False}


#
# Session lifecycle and failure tolerance
#


@pytest.mark.anyio
async def test_user_manager_basic_login_syncs_dex_local_auth(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        monkeypatch.setattr(
            "tracecat.auth.users.config.TRACECAT__AUTH_TYPES",
            {AuthType.BASIC},
        )
        await user_db.create(
            {
                "email": "login@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        authenticated_user = await user_manager.authenticate(
            OAuth2PasswordRequestForm(
                username="login@example.com",
                password="this-is-a-strong-password",
            )
        )
        assert authenticated_user is not None

        await user_manager.on_after_login(authenticated_user)

    assert len(service.upserts) == 1
    assert service.upserts[0]["email"] == "login@example.com"
    password_hash = service.upserts[0]["password_hash"]
    assert password_hash is not None
    assert password_hash.startswith("$2")


@pytest.mark.anyio
async def test_user_manager_delete_syncs_dex_local_auth(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RecordingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        user = await user_db.create(
            {
                "email": "delete@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        await user_manager.delete(user)

    assert service.deletes == ["delete@example.com"]


@pytest.mark.anyio
async def test_user_manager_on_after_register_tolerates_dex_failures(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FailingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        user = await user_db.create(
            {
                "email": "register-failure@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$post-commit-hash",
        )
        await user_manager.on_after_register(user)

    assert service.upsert_attempts == 1


@pytest.mark.anyio
async def test_user_manager_on_after_update_tolerates_dex_failures(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FailingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        user = await user_db.create(
            {
                "email": "update-failure@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$post-commit-hash",
            previous_email="before@example.com",
        )
        await user_manager.on_after_update(
            user, {"email": "update-failure@example.com", "password": "new-password"}
        )

    assert service.upsert_attempts == 1


@pytest.mark.anyio
async def test_user_manager_on_after_reset_password_tolerates_dex_failures(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FailingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        user = await user_db.create(
            {
                "email": "reset-failure@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        set_pending_dex_state(
            user_manager,
            password_hash="$2b$12$post-commit-hash",
        )
        await user_manager.on_after_reset_password(user)

    assert service.upsert_attempts == 1


@pytest.mark.anyio
async def test_user_manager_on_after_delete_tolerates_dex_failures(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FailingProvisioningService()

    async with user_manager_context(session) as (user_db, user_manager):
        patch_dex_service(monkeypatch, service)
        user = await user_db.create(
            {
                "email": "delete-failure@example.com",
                "hashed_password": user_manager.password_helper.hash(
                    "this-is-a-strong-password"
                ),
                "is_verified": True,
            }
        )

        await user_manager.on_after_delete(user)

    assert service.delete_attempts == 1
