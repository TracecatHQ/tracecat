from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit import service as audit_service_module
from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.logger import (
    AuditEventDetails,
    audit_log,
)
from tracecat.audit.sanitization import sanitize_audit_metadata
from tracecat.audit.service import (
    AuditService,
    _AuditDelivery,
    _spawn_delivery,
)
from tracecat.audit.types import AuditEvent, AuditMetadata
from tracecat.auth.types import PlatformRole, Role
from tracecat.auth.users import UserManager
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.contexts import RequestAuditContext, ctx_request_audit, ctx_role
from tracecat.service import BaseService


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        workspace_id=None,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


@pytest.fixture
def audit_service(role: Role) -> AuditService:
    return AuditService(AsyncMock(), role=role)


@pytest.fixture(autouse=True)
def clear_audit_setting_cache() -> Iterator[None]:
    """Isolate the module-level audit-setting TTL cache between tests."""
    audit_service_module._get_audit_setting_cached.cache_clear()
    yield
    audit_service_module._get_audit_setting_cached.cache_clear()


class _AuditedService(BaseService):
    service_name = "test_audit"

    def __init__(self, session: AsyncSession, role: Role) -> None:
        super().__init__(session)
        self.role = role

    def _mutate_details(
        self, workflow_id: uuid.UUID, *, fail: bool = False
    ) -> AuditEventDetails:
        return AuditEventDetails(data={"changed_fields": ["status"]})

    @audit_log(
        resource_type="workflow",
        action="update",
        attempt_metadata=_mutate_details,
    )
    async def mutate(self, workflow_id: uuid.UUID, *, fail: bool = False) -> str:
        if fail:
            raise ValueError("operation failed")
        return "result"

    def _broken_details(self, workflow_id: uuid.UUID) -> AuditEventDetails:
        return AuditEventDetails(data={"changed_fields": ["status"]})

    @staticmethod
    def _broken_audit_result(
        result: str, *args: Any, **kwargs: Any
    ) -> AuditEventDetails:
        raise ValueError("metadata derivation failed")

    @audit_log(
        resource_type="workflow",
        action="update",
        attempt_metadata=_broken_details,
        terminal_metadata=_broken_audit_result,
    )
    async def mutate_with_broken_result(self, workflow_id: uuid.UUID) -> str:
        return "committed result"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scenario", "expected_statuses"),
    [
        ("success", [AuditEventStatus.ATTEMPT, AuditEventStatus.SUCCESS]),
        ("failure", [AuditEventStatus.ATTEMPT, AuditEventStatus.FAILURE]),
        ("service_account", [AuditEventStatus.ATTEMPT, AuditEventStatus.SUCCESS]),
        ("service_attributed", [AuditEventStatus.ATTEMPT, AuditEventStatus.SUCCESS]),
        ("service_unattributed", []),
    ],
)
async def test_audit_log_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    scenario: str,
    expected_statuses: list[AuditEventStatus],
) -> None:
    events: list[dict[str, object]] = []
    audit_sessions: list[object | None] = []
    resource_id = uuid.uuid4()

    @asynccontextmanager
    async def with_session(
        cls: type[AuditService],
        _role: Role | None = None,
        *,
        session: object | None = None,
        audit_sink: object | None = None,
    ):
        del cls, audit_sink
        audit_sessions.append(session)

        class Capture:
            async def create_event(self, **kwargs: object) -> None:
                events.append(kwargs)

        yield Capture()

    monkeypatch.setattr(AuditService, "with_session", classmethod(with_session))
    if scenario == "service_attributed":
        audit_role = role.model_copy(update={"type": "service"})
    elif scenario == "service_unattributed":
        audit_role = role.model_copy(update={"type": "service", "user_id": None})
    elif scenario == "service_account":
        audit_role = role.model_copy(
            update={
                "type": "service_account",
                "user_id": None,
                "service_account_id": uuid.uuid4(),
            }
        )
    else:
        audit_role = role
    service = _AuditedService(AsyncMock(), audit_role)
    token = ctx_role.set(role)
    try:
        if scenario == "failure":
            with pytest.raises(ValueError, match="operation failed"):
                await service.mutate(resource_id, fail=True)
        else:
            result = await service.mutate(resource_id)
            assert result == "result"
    finally:
        ctx_role.reset(token)

    assert [event["status"] for event in events] == expected_statuses
    if scenario == "failure":
        assert audit_sessions == [service.session, None]
    if events:
        assert all(event["data"] == {"changed_fields": ["status"]} for event in events)


@pytest.mark.anyio
async def test_audit_log_emit_false_failure_executes_operation_once(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
) -> None:
    operation_calls = 0
    create_event = AsyncMock()

    class SuppressedService(BaseService):
        service_name = "suppressed"

        def __init__(self) -> None:
            super().__init__(AsyncMock())
            self.role = role

        def _suppress_audit(self, workflow_id: uuid.UUID) -> AuditEventDetails:
            return AuditEventDetails(emit=False)

        @audit_log(
            resource_type="workflow",
            action="update",
            attempt_metadata=_suppress_audit,
        )
        async def mutate(self, workflow_id: uuid.UUID) -> None:
            del workflow_id
            nonlocal operation_calls
            operation_calls += 1
            raise ValueError("synthetic operation failure")

    monkeypatch.setattr(AuditService, "create_event", create_event)

    with pytest.raises(ValueError, match="synthetic operation failure"):
        await SuppressedService().mutate(uuid.uuid4())

    assert operation_calls == 1
    create_event.assert_not_awaited()


@pytest.mark.anyio
async def test_audit_log_result_derivation_failure_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch, role: Role
) -> None:
    events: list[dict[str, object]] = []

    async def create_event(_self: AuditService, **kwargs: object) -> None:
        events.append(kwargs)

    monkeypatch.setattr(AuditService, "create_event", create_event)
    service = _AuditedService(AsyncMock(), role)
    result = await service.mutate_with_broken_result(uuid.uuid4())

    assert result == "committed result"
    assert [event["status"] for event in events] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.SUCCESS,
    ]
    assert events[-1]["data"] == {"changed_fields": ["status"]}


