from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
import respx

from tracecat.audit.enums import AuditEventActor, AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditEvent
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.contexts import ctx_role


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        workspace_id=None,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        workspace_role=None,
        scopes=ADMIN_SCOPES,
    )


@pytest.fixture
def audit_service(role: Role) -> AuditService:
    return AuditService(AsyncMock(), role=role)


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
async def test_create_event_streams_to_webhook(
    monkeypatch: pytest.MonkeyPatch, audit_service: AuditService
) -> None:
    webhook_url = "https://example.com/audit"
    monkeypatch.setattr(
        audit_service, "_get_webhook_url", AsyncMock(return_value=webhook_url)
    )
    mock_user = MagicMock()
    mock_user.email = "user@example.com"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=mock_user)
    audit_service.session.execute = AsyncMock(return_value=result_mock)
    route = respx.post(webhook_url).mock(return_value=httpx.Response(200))

    await audit_service.create_event(
        resource_type="workflow",
        action="create",
        resource_id=uuid.uuid4(),
        status=AuditEventStatus.SUCCESS,
    )

    assert route.called
    payload = orjson.loads(route.calls[0].request.content)
    assert payload["resource_type"] == "workflow"
    assert payload["action"] == "create"
    assert payload["status"] == AuditEventStatus.SUCCESS.value
    assert payload["actor_label"] == "user@example.com"


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

    # Verify attempt was logged
    assert len(create_event_calls) >= 1
    attempt_call = create_event_calls[0]
    assert attempt_call[1]["status"] == AuditEventStatus.ATTEMPT

    # Verify failure was logged
    assert len(create_event_calls) >= 2
    failure_call = create_event_calls[1]
    assert failure_call[1]["status"] == AuditEventStatus.FAILURE


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
        # Fail on attempt, succeed on success
        if kwargs.get("status") == AuditEventStatus.ATTEMPT:
            raise Exception("Attempt logging failed")
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            result = await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    # Verify function executed successfully
    assert result["status"] == "created"

    # Verify success was still logged despite attempt failure
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
        # Succeed on attempt, fail on success
        if kwargs.get("status") == AuditEventStatus.SUCCESS:
            raise Exception("Success logging failed")
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            result = await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    # Verify function returned result despite success logging failure
    assert result == expected_result

    # Verify attempt was logged
    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.ATTEMPT


@pytest.mark.anyio
async def test_audit_log_failure_logging_failure_still_raises_original_exception(
    role: Role,
):
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
        # Succeed on attempt, fail on failure logging
        if kwargs.get("status") == AuditEventStatus.FAILURE:
            raise Exception("Failure logging failed")
        create_event_calls.append((args, kwargs))

    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            with pytest.raises(ValueError, match="Original error"):
                await service.create_workflow(workflow_id=uuid.uuid4())
    finally:
        ctx_role.reset(token)

    # Verify attempt was logged
    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.ATTEMPT
