from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import Response

from tracecat.audit.constants import (
    AUDIT_DELIVERY_STREAMS_KEY,
    audit_delivery_stream_key,
)
from tracecat.audit.delivery import AuditDeliveryConsumer
from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import (
    AuditService,
    AuditWebhookConfig,
    build_audit_webhook_request,
)
from tracecat.audit.types import AuditEvent
from tracecat.auth.types import PlatformRole, Role
from tracecat.auth.users import UserManager
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.cases.service import CaseCommentsService
from tracecat.contexts import ctx_client_ip, ctx_request_id, ctx_role, ctx_user_agent
from tracecat.middleware.request import RequestLoggingMiddleware


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


def _sample_audit_event(
    *, organization_id: uuid.UUID | None = None, resource_id: str | None = None
) -> AuditEvent:
    return AuditEvent(
        organization_id=organization_id or uuid.uuid4(),
        workspace_id=None,
        actor_type=AuditEventActor.USER,
        actor_id=uuid.uuid4(),
        actor_label="user@example.com",
        resource_type="workflow",
        resource_id=resource_id or str(uuid.uuid4()),
        action="create",
        status=AuditEventStatus.SUCCESS,
    )


@pytest.mark.anyio
async def test_request_logging_middleware_truncates_request_id() -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(logger=SimpleNamespace(debug=MagicMock()))
    )
    middleware = RequestLoggingMiddleware(cast(Any, app))
    long_request_id = f"  {'x' * 200}  "
    captured: dict[str, str | None] = {}

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def call_next(request: Request) -> Response:
        captured["request_id"] = ctx_request_id.get()
        return Response("ok")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "path": "/",
            "query_string": b"",
            "headers": [(b"x-request-id", long_request_id.encode())],
            "app": app,
        },
        receive=receive,
    )

    await middleware.dispatch(request, call_next)

    assert captured["request_id"] == "x" * 128