@pytest.mark.anyio
async def test_audit_log_failure_uses_fresh_session_after_abort(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    session: AsyncSession,
) -> None:
    delivered: list[tuple[AsyncSession, AuditEventStatus]] = []

    async def get_webhook_url(_self: AuditService) -> str:
        return "https://example.com/audit"

    async def get_actor_label(_self: AuditService) -> str:
        return "user@example.com"

    async def post_event(
        self: AuditService, *, webhook_url: str, payload: AuditEvent
    ) -> None:
        assert webhook_url == "https://example.com/audit"
        delivered.append((self.session, payload.status))

    class FailingService(_AuditedService):
        @audit_log(
            resource_type="workflow",
            action="update",
        )
        async def fail_after_aborting_session(self, workflow_id: uuid.UUID) -> None:
            await self.session.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(AuditService, "_get_webhook_url", get_webhook_url)
    monkeypatch.setattr(AuditService, "_get_actor_label", get_actor_label)
    monkeypatch.setattr(AuditService, "_post_event", post_event)

    service = FailingService(session, role)
    with pytest.raises(SQLAlchemyError):
        await service.fail_after_aborting_session(uuid.uuid4())

    assert [status for _, status in delivered] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.FAILURE,
    ]
    assert delivered[0][0] is session
    assert delivered[1][0] is not session


