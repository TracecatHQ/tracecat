from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.logger import (
    AuditCallContext,
    AuditEventDetails,
    audit_log,
)
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditEvent
from tracecat.auth.types import PlatformRole, Role
from tracecat.auth.users import UserManager
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.contexts import ctx_client_ip, ctx_role, ctx_user_agent
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


def _test_audit_details(_context: AuditCallContext) -> AuditEventDetails:
    return AuditEventDetails(data={"changed_fields": ["status"]})


def _broken_audit_result(_context: AuditCallContext, _result: str) -> AuditEventDetails:
    raise ValueError("metadata derivation failed")


class _AuditedService(BaseService):
    service_name = "test_audit"

    def __init__(self, session: AsyncSession, role: Role) -> None:
        super().__init__(session)
        self.role = role

    @audit_log(
        resource_type="workflow",
        action="update",
        attempt_metadata=_test_audit_details,
    )
    async def mutate(self, workflow_id: uuid.UUID, *, fail: bool = False) -> str:
        if fail:
            raise ValueError("operation failed")
        return "result"

    @audit_log(
        resource_type="workflow",
        action="update",
        attempt_metadata=_test_audit_details,
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
        ("service_gate", []),
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
    if scenario == "service_gate":
        audit_role = role.model_copy(update={"type": "service"})
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

    def suppress_audit(_context: AuditCallContext) -> AuditEventDetails:
        return AuditEventDetails(emit=False)

    class SuppressedService(BaseService):
        service_name = "suppressed"

        def __init__(self) -> None:
            super().__init__(AsyncMock())
            self.role = role

        @audit_log(
            resource_type="workflow",
            action="update",
            attempt_metadata=suppress_audit,
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

    client_ip_token = ctx_client_ip.set(client_ip)
    user_agent_token = ctx_user_agent.set(user_agent)
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
        ctx_user_agent.reset(user_agent_token)
        ctx_client_ip.reset(client_ip_token)

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
    client_ip_token = ctx_client_ip.set(client_ip)
    user_agent_token = ctx_user_agent.set(user_agent)

    try:
        await audit_service.create_event(
            resource_type="auth",
            action="sign_in",
            resource_id=role.user_id,
            data={"auth_method": "saml"},
            include_actor_label=False,
        )
    finally:
        ctx_user_agent.reset(user_agent_token)
        ctx_client_ip.reset(client_ip_token)

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
    get_platform_setting = AsyncMock(return_value="https://example.com/audit")
    monkeypatch.setattr(audit_service, "_get_platform_setting", get_platform_setting)

    assert audit_service.audit_sink == "platform"
    assert await audit_service._get_webhook_url() == "https://example.com/audit"
    get_platform_setting.assert_awaited_once_with("audit_webhook_url")


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

    async_client_ctor.assert_called_once_with(timeout=10.0, verify=True)
    kwargs = client_mock.post.await_args.kwargs
    wrapped_payload = kwargs["json"]
    assert set(wrapped_payload.keys()) == {"event"}
    assert wrapped_payload["event"]["custom"] == "yes"
    assert wrapped_payload["event"]["actor_label"] == "user@example.com"


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