@pytest.mark.anyio
async def test_create_event_skips_without_webhook(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    monkeypatch.setattr(
        audit_service, "_has_configured_sink", AsyncMock(return_value=False)
    )
    publish_mock = AsyncMock()
    monkeypatch.setattr(audit_service, "_publish_event", publish_mock)

    await audit_service.create_event(resource_type="workflow", action="create")

    publish_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_create_event_queues_audit_event_envelope(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    resource_id = uuid.uuid4()
    request_id = "req-123"
    user_agent = "tracecat-test"
    fake_redis = MagicMock()
    fake_redis.xadd_audit = AsyncMock(return_value="1-0")
    fake_redis.sadd_audit = AsyncMock(return_value=1)
    monkeypatch.setattr(
        audit_service, "_has_configured_sink", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "tracecat.audit.service.get_redis_client", AsyncMock(return_value=fake_redis)
    )
    mock_user = MagicMock()
    mock_user.email = "user@example.com"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=mock_user)
    audit_service.session.execute = AsyncMock(return_value=result_mock)
    request_id_token = ctx_request_id.set(request_id)
    user_agent_token = ctx_user_agent.set(user_agent)

    try:
        await audit_service.create_event(
            resource_type="workflow",
            action="create",
            resource_id=resource_id,
            status=AuditEventStatus.SUCCESS,
        )
    finally:
        ctx_user_agent.reset(user_agent_token)
        ctx_request_id.reset(request_id_token)

    assert isinstance(audit_service.role, Role)
    stream_key = audit_delivery_stream_key(
        "organization", audit_service.role.organization_id
    )
    fake_redis.xadd_audit.assert_awaited_once()
    assert fake_redis.xadd_audit.await_args.args[0] == stream_key
    fields = fake_redis.xadd_audit.await_args.args[1]
    payload = AuditEvent.model_validate_json(fields["event"])
    assert payload.version == 1
    assert payload.source == "api"
    assert payload.resource_type == "workflow"
    assert payload.resource_id == str(resource_id)
    assert payload.action == "create"
    assert payload.status == AuditEventStatus.SUCCESS
    assert payload.actor_label == "user@example.com"
    assert payload.user_agent == user_agent
    assert payload.request_id == request_id
    fake_redis.sadd_audit.assert_awaited_once_with(
        AUDIT_DELIVERY_STREAMS_KEY, stream_key
    )


@pytest.mark.anyio
async def test_create_event_preserves_metadata_data_in_queued_event(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    publish_mock = AsyncMock()
    data = {"case_id": str(uuid.uuid4()), "redacted_fields": ["content"]}
    monkeypatch.setattr(
        audit_service, "_has_configured_sink", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        audit_service, "_get_actor_label", AsyncMock(return_value="user@example.com")
    )
    monkeypatch.setattr(audit_service, "_publish_event", publish_mock)

    await audit_service.create_event(
        resource_type="case_comment",
        action="create",
        resource_id=uuid.uuid4(),
        status=AuditEventStatus.SUCCESS,
        data=data,
    )

    assert publish_mock.await_count == 1
    assert publish_mock.await_args is not None
    payload = publish_mock.await_args.args[0]
    assert payload.resource_type == "case_comment"
    assert payload.data == data


@pytest.mark.anyio
async def test_create_event_drops_redis_errors_without_raising(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    logger_mock = SimpleNamespace(debug=MagicMock(), warning=MagicMock())
    audit_service.logger = cast(Any, logger_mock)
    monkeypatch.setattr(
        audit_service, "_has_configured_sink", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        audit_service, "_publish_event", AsyncMock(side_effect=RuntimeError("down"))
    )

    await audit_service.create_event(
        resource_type="workflow",
        action="create",
        include_actor_label=False,
    )

    logger_mock.warning.assert_called_once()
    assert logger_mock.warning.call_args.kwargs["event_id"]


@pytest.mark.anyio
async def test_create_event_can_emit_sanitized_platform_org_auth_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_ip = "203.0.113.10"
    role = Role(
        type="user",
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )
    audit_service = AuditService(AsyncMock(), role=role, audit_sink="platform")
    publish_mock = AsyncMock()
    actor_label_mock = AsyncMock()
    monkeypatch.setattr(
        audit_service, "_has_configured_sink", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(audit_service, "_get_actor_label", actor_label_mock)
    monkeypatch.setattr(audit_service, "_publish_event", publish_mock)
    token = ctx_client_ip.set(client_ip)

    try:
        await audit_service.create_event(
            resource_type="auth",
            action="sign_in",
            resource_id=role.user_id,
            data={"auth_method": "saml"},
            include_actor_label=False,
        )
    finally:
        ctx_client_ip.reset(token)

    actor_label_mock.assert_not_awaited()
    assert publish_mock.await_args is not None
    payload = publish_mock.await_args.args[0]
    assert payload.organization_id == role.organization_id
    assert payload.actor_id == role.user_id
    assert payload.actor_label is None
    assert payload.ip_address == client_ip
    assert payload.data == {"auth_method": "saml"}


def _capture_audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

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
    return calls


@pytest.mark.parametrize(
    "case", ["org-success", "superuser-success", "org-failure", "contextless"]
)
@pytest.mark.anyio
async def test_auth_audit_recipient_and_sanitized_payload_cases(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: str,
) -> None:
    is_superuser = case == "superuser-success"
    org_scoped = case.startswith("org-")
    reason = "saml_enforced" if case == "org-failure" else None
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    user = MagicMock(id=user_id, is_superuser=is_superuser)
    calls = _capture_audit_calls(monkeypatch)
    manager = UserManager.__new__(UserManager)
    kwargs: dict[str, Any] = {
        "user": user,
        "auth_method": "password",
        "org_ids": {org_id} if org_scoped else set(),
    }
    if reason:
        kwargs["reason"] = reason

    await manager._emit_auth_audit(**kwargs)

    assert [call["audit_sink"] for call in calls] == (
        ["platform", "organization"] if org_scoped else ["platform"]
    )
    expected_data = {"auth_method": "password"}
    if reason:
        expected_data["reason"] = reason
    expected_event = {
        "resource_type": "auth",
        "action": "sign_in",
        "resource_id": user_id,
        "data": expected_data,
        "include_actor_label": False,
    }
    if reason:
        expected_event["status"] = AuditEventStatus.FAILURE
    for call in calls:
        role = call["role"]
        assert isinstance(
            role, PlatformRole if is_superuser and not org_scoped else Role
        )
        assert role.user_id == user_id
        assert getattr(role, "organization_id", None) == (
            org_id if org_scoped else None
        )
        assert call["kwargs"] == expected_event


@pytest.mark.parametrize("case", ["contextless", "password", "oauth", "org-scoped"])
@pytest.mark.anyio
async def test_on_after_login_method_and_org_attribution_cases(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: str,
) -> None:
    path, state_method, org_scoped, expected_method = {
        "contextless": (None, None, False, "unknown"),
        "password": ("/auth/login", None, False, "password"),
        "oauth": ("/auth/oauth/callback", "okta", False, "okta"),
        "org-scoped": (None, None, True, "unknown"),
    }[case]
    org_id = uuid.uuid4()
    user = MagicMock(id=uuid.uuid4(), email="super@example.com", is_superuser=True)
    request = None
    if path:
        state = SimpleNamespace()
        if state_method:
            state.tracecat_auth_method = state_method
        request = cast(
            Any, SimpleNamespace(url=SimpleNamespace(path=path), state=state)
        )
    calls = _capture_audit_calls(monkeypatch)
    manager = UserManager.__new__(UserManager)
    manager.logger = MagicMock()
    manager.user_db = MagicMock(update=AsyncMock())
    manager._list_user_org_ids = AsyncMock(
        side_effect=AssertionError("login audit must not re-derive memberships")
    )

    await manager.on_after_login(
        user,
        request=request,
        response=None,
        organization_id=org_id if org_scoped else None,
    )

    expected_sinks = (
        ["platform", "platform", "organization"] if org_scoped else ["platform"]
    )
    assert [call["audit_sink"] for call in calls] == expected_sinks
    assert {call["kwargs"]["data"]["auth_method"] for call in calls} == {
        expected_method
    }
    assert {
        call["role"].organization_id for call in calls if isinstance(call["role"], Role)
    } == ({org_id} if org_scoped else set())
    manager._list_user_org_ids.assert_not_awaited()


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


def test_build_audit_webhook_request_uses_headers_and_custom_payload() -> None:
    event = AuditEvent(
        organization_id=uuid.uuid4(),
        workspace_id=None,
        actor_type=AuditEventActor.USER,
        actor_id=uuid.uuid4(),
        actor_label="user@example.com",
        resource_type="workflow",
        resource_id=str(uuid.uuid4()),
        action="create",
        status=AuditEventStatus.SUCCESS,
    )
    webhook_config = AuditWebhookConfig(
        webhook_url="https://example.com/audit",
        custom_headers={"X-Custom-Header": "custom-value"},
        custom_payload={
            "id": "customer-id",
            "version": 99,
            "resource_type": "organization",
            "custom": "yes",
        },
        verify_ssl=False,
    )

    body, headers = build_audit_webhook_request(payload=event, config=webhook_config)

    assert headers == {
        "X-Custom-Header": "custom-value",
        "X-Tracecat-Event-Id": str(event.id),
        "X-Tracecat-Timestamp": event.created_at.isoformat(),
    }
    assert body["id"] == str(event.id)
    assert body["version"] == 1
    assert body["custom"] == "yes"
    assert body["resource_type"] == "organization"
    assert body["actor_label"] == "user@example.com"


def test_build_audit_webhook_request_wraps_payload_when_attribute_configured() -> None:
    event = AuditEvent(
        organization_id=uuid.uuid4(),
        workspace_id=None,
        actor_type=AuditEventActor.USER,
        actor_id=uuid.uuid4(),
        actor_label="user@example.com",
        resource_type="workflow",
        resource_id=str(uuid.uuid4()),
        action="create",
        status=AuditEventStatus.SUCCESS,
    )
    webhook_config = AuditWebhookConfig(
        webhook_url="https://example.com/audit",
        custom_headers={"X-Custom-Header": "custom-value"},
        custom_payload={"custom": "yes"},
        payload_attribute="event",
    )

    body, headers = build_audit_webhook_request(payload=event, config=webhook_config)

    assert set(body.keys()) == {"event"}
    assert body["event"]["custom"] == "yes"
    assert body["event"]["actor_label"] == "user@example.com"
    assert headers["X-Tracecat-Event-Id"] == str(event.id)
    assert headers["X-Tracecat-Timestamp"] == event.created_at.isoformat()


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

    assert len(create_event_calls) == 1
    assert create_event_calls[0]["status"] == AuditEventStatus.SUCCESS
    success_call = next(
        call
        for call in create_event_calls
        if call["status"] == AuditEventStatus.SUCCESS
    )
    assert success_call["resource_id"] == str(invitation_id)


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
    assert resource_ids == [str(invitation_id)]


@pytest.mark.anyio
async def test_audit_log_emit_attempt_emits_attempt_then_terminal(
    role: Role,
) -> None:
    workflow_id = "wf_alias"

    def metadata(self, workflow_id: str, result):
        return {"workflow_id": workflow_id, "result_id": result.id}

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(
            resource_type="workflow",
            action="create",
            data_fn=metadata,
            emit_attempt=True,
        )
        async def create_workflow(self, workflow_id: str):
            return SimpleNamespace(id="created")

    service = MockService()
    token = ctx_role.set(role)
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append(kwargs)

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            await service.create_workflow(workflow_id)
    finally:
        ctx_role.reset(token)

    assert len(create_event_calls) == 2
    assert create_event_calls[0]["status"] == AuditEventStatus.ATTEMPT
    assert "data" not in create_event_calls[0]
    assert create_event_calls[1]["status"] == AuditEventStatus.SUCCESS
    assert create_event_calls[1]["resource_id"] == "wf_alias"
    assert create_event_calls[1]["data"] == {
        "workflow_id": workflow_id,
        "result_id": "created",
    }


@pytest.mark.anyio
async def test_audit_log_data_fn_failure_emits_none_data(role: Role) -> None:
    def bad_metadata(**kwargs):
        raise RuntimeError("metadata failed")

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create", data_fn=bad_metadata)
        async def create_workflow(self, workflow_id: uuid.UUID):
            return SimpleNamespace(workflow_id=workflow_id)

    service = MockService()
    token = ctx_role.set(role)
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append(kwargs)

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            await service.create_workflow(uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert create_event_calls[0]["status"] == AuditEventStatus.SUCCESS
    assert create_event_calls[0]["data"] is None


@pytest.mark.anyio
async def test_audit_log_inner_function_failure_logs_failure_event(role: Role):
    """Test that when inner function raises exception, failure event is logged."""

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create")
        async def create_workflow(self, workflow_id: uuid.UUID):
            raise ValueError("Workflow creation failed")

    service = MockService()
    token = ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            with pytest.raises(ValueError, match="Workflow creation failed"):
                await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.FAILURE


@pytest.mark.anyio
async def test_audit_log_attempt_logging_failure_still_executes_function(role: Role):
    """Test that when attempt logging fails, function still executes."""

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create", emit_attempt=True)
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

        @audit_log(resource_type="workflow", action="create", emit_attempt=True)
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
async def test_audit_delivery_consumer_acks_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    client.delete = AsyncMock(return_value=1)
    client.xack = AsyncMock(return_value=1)
    consumer = AuditDeliveryConsumer(client)
    sink_config = AuditWebhookConfig(webhook_url="https://example.com/audit")
    deliver_event = AsyncMock(return_value=None)
    monkeypatch.setattr(
        consumer, "_get_sink_config", AsyncMock(return_value=sink_config)
    )
    monkeypatch.setattr(consumer, "_deliver_event", deliver_event)

    await consumer._handle_message(
        stream_key, "1-0", {"event": event.model_dump_json()}, attempts=1
    )

    deliver_event.assert_awaited_once()
    client.xack.assert_awaited_once_with(stream_key, consumer.group, ["1-0"])
    client.delete.assert_awaited_once()


@pytest.mark.anyio
async def test_audit_delivery_consumer_acks_on_attempt_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    client.incr_with_expire = AsyncMock(return_value=2)
    client.xack = AsyncMock(return_value=1)
    consumer = AuditDeliveryConsumer(client)
    consumer.max_attempts = 2
    monkeypatch.setattr(
        consumer,
        "_get_sink_config",
        AsyncMock(
            return_value=AuditWebhookConfig(webhook_url="https://example.com/audit")
        ),
    )
    monkeypatch.setattr(
        consumer, "_deliver_event", AsyncMock(side_effect=httpx.HTTPError("failed"))
    )

    await consumer._handle_message(
        stream_key, "1-0", {"event": event.model_dump_json()}, attempts=2
    )

    client.incr_with_expire.assert_awaited_once_with(
        consumer._circuit_key("organization", org_id),
        expire_seconds=consumer.circuit_ttl,
    )
    client.xack.assert_awaited_once_with(stream_key, consumer.group, ["1-0"])


@pytest.mark.anyio
async def test_audit_delivery_consumer_circuit_open_leaves_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.get = AsyncMock(return_value="5")
    client.xack = AsyncMock(return_value=0)
    consumer = AuditDeliveryConsumer(client)
    consumer.circuit_threshold = 5
    get_sink_config = AsyncMock()
    deliver_event = AsyncMock()
    monkeypatch.setattr(consumer, "_get_sink_config", get_sink_config)
    monkeypatch.setattr(consumer, "_deliver_event", deliver_event)

    await consumer._handle_message(
        stream_key, "1-0", {"event": event.model_dump_json()}, attempts=1
    )

    get_sink_config.assert_not_awaited()
    deliver_event.assert_not_awaited()
    client.xack.assert_not_awaited()


@pytest.mark.anyio
async def test_audit_delivery_consumer_records_failures_with_incr() -> None:
    org_id = uuid.uuid4()
    client = MagicMock()
    client.incr_with_expire = AsyncMock(return_value=1)
    consumer = AuditDeliveryConsumer(client)

    await consumer._record_sink_failure("organization", org_id)

    client.incr_with_expire.assert_awaited_once_with(
        consumer._circuit_key("organization", org_id),
        expire_seconds=consumer.circuit_ttl,
    )


@pytest.mark.anyio
async def test_audit_delivery_config_exception_leaves_message_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    client.xack = AsyncMock(return_value=0)
    consumer = AuditDeliveryConsumer(client)
    deliver_event = AsyncMock()
    monkeypatch.setattr(
        consumer,
        "_get_sink_config",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(consumer, "_deliver_event", deliver_event)

    await consumer._handle_message(
        stream_key, "1-0", {"event": event.model_dump_json()}, attempts=1
    )

    deliver_event.assert_not_awaited()
    client.xack.assert_not_awaited()


@pytest.mark.anyio
async def test_audit_delivery_pending_sweep_runs_when_reads_return_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.smembers = AsyncMock(return_value={stream_key})
    client.exists = AsyncMock(return_value=True)
    client.xgroup_create = AsyncMock(return_value=True)
    client.xreadgroup = AsyncMock(
        side_effect=[
            [(stream_key, [("1-0", {"event": event.model_dump_json()})])],
            asyncio.CancelledError(),
        ]
    )
    consumer = AuditDeliveryConsumer(client)
    monkeypatch.setattr(
        "tracecat.audit.delivery.monotonic",
        MagicMock(side_effect=[0.0, consumer._pending_check_interval + 1.0]),
    )
    handle_message = AsyncMock()
    claim_idle_messages = AsyncMock()
    monkeypatch.setattr(consumer, "_handle_message", handle_message)
    monkeypatch.setattr(consumer, "_claim_idle_messages", claim_idle_messages)

    with pytest.raises(asyncio.CancelledError):
        await consumer.run()

    handle_message.assert_awaited_once_with(
        stream_key, "1-0", {"event": event.model_dump_json()}, attempts=1
    )
    claim_idle_messages.assert_awaited_once()


@pytest.mark.anyio
async def test_audit_delivery_consumer_acks_unconfigured_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    client.xack = AsyncMock(return_value=1)
    consumer = AuditDeliveryConsumer(client)
    deliver_event = AsyncMock()
    monkeypatch.setattr(consumer, "_get_sink_config", AsyncMock(return_value=None))
    monkeypatch.setattr(consumer, "_deliver_event", deliver_event)

    await consumer._handle_message(
        stream_key, "1-0", {"event": event.model_dump_json()}, attempts=1
    )

    deliver_event.assert_not_awaited()
    client.xack.assert_awaited_once_with(stream_key, consumer.group, ["1-0"])


@pytest.mark.anyio
async def test_audit_delivery_claim_uses_times_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    stream_key = audit_delivery_stream_key("organization", org_id)
    event = _sample_audit_event(organization_id=org_id)
    client = MagicMock()
    client.smembers = AsyncMock(return_value={stream_key})
    client.exists = AsyncMock(return_value=True)
    client.xgroup_create = AsyncMock(return_value=True)
    client.xpending_range = AsyncMock(
        return_value=[
            {"message_id": "1-0", "times_delivered": 3},
            {"message_id": "2-0", "times_delivered": 1},
        ]
    )
    client.expire = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value=None)
    client.xack = AsyncMock(return_value=1)
    client.xclaim = AsyncMock(
        return_value=[("2-0", {"event": event.model_dump_json()})]
    )
    consumer = AuditDeliveryConsumer(client)
    consumer.max_attempts = 3
    handle_message = AsyncMock()
    monkeypatch.setattr(consumer, "_handle_message", handle_message)

    await consumer._claim_idle_messages()

    client.expire.assert_awaited_once_with(stream_key, consumer.stream_ttl)
    client.xack.assert_awaited_once_with(stream_key, consumer.group, ["1-0"])
    client.xclaim.assert_awaited_once_with(
        stream_key,
        consumer.group,
        consumer.consumer_name,
        consumer.claim_idle_ms,
        ["2-0"],
    )
    handle_message.assert_awaited_once_with(
        stream_key,
        "2-0",
        {"event": event.model_dump_json()},
        attempts=2,
    )


def test_case_comment_audit_data_redacts_content() -> None:
    service = CaseCommentsService.__new__(CaseCommentsService)
    case_id = uuid.uuid4()
    comment_id = uuid.uuid4()

    data = service._comment_audit_data(
        case_id=case_id,
        comment_id=comment_id,
        parent_id=None,
        content="sensitive",
    )

    assert data["case_id"] == str(case_id)
    assert data["comment_id"] == str(comment_id)
    assert data["parent_id"] is None
    assert data["redacted_fields"] == ["content"]
    assert "content" not in data


@pytest.mark.anyio
async def test_audit_log_failure_logging_failure_still_raises_original_exception(
    role: Role,
) -> None:
    """Test that when failure logging fails, original exception is still raised."""

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create", emit_attempt=True)
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