@pytest.mark.anyio
async def test_create_event_skips_without_webhook(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    monkeypatch.setattr(audit_service, "_get_webhook_url", AsyncMock(return_value=None))
    post_mock = AsyncMock()
    monkeypatch.setattr(audit_service, "_post_event", post_mock)

    await audit_service.create_event(resource_type="workflow", action="create")

    post_mock.assert_not_awaited()


@respx.mock
@pytest.mark.anyio
async def test_create_event_streams_exact_payload_contract(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    webhook_url = "https://example.com/audit"
    resource_id = uuid.uuid4()
    webhook_id = uuid.uuid4()
    client_ip = "192.0.2.10"
    user_agent = "TracecatAuditTest/1.0"
    monkeypatch.setattr(
        audit_service, "_get_webhook_url", AsyncMock(return_value=webhook_url)
    )
    monkeypatch.setattr(
        audit_service, "_get_custom_headers", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        audit_service, "_get_custom_payload", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(audit_service, "_get_verify_ssl", AsyncMock(return_value=True))
    monkeypatch.setattr(
        audit_service, "_get_payload_attribute", AsyncMock(return_value=None)
    )
    mock_user = MagicMock()
    mock_user.email = "actor@example.test"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=mock_user)
    audit_service.session.execute = AsyncMock(return_value=result_mock)
    route = respx.post(webhook_url).mock(return_value=httpx.Response(200))

    audit_token = ctx_request_audit.set(
        RequestAuditContext(client_ip=client_ip, user_agent=user_agent)
    )
    try:
        await audit_service.create_event(
            resource_type="webhook",
            action="update",
            resource_id=resource_id,
            status=AuditEventStatus.SUCCESS,
            data={
                "webhook_id": str(webhook_id),
                "changed_fields": ["status"],
            },
        )
    finally:
        ctx_request_audit.reset(audit_token)

    await flush_audit_deliveries()
    assert route.called
    payload = orjson.loads(route.calls[0].request.content)
    created_at = payload.pop("created_at")
    assert (
        datetime.fromisoformat(created_at.replace("Z", "+00:00")).utcoffset()
        is not None
    )
    role = audit_service.role
    assert isinstance(role, Role)
    assert payload == {
        "organization_id": str(role.organization_id),
        "workspace_id": None,
        "actor_type": "USER",
        "actor_id": str(role.actor_id),
        "actor_label": "actor@example.test",
        "ip_address": client_ip,
        "user_agent": user_agent,
        "resource_type": "webhook",
        "resource_id": str(resource_id),
        "action": "update",
        "status": "SUCCESS",
        "data": {
            "webhook_id": str(webhook_id),
            "changed_fields": ["status"],
        },
    }


@pytest.mark.anyio
async def test_create_event_enforces_audit_metadata_policy(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    webhook_url = "https://example.com/audit"
    post_mock = AsyncMock()
    case_id = str(uuid.uuid4())
    data = {
        "case_id": case_id,
        "execution_id": "https://user:password@example.test/run?token=secret",
        "operation": "retry",
        "workflow_status": "Authorization: Bearer opaque-token",
        "changed_fields": ["status"],
        "name": "arbitrary resource name",
        "description": "arbitrary resource description",
        "content": "raw comment content",
        "password": "password-value",
        "api_key": "api-key-value",
        "oauth_token": "oauth-token-value",
        "cookie": "session=cookie-value",
        "authorization": "Bearer authorization-value",
        "headers": "Authorization: Bearer header-value",
        "request_body": "raw request body",
        "response_body": "raw response body",
        "workflow_input": "raw workflow input",
        "workflow_output": "raw workflow output",
        "prompt": "raw prompt",
        "tool_result": "raw tool result",
        "file_content": "raw uploaded file content",
        "environment": "SECRET_VALUE",
        "before": "secret-bearing old value",
        "after": "secret-bearing new value",
    }
    monkeypatch.setattr(
        audit_service, "_get_webhook_url", AsyncMock(return_value=webhook_url)
    )
    monkeypatch.setattr(
        audit_service, "_get_actor_label", AsyncMock(return_value="user@example.com")
    )
    monkeypatch.setattr(audit_service, "_post_event", post_mock)

    await audit_service.create_event(
        resource_type="case_comment",
        action="create",
        resource_id=uuid.uuid4(),
        status=AuditEventStatus.SUCCESS,
        data=data,
    )

    assert post_mock.await_count == 1
    assert post_mock.await_args is not None
    payload = post_mock.await_args.kwargs["payload"]
    assert payload.resource_type == "case_comment"
    assert payload.data == {
        "case_id": case_id,
        "operation": "retry",
        "changed_fields": ["status"],
    }


@pytest.mark.anyio
async def test_create_event_can_emit_sanitized_platform_org_auth_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhook_url = "https://example.com/platform-audit"
    client_ip = "203.0.113.10"
    user_agent = "TracecatClient/1.0 [redacted email] Authorization: [redacted]"
    role = Role(
        type="user",
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )
    audit_service = AuditService(AsyncMock(), role=role, audit_sink="platform")
    post_mock = AsyncMock()
    actor_label_mock = AsyncMock()
    monkeypatch.setattr(
        audit_service, "_get_webhook_url", AsyncMock(return_value=webhook_url)
    )
    monkeypatch.setattr(audit_service, "_get_actor_label", actor_label_mock)
    monkeypatch.setattr(audit_service, "_post_event", post_mock)
    audit_token = ctx_request_audit.set(
        RequestAuditContext(client_ip=client_ip, user_agent=user_agent)
    )

    try:
        await audit_service.create_event(
            resource_type="auth",
            action="sign_in",
            resource_id=role.user_id,
            data={"auth_method": "saml"},
            include_actor_label=False,
        )
    finally:
        ctx_request_audit.reset(audit_token)

    actor_label_mock.assert_not_awaited()
    assert post_mock.await_args is not None
    payload = post_mock.await_args.kwargs["payload"]
    assert payload.organization_id == role.organization_id
    assert payload.actor_id == role.user_id
    assert payload.actor_label is None
    assert payload.ip_address == client_ip
    assert payload.user_agent == user_agent
    assert payload.data == {"auth_method": "saml"}


@pytest.mark.anyio
async def test_auth_success_audit_emits_to_platform_and_org_sinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.is_superuser = False
    calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_with_session(
        role: Role | PlatformRole | None = None,
        *,
        session=None,
        audit_sink=None,
    ):
        class FakeAuditService:
            async def create_event(self, **kwargs):
                calls.append(
                    {
                        "role": role,
                        "session": session,
                        "audit_sink": audit_sink,
                        "kwargs": kwargs,
                    }
                )

        yield FakeAuditService()

    monkeypatch.setattr(AuditService, "with_session", fake_with_session)
    manager = UserManager.__new__(UserManager)

    await manager._emit_auth_success_audit(
        user=user,
        auth_method="basic",
        org_ids={org_id},
    )

    assert [call["audit_sink"] for call in calls] == ["platform", "organization"]
    for call in calls:
        role = call["role"]
        assert isinstance(role, Role)
        assert role.user_id == user_id
        assert role.organization_id == org_id
        assert call["kwargs"] == {
            "resource_type": "auth",
            "action": "sign_in",
            "resource_id": user_id,
            "data": {"auth_method": "basic"},
            "include_actor_label": False,
        }


@pytest.mark.anyio
async def test_auth_success_audit_emits_superuser_auth_only_to_platform_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.is_superuser = True
    calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_with_session(
        role: Role | PlatformRole | None = None,
        *,
        session=None,
        audit_sink=None,
    ):
        class FakeAuditService:
            async def create_event(self, **kwargs):
                calls.append(
                    {
                        "role": role,
                        "session": session,
                        "audit_sink": audit_sink,
                        "kwargs": kwargs,
                    }
                )

        yield FakeAuditService()

    monkeypatch.setattr(AuditService, "with_session", fake_with_session)
    manager = UserManager.__new__(UserManager)

    await manager._emit_auth_success_audit(
        user=user,
        auth_method="basic",
        org_ids=set(),
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["audit_sink"] == "platform"
    role = call["role"]
    assert isinstance(role, PlatformRole)
    assert role.user_id == user_id
    assert call["kwargs"] == {
        "resource_type": "auth",
        "action": "sign_in",
        "resource_id": user_id,
        "data": {"auth_method": "basic"},
        "include_actor_label": False,
    }


@pytest.mark.anyio
async def test_on_after_login_without_org_context_skips_org_sinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A context-less login (basic/OIDC) must not fan out to org sinks.

    Regression test for the smell where a non-org-specific login was attributed
    into every organization the user belonged to. A platform superuser who is a
    member of several orgs must only ever reach the platform sink here.
    """
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "super@example.com"
    user.is_superuser = True
    calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_with_session(
        role: Role | PlatformRole | None = None,
        *,
        session=None,
        audit_sink=None,
    ):
        class FakeAuditService:
            async def create_event(self, **kwargs):
                calls.append({"role": role, "audit_sink": audit_sink})

        yield FakeAuditService()

    # If the fanout regressed, it would re-derive these memberships and write to
    # their org sinks. Returning a non-empty set makes that regression fail.
    async def fake_list_org_ids(self, user_id):
        return {uuid.uuid4(), uuid.uuid4()}

    monkeypatch.setattr(AuditService, "with_session", fake_with_session)
    monkeypatch.setattr(UserManager, "_list_user_org_ids", fake_list_org_ids)
    manager = UserManager.__new__(UserManager)
    manager.logger = MagicMock()
    manager.user_db = MagicMock()
    manager.user_db.update = AsyncMock()

    # Basic/OIDC caller shape: organization_id defaults to None.
    await manager.on_after_login(user, request=None, response=None)

    assert [call["audit_sink"] for call in calls] == ["platform"]
    assert isinstance(calls[0]["role"], PlatformRole)


@pytest.mark.anyio
async def test_on_after_login_with_org_context_fans_out_to_that_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A login carrying explicit org context (SAML) writes that one org's sinks."""
    org_id = uuid.uuid4()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "super@example.com"
    user.is_superuser = True
    calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_with_session(
        role: Role | PlatformRole | None = None,
        *,
        session=None,
        audit_sink=None,
    ):
        class FakeAuditService:
            async def create_event(self, **kwargs):
                calls.append(
                    {
                        "role": role,
                        "audit_sink": audit_sink,
                        "organization_id": getattr(role, "organization_id", None),
                    }
                )

        yield FakeAuditService()

    # Should never be consulted when explicit org context is supplied.
    async def fake_list_org_ids(self, user_id):
        raise AssertionError("must not re-derive memberships with explicit org context")

    monkeypatch.setattr(AuditService, "with_session", fake_with_session)
    monkeypatch.setattr(UserManager, "_list_user_org_ids", fake_list_org_ids)
    manager = UserManager.__new__(UserManager)
    manager.logger = MagicMock()
    manager.user_db = MagicMock()
    manager.user_db.update = AsyncMock()

    await manager.on_after_login(
        user, request=None, response=None, organization_id=org_id
    )

    # superuser platform event + the single org's (platform, organization) sinks.
    assert [call["audit_sink"] for call in calls] == [
        "platform",
        "platform",
        "organization",
    ]
    org_scoped = [c for c in calls if isinstance(c["role"], Role)]
    assert {c["organization_id"] for c in org_scoped} == {org_id}


@pytest.mark.anyio
async def test_auth_success_audit_skips_platform_scoped_auth_for_non_superusers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.is_superuser = False
    with_session = MagicMock()
    monkeypatch.setattr(AuditService, "with_session", with_session)
    manager = UserManager.__new__(UserManager)

    await manager._emit_auth_success_audit(
        user=user,
        auth_method="basic",
        org_ids=set(),
    )

    with_session.assert_not_called()


@pytest.mark.anyio
async def test_on_after_register_emits_user_create_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    user = MagicMock()
    user.id = user_id
    user.email = "registered@example.com"
    user.is_superuser = False
    calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def fake_with_session(
        role: Role | PlatformRole | None = None,
        *,
        session=None,
        audit_sink=None,
    ):
        class FakeAuditService:
            async def create_event(self, **kwargs):
                calls.append(
                    {
                        "role": role,
                        "session": session,
                        "audit_sink": audit_sink,
                        "kwargs": kwargs,
                    }
                )

        yield FakeAuditService()

    monkeypatch.setattr(AuditService, "with_session", fake_with_session)
    monkeypatch.setattr(
        "tracecat.auth.users.ensure_single_tenant_user_defaults",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "tracecat.auth.users.config.TRACECAT__AUTH_SUPERADMIN_EMAIL",
        "superadmin@example.com",
    )

    manager = UserManager.__new__(UserManager)
    manager.logger = MagicMock()
    manager._pending_invitation_token = None

    await manager.on_after_register(user)

    assert len(calls) == 1
    call = calls[0]
    assert call["audit_sink"] == "platform"
    role = call["role"]
    assert isinstance(role, PlatformRole)
    assert role.user_id == user_id
    assert call["kwargs"] == {
        "resource_type": "user",
        "action": "create",
        "resource_id": user_id,
    }


@pytest.mark.anyio
async def test_platform_role_defaults_to_platform_audit_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )
    audit_service = AuditService(AsyncMock(), role=role)
    fetch_platform_setting = AsyncMock(return_value="https://example.com/audit")
    monkeypatch.setattr(
        audit_service_module, "_fetch_platform_setting", fetch_platform_setting
    )

    assert audit_service.audit_sink == "platform"
    assert await audit_service._get_webhook_url() == "https://example.com/audit"
    fetch_platform_setting.assert_awaited_once_with("audit_webhook_url")


@pytest.mark.anyio
async def test_audit_setting_cache_hits_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeat lookup within the TTL is served from cache, not the DB."""
    role = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    audit_service = AuditService(AsyncMock(), role=role)
    fetch = AsyncMock(return_value="https://example.com/audit")
    monkeypatch.setattr(audit_service_module, "_fetch_platform_setting", fetch)

    first = await audit_service._get_webhook_url()
    second = await audit_service._get_webhook_url()

    assert first == second == "https://example.com/audit"
    fetch.assert_awaited_once_with("audit_webhook_url")


@pytest.mark.anyio
async def test_audit_setting_cache_separates_sinks_and_orgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sink identity and org id are part of the key; no cross-tenant bleed."""
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    platform_role = PlatformRole(
        type="user", user_id=uuid.uuid4(), service_id="tracecat-api"
    )

    def make_org_service(org_id: uuid.UUID) -> AuditService:
        return AuditService(
            AsyncMock(),
            role=Role(
                type="user",
                organization_id=org_id,
                user_id=uuid.uuid4(),
                service_id="tracecat-api",
                scopes=ADMIN_SCOPES,
            ),
        )

    platform_service = AuditService(AsyncMock(), role=platform_role)
    monkeypatch.setattr(
        audit_service_module,
        "_fetch_platform_setting",
        AsyncMock(return_value="https://platform.example.com/audit"),
    )

    org_values = {
        org_a: "https://org-a.example.com/audit",
        org_b: "https://org-b.example.com/audit",
    }

    async def fake_get_setting(
        key: str, *, role: Role | None = None, session: Any = None, default: Any = None
    ) -> Any:
        assert role is not None and role.organization_id is not None
        return org_values[role.organization_id]

    monkeypatch.setattr("tracecat.settings.service.get_setting", fake_get_setting)

    assert (
        await platform_service._get_webhook_url()
        == "https://platform.example.com/audit"
    )
    assert (
        await make_org_service(org_a)._get_webhook_url()
        == "https://org-a.example.com/audit"
    )
    assert (
        await make_org_service(org_b)._get_webhook_url()
        == "https://org-b.example.com/audit"
    )


@pytest.mark.anyio
async def test_audit_setting_cache_clear_restores_fresh_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache_clear forces the next lookup back to the underlying fetch."""
    role = PlatformRole(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    audit_service = AuditService(AsyncMock(), role=role)
    fetch = AsyncMock(
        side_effect=[
            "https://first.example.com/audit",
            "https://second.example.com/audit",
        ]
    )
    monkeypatch.setattr(audit_service_module, "_fetch_platform_setting", fetch)

    assert await audit_service._get_webhook_url() == "https://first.example.com/audit"
    assert await audit_service._get_webhook_url() == "https://first.example.com/audit"

    audit_service_module._get_audit_setting_cached.cache_clear()

    assert await audit_service._get_webhook_url() == "https://second.example.com/audit"
    assert fetch.await_count == 2


@pytest.mark.anyio
async def test_post_event_uses_custom_payload_headers_and_verify_ssl(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    webhook_url = "https://example.com/audit"
    monkeypatch.setattr(
        audit_service,
        "_get_custom_headers",
        AsyncMock(return_value={"X-Custom-Header": "custom-value"}),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_custom_payload",
        AsyncMock(return_value={"resource_type": "organization", "custom": "yes"}),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_verify_ssl",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_payload_attribute",
        AsyncMock(return_value=None),
    )

    response_mock = MagicMock()
    response_mock.raise_for_status = MagicMock()
    client_mock = AsyncMock()
    client_mock.post = AsyncMock(return_value=response_mock)
    async_client_context_mock = AsyncMock()
    async_client_context_mock.__aenter__.return_value = client_mock
    async_client_context_mock.__aexit__.return_value = None

    event = AuditEvent(
        organization_id=uuid.uuid4(),
        workspace_id=None,
        actor_type=AuditEventActor.USER,
        actor_id=uuid.uuid4(),
        actor_label="user@example.com",
        resource_type="workflow",
        resource_id=uuid.uuid4(),
        action="create",
        status=AuditEventStatus.SUCCESS,
    )

    with patch(
        "tracecat.audit.service.httpx.AsyncClient",
        return_value=async_client_context_mock,
    ) as async_client_ctor:
        await audit_service._post_event(webhook_url=webhook_url, payload=event)
        await flush_audit_deliveries()

    async_client_ctor.assert_called_once_with(timeout=10.0, verify=False)
    assert client_mock.post.await_count == 1
    args = client_mock.post.await_args.args
    kwargs = client_mock.post.await_args.kwargs
    assert args[0] == webhook_url
    assert kwargs["headers"] == {"X-Custom-Header": "custom-value"}
    assert kwargs["json"]["custom"] == "yes"
    assert kwargs["json"]["resource_type"] == "organization"
    assert kwargs["json"]["actor_label"] == "user@example.com"


@pytest.mark.anyio
async def test_post_event_wraps_payload_when_attribute_configured(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    webhook_url = "https://example.com/audit"
    monkeypatch.setattr(
        audit_service,
        "_get_custom_headers",
        AsyncMock(return_value={"X-Custom-Header": "custom-value"}),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_custom_payload",
        AsyncMock(return_value={"custom": "yes"}),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_verify_ssl",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_payload_attribute",
        AsyncMock(return_value="event"),
    )

    response_mock = MagicMock()
    response_mock.raise_for_status = MagicMock()
    client_mock = AsyncMock()
    client_mock.post = AsyncMock(return_value=response_mock)
    async_client_context_mock = AsyncMock()
    async_client_context_mock.__aenter__.return_value = client_mock
    async_client_context_mock.__aexit__.return_value = None

    event = AuditEvent(
        organization_id=uuid.uuid4(),
        workspace_id=None,
        actor_type=AuditEventActor.USER,
        actor_id=uuid.uuid4(),
        actor_label="user@example.com",
        resource_type="workflow",
        resource_id=uuid.uuid4(),
        action="create",
        status=AuditEventStatus.SUCCESS,
    )

    with patch(
        "tracecat.audit.service.httpx.AsyncClient",
        return_value=async_client_context_mock,
    ) as async_client_ctor:
        await audit_service._post_event(webhook_url=webhook_url, payload=event)
        await flush_audit_deliveries()

    async_client_ctor.assert_called_once_with(timeout=10.0, verify=True)
    kwargs = client_mock.post.await_args.kwargs
    wrapped_payload = kwargs["json"]
    assert set(wrapped_payload.keys()) == {"event"}
    assert wrapped_payload["event"]["custom"] == "yes"
    assert wrapped_payload["event"]["actor_label"] == "user@example.com"


@pytest.mark.anyio
async def test_post_event_failure_does_not_log_webhook_url(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    webhook_url = "https://secret-host.example.com/audit-hook"
    monkeypatch.setattr(
        audit_service, "_get_custom_headers", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        audit_service, "_get_custom_payload", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(audit_service, "_get_verify_ssl", AsyncMock(return_value=True))
    monkeypatch.setattr(
        audit_service, "_get_payload_attribute", AsyncMock(return_value=None)
    )

    client_mock = AsyncMock()
    client_mock.post = AsyncMock(
        side_effect=httpx.ConnectError(f"cannot connect to {webhook_url}")
    )
    async_client_context_mock = AsyncMock()
    async_client_context_mock.__aenter__.return_value = client_mock
    async_client_context_mock.__aexit__.return_value = None

    event = AuditEvent(
        organization_id=uuid.uuid4(),
        workspace_id=None,
        actor_type=AuditEventActor.USER,
        actor_id=uuid.uuid4(),
        actor_label="user@example.com",
        resource_type="workflow",
        resource_id=uuid.uuid4(),
        action="create",
        status=AuditEventStatus.SUCCESS,
    )

    warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(msg: str, **kwargs: object) -> None:
        warnings.append((msg, kwargs))

    with (
        patch(
            "tracecat.audit.service.httpx.AsyncClient",
            return_value=async_client_context_mock,
        ),
        patch("tracecat.audit.service.logger.warning", side_effect=capture_warning),
    ):
        await audit_service._post_event(webhook_url=webhook_url, payload=event)
        await flush_audit_deliveries()

    assert client_mock.post.await_count == 1
    delivery_warnings = [
        kwargs for m, kwargs in warnings if m == "Failed to deliver audit webhook"
    ]
    assert delivery_warnings
    for kwargs in delivery_warnings:
        # The URL is never logged in any form.
        assert "webhook_url" not in kwargs
        # The delivery path logs no exception text, so the URL embedded in the
        # exception message never reaches the log either.
        assert "error" not in kwargs
        assert webhook_url not in repr(kwargs)
        assert kwargs["error_type"] == "ConnectError"
    # No part of the URL may appear through any current or future log field.
    assert webhook_url not in str(warnings)
    assert "secret-host" not in str(warnings)


@pytest.mark.anyio
async def test_create_event_settings_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    """Audit is best-effort even for direct create_event callers.

    A failed settings lookup in _build_delivery must be swallowed, not abort
    the audited operation, and must not leak the sink URL.
    """
    monkeypatch.setattr(
        audit_service,
        "_get_webhook_url",
        AsyncMock(return_value="https://secret-host.example.com/audit-hook"),
    )
    monkeypatch.setattr(
        audit_service,
        "_get_custom_headers",
        AsyncMock(side_effect=SQLAlchemyError("settings lookup failed")),
    )
    monkeypatch.setattr(audit_service, "_get_actor_label", AsyncMock(return_value=None))
    spawn = MagicMock()
    monkeypatch.setattr(audit_service_module, "_spawn_delivery", spawn)
    logger_mock = MagicMock()
    monkeypatch.setattr(audit_service, "logger", logger_mock)

    await audit_service.create_event(resource_type="workflow", action="update")

    spawn.assert_not_called()
    resolution_warnings = [
        call
        for call in logger_mock.warning.call_args_list
        if call.args and call.args[0] == "Failed to resolve audit webhook delivery"
    ]
    assert len(resolution_warnings) == 1
    assert resolution_warnings[0].kwargs == {"error_type": "SQLAlchemyError"}
    assert "secret-host" not in str(logger_mock.warning.call_args_list)


def _delivery(tag: str, url: str = "https://example.com/audit") -> _AuditDelivery:
    """Build a minimal delivery whose resource_id doubles as an identifying tag."""
    return _AuditDelivery(
        webhook_url=url,
        request_payload={"resource_id": tag},
        headers=None,
        verify_ssl=True,
        resource_type=cast(Any, "workflow"),
        action=cast(Any, "update"),
    )


async def flush_audit_deliveries() -> None:
    """Wait for this loop's in-flight audit deliveries."""
    loop = asyncio.get_running_loop()
    tasks = [t for t in audit_service_module._delivery_tasks if t.get_loop() is loop]
    if tasks:
        await asyncio.gather(*tasks)


async def _cancel_loop_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Cancel and await this loop's delivery tasks so they can't leak."""
    tasks = [t for t in audit_service_module._delivery_tasks if t.get_loop() is loop]
    for task in tasks:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
async def fresh_delivery_tasks() -> AsyncIterator[None]:
    """Isolate _delivery_tasks for the current loop and restore afterward.

    _delivery_tasks is a module global and pytest-asyncio may reuse a loop
    across tests, so a leaked in-flight task would pollute later tests.
    """
    loop = asyncio.get_running_loop()
    await _cancel_loop_tasks(loop)
    yield
    await _cancel_loop_tasks(loop)


@respx.mock
@pytest.mark.anyio
async def test_deliver_failure_does_not_affect_other_deliveries(
    fresh_delivery_tasks: None,
) -> None:
    """A failed HTTP delivery must not block, drop, or cancel other deliveries.

    The failure is injected at the transport layer: _deliver itself must never
    raise, or the unretrieved task exception would surface as loop noise.
    """
    respx.post("https://fail.example.com/audit").mock(
        side_effect=httpx.ConnectError("boom")
    )
    ok_route = respx.post("https://example.com/audit").mock(
        return_value=httpx.Response(200)
    )

    warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(msg: str, **kwargs: object) -> None:
        warnings.append((msg, kwargs))

    with patch("tracecat.audit.service.logger.warning", side_effect=capture_warning):
        _spawn_delivery(_delivery("first", url="https://fail.example.com/audit"))
        _spawn_delivery(_delivery("second"))
        _spawn_delivery(_delivery("third"))
        await flush_audit_deliveries()

    # Concurrent tasks: no ordering guarantee, but nothing is lost.
    tags = {
        orjson.loads(call.request.content)["resource_id"] for call in ok_route.calls
    }
    assert tags == {"second", "third"}
    assert [
        (msg, kwargs.get("error_type"), kwargs.get("status_code"))
        for msg, kwargs in warnings
    ] == [("Failed to deliver audit webhook", "ConnectError", None)]


@respx.mock
@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_deliver_http_status_failures_log_status_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    fresh_delivery_tasks: None,
    status_code: int,
) -> None:
    """HTTP errors log HTTPStatusError + real status; never URL/exc text/payload."""
    webhook_url = "https://secret-host.example.com/audit-hook"
    payload_marker = "payload-secret-marker"
    respx.post(webhook_url).mock(return_value=httpx.Response(status_code))

    warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(msg: str, **kwargs: object) -> None:
        warnings.append((msg, kwargs))

    delivery = _AuditDelivery(
        webhook_url=webhook_url,
        request_payload={"resource_id": payload_marker},
        headers=None,
        verify_ssl=True,
        resource_type=cast(Any, "workflow"),
        action=cast(Any, "update"),
    )

    with patch("tracecat.audit.service.logger.warning", side_effect=capture_warning):
        _spawn_delivery(delivery)
        await flush_audit_deliveries()

    delivery_warnings = [
        kwargs for m, kwargs in warnings if m == "Failed to deliver audit webhook"
    ]
    assert len(delivery_warnings) == 1
    kwargs = delivery_warnings[0]
    assert kwargs["error_type"] == "HTTPStatusError"
    assert kwargs["status_code"] == status_code
    # No sink URL, exception text, or payload contents anywhere in the logs.
    all_logs = repr(warnings)
    assert webhook_url not in all_logs
    assert payload_marker not in all_logs
    assert str(status_code) in all_logs  # status is present, only as the field


@pytest.mark.anyio
async def test_slow_delivery_does_not_block_later_deliveries(
    fresh_delivery_tasks: None,
) -> None:
    """No head-of-line blocking: later deliveries complete while one is stuck."""
    delivered: list[str] = []
    gate = asyncio.Event()

    async def gated_deliver(delivery: _AuditDelivery) -> None:
        tag = delivery.request_payload["resource_id"]
        if tag == "first":
            await gate.wait()
        delivered.append(tag)

    with patch("tracecat.audit.service._deliver", gated_deliver):
        _spawn_delivery(_delivery("first"))
        _spawn_delivery(_delivery("second"))
        _spawn_delivery(_delivery("third"))

        # Later deliveries complete while "first" is still gated.
        while len(delivered) < 2:
            await asyncio.sleep(0)
        assert sorted(delivered) == ["second", "third"]

        gate.set()
        await flush_audit_deliveries()

    assert sorted(delivered) == ["first", "second", "third"]


@pytest.mark.anyio
async def test_decorator_delivers_attempt_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    fresh_delivery_tasks: None,
) -> None:
    """Through the real @audit_log path, both ATTEMPT and terminal are delivered.

    Arrival order at the sink is not guaranteed; consumers order by event
    timestamp, not delivery order.
    """
    webhook_url = "https://example.com/audit"
    received: list[str] = []

    async def get_webhook_url(_self: AuditService) -> str:
        return webhook_url

    async def get_actor_label(_self: AuditService) -> str | None:
        return None

    async def deliver(delivery: _AuditDelivery) -> None:
        received.append(delivery.request_payload["status"])

    monkeypatch.setattr(AuditService, "_get_webhook_url", get_webhook_url)
    monkeypatch.setattr(AuditService, "_get_actor_label", get_actor_label)
    monkeypatch.setattr(
        AuditService, "_get_custom_headers", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        AuditService, "_get_custom_payload", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(AuditService, "_get_verify_ssl", AsyncMock(return_value=True))
    monkeypatch.setattr(
        AuditService, "_get_payload_attribute", AsyncMock(return_value=None)
    )

    service = _AuditedService(AsyncMock(), role)
    token = ctx_role.set(role)
    try:
        with patch("tracecat.audit.service._deliver", deliver):
            assert await service.mutate(uuid.uuid4()) == "result"
            await flush_audit_deliveries()
    finally:
        ctx_role.reset(token)

    assert sorted(received) == ["ATTEMPT", "SUCCESS"]


@pytest.mark.anyio
async def test_settings_resolution_failure_is_non_fatal_and_leaks_no_url(
    monkeypatch: pytest.MonkeyPatch,
    role: Role,
    fresh_delivery_tasks: None,
) -> None:
    """A failing settings read inside _build_delivery must not break the op."""
    webhook_url = "https://secret-host.example.com/audit-hook"
    delivered: list[_AuditDelivery] = []

    async def get_webhook_url(_self: AuditService) -> str:
        return webhook_url

    async def get_actor_label(_self: AuditService) -> str | None:
        return None

    async def broken_verify_ssl(_self: AuditService) -> bool:
        raise RuntimeError("settings backend down")

    async def deliver(delivery: _AuditDelivery) -> None:
        delivered.append(delivery)

    monkeypatch.setattr(AuditService, "_get_webhook_url", get_webhook_url)
    monkeypatch.setattr(AuditService, "_get_actor_label", get_actor_label)
    monkeypatch.setattr(
        AuditService, "_get_custom_headers", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        AuditService, "_get_custom_payload", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(AuditService, "_get_verify_ssl", broken_verify_ssl)
    monkeypatch.setattr(
        AuditService, "_get_payload_attribute", AsyncMock(return_value=None)
    )

    warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(msg: str, **kwargs: object) -> None:
        warnings.append((msg, kwargs))

    service = _AuditedService(AsyncMock(), role)
    token = ctx_role.set(role)
    try:
        with (
            patch("tracecat.audit.service._deliver", deliver),
            patch("tracecat.audit.logger.logger.warning", side_effect=capture_warning),
            patch("tracecat.audit.service.logger.warning", side_effect=capture_warning),
        ):
            # The decorated business op still succeeds despite settings failure.
            assert await service.mutate(uuid.uuid4()) == "result"
            await flush_audit_deliveries()
    finally:
        ctx_role.reset(token)

    # No delivery occurs when settings resolution raises.
    assert delivered == []
    # No warning leaks the sink URL.
    assert webhook_url not in repr(warnings)


@pytest.mark.anyio
async def test_spawn_delivery_drops_past_in_flight_cap(
    monkeypatch: pytest.MonkeyPatch,
    fresh_delivery_tasks: None,
) -> None:
    """Past the in-flight cap, spawns shed with a warning; capacity frees on completion."""
    monkeypatch.setattr(audit_service_module, "_MAX_IN_FLIGHT_DELIVERIES", 2)
    delivered: list[str] = []
    gate = asyncio.Event()

    async def gated_deliver(delivery: _AuditDelivery) -> None:
        await gate.wait()
        delivered.append(delivery.request_payload["resource_id"])

    warnings: list[tuple[str, dict[str, object]]] = []

    def capture_warning(msg: str, **kwargs: object) -> None:
        warnings.append((msg, kwargs))

    with (
        patch("tracecat.audit.service._deliver", gated_deliver),
        patch("tracecat.audit.service.logger.warning", side_effect=capture_warning),
    ):
        _spawn_delivery(_delivery("first"))
        _spawn_delivery(_delivery("second"))
        # Cap reached while both are gated in flight; this one is shed.
        _spawn_delivery(_delivery("shed", url="https://drop-host.example.com/hook"))

        gate.set()
        await flush_audit_deliveries()

        # Completions freed capacity; later spawns deliver again.
        _spawn_delivery(_delivery("after"))
        await flush_audit_deliveries()

    assert sorted(delivered) == ["after", "first", "second"]
    drop_warnings = [
        kwargs
        for m, kwargs in warnings
        if m == "Dropped audit webhook delivery; in-flight limit reached"
    ]
    assert len(drop_warnings) == 1
    dropped = drop_warnings[0]
    assert dropped["resource_type"] == "workflow"
    assert dropped["action"] == "update"
    assert dropped["max_in_flight"] == 2
    # No payload contents or sink URL on the drop-log boundary.
    all_logs = repr(warnings)
    assert "drop-host" not in all_logs
    assert "resource_id" not in repr(dropped)


@pytest.mark.anyio
async def test_spawn_delivery_evicts_closed_loop_tasks(
    fresh_delivery_tasks: None,
) -> None:
    """Regression: tasks stranded on closed loops are evicted; no unbounded growth.

    A task whose loop closed before it ran never fires its done callback, so
    _spawn_delivery must sweep those refs out of the module set.
    """
    tasks = audit_service_module._delivery_tasks

    # Seed stranded refs whose loops are closed; their callbacks can never run.
    stranded: list[Any] = []
    for _ in range(5):
        dead_loop = asyncio.new_event_loop()
        dead_loop.close()
        fake_task = MagicMock()
        fake_task.get_loop.return_value = dead_loop
        stranded.append(fake_task)
        tasks.add(cast(Any, fake_task))

    async def deliver(delivery: _AuditDelivery) -> None:
        return None

    with patch("tracecat.audit.service._deliver", deliver):
        _spawn_delivery(_delivery("live"))
        await flush_audit_deliveries()

    for fake_task in stranded:
        assert fake_task not in tasks


@pytest.mark.anyio
async def test_audit_log_organization_invitation_create_uses_returned_id(
    role: Role,
) -> None:
    invitation_id = uuid.uuid4()

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="organization_invitation", action="create")
        async def create_invitation(self):
            return SimpleNamespace(id=invitation_id)

    service = MockService()
    token = ctx_role.set(role)
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append(kwargs)

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            await service.create_invitation()
    finally:
        ctx_role.reset(token)

    success_call = next(
        call
        for call in create_event_calls
        if call["status"] == AuditEventStatus.SUCCESS
    )
    assert success_call["resource_id"] == invitation_id


@pytest.mark.anyio
async def test_audit_log_explicit_invitation_id_overrides_result_id(
    role: Role,
) -> None:
    invitation_id = uuid.uuid4()
    unrelated_result_id = uuid.uuid4()

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(
            resource_type="organization_invitation",
            action="revoke",
            resource_id_attr="invitation_id",
        )
        async def revoke_invitation(self, invitation_id: uuid.UUID):
            return SimpleNamespace(id=unrelated_result_id)

    service = MockService()
    token = ctx_role.set(role)
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append(kwargs)

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            await service.revoke_invitation(invitation_id)
    finally:
        ctx_role.reset(token)

    resource_ids = [call["resource_id"] for call in create_event_calls]
    assert resource_ids == [invitation_id, invitation_id]


@pytest.mark.anyio
async def test_audit_log_inner_function_failure_logs_failure_event(role: Role):
    """Test that when inner function raises exception, failure event is logged."""

    class MockService(BaseService):
        service_name = "mock"

        def __init__(self):
            super().__init__(AsyncMock())

        @audit_log(resource_type="workflow", action="create")
        async def create_workflow(self, workflow_id: uuid.UUID):
            raise ValueError("Workflow creation failed")

    service = MockService()
    token = ctx_role.set(role)

    create_event_calls = []
    audit_sessions: list[object | None] = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append((args, kwargs))

    @asynccontextmanager
    async def mock_with_session(
        cls,
        _role=None,
        *,
        session=None,
        audit_sink=None,
    ):
        del cls, audit_sink
        audit_sessions.append(session)
        yield SimpleNamespace(create_event=mock_create_event)

    try:
        with patch.object(AuditService, "with_session", classmethod(mock_with_session)):
            with pytest.raises(ValueError, match="Workflow creation failed"):
                await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert len(create_event_calls) >= 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.ATTEMPT
    assert len(create_event_calls) >= 2
    assert create_event_calls[1][1]["status"] == AuditEventStatus.FAILURE
    assert audit_sessions == [service.session, None]


@pytest.mark.anyio
async def test_audit_log_ok_false_logs_failure_event(role: Role) -> None:
    class MockService(BaseService):
        service_name = "mock"

        def __init__(self) -> None:
            super().__init__(AsyncMock())

        @audit_log(resource_type="workflow", action="publish")
        async def publish_workflow(self, workflow_id: uuid.UUID):
            return SimpleNamespace(ok=False)

    token = ctx_role.set(role)
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append(kwargs)

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            await MockService().publish_workflow(uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert [call["status"] for call in create_event_calls] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.FAILURE,
    ]


@pytest.mark.anyio
async def test_audit_log_attempt_logging_failure_still_executes_function(role: Role):
    """Test that when attempt logging fails, function still executes."""

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create")
        async def create_workflow(self, workflow_id: uuid.UUID):
            return {"id": str(workflow_id), "status": "created"}

    service = MockService()
    token = ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        if kwargs.get("status") == AuditEventStatus.ATTEMPT:
            raise Exception("Attempt logging failed")
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            result = await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert result["status"] == "created"
    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.SUCCESS


@pytest.mark.anyio
async def test_audit_log_success_logging_failure_still_returns_result(role: Role):
    """Test that when success logging fails, function still returns result."""
    expected_result = {"id": str(uuid.uuid4()), "status": "created"}

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create")
        async def create_workflow(self, workflow_id: uuid.UUID):
            return expected_result

    service = MockService()
    token = ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        if kwargs.get("status") == AuditEventStatus.SUCCESS:
            raise Exception("Success logging failed")
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            result = await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert result == expected_result
    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.ATTEMPT


@pytest.mark.anyio
async def test_audit_log_failure_logging_failure_still_raises_original_exception(
    role: Role,
) -> None:
    """Test that when failure logging fails, original exception is still raised."""

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create")
        async def create_workflow(self, workflow_id: uuid.UUID):
            raise ValueError("Original error")

    service = MockService()
    token = ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        if kwargs.get("status") == AuditEventStatus.FAILURE:
            raise Exception("Failure logging failed")
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            with pytest.raises(ValueError, match="Original error"):
                await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.ATTEMPT


def test_sanitize_audit_metadata_keeps_batch_operation_keys():
    id_a, id_b = str(uuid.uuid4()), str(uuid.uuid4())
    data = {
        "is_batch": True,
        "case_ids": [id_a, id_b],
        "case_count": 2,
        "succeeded_count": 1,
        "failed_count": 1,
        "description": "raw content must be dropped",
    }
    assert sanitize_audit_metadata(data) == {
        "is_batch": True,
        "case_ids": [id_a, id_b],
        "case_count": 2,
        "succeeded_count": 1,
        "failed_count": 1,
    }


def test_sanitize_audit_metadata_drops_non_string_id_list_items():
    id_a = str(uuid.uuid4())
    # Deliberately ill-typed items to exercise the runtime guard.
    data = cast(AuditMetadata, {"case_ids": [id_a, 42, None]})
    assert sanitize_audit_metadata(data) == {"case_ids": [id_a]}
