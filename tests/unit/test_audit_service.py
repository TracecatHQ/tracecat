from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
import respx

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.logger import audit_log
from tracecat.audit.service import AuditService
from tracecat.auth.types import AccessLevel, Role
from tracecat.contexts import ctx_role


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        workspace_id=None,
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        access_level=AccessLevel.ADMIN,
        workspace_role=None,
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
async def test_audit_log_inner_function_failure_logs_failure_event(role: Role):
    """Test that when inner function raises exception, failure event is logged."""

    class MockService:
        def __init__(self):
            self.session = AsyncMock()

        @audit_log(resource_type="workflow", action="create")
        async def create_workflow(self, workflow_id: uuid.UUID):
            raise ValueError("Workflow creation failed")

    service = MockService()
    ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        create_event_calls.append((args, kwargs))

    with patch.object(AuditService, "create_event", side_effect=mock_create_event):
        with pytest.raises(ValueError, match="Workflow creation failed"):
            await service.create_workflow(workflow_id=uuid.uuid4())

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
    ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        # Fail on attempt, succeed on success
        if kwargs.get("status") == AuditEventStatus.ATTEMPT:
            raise Exception("Attempt logging failed")
        create_event_calls.append((args, kwargs))

    with patch.object(AuditService, "create_event", side_effect=mock_create_event):
        result = await service.create_workflow(workflow_id=uuid.uuid4())

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
    ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        # Succeed on attempt, fail on success
        if kwargs.get("status") == AuditEventStatus.SUCCESS:
            raise Exception("Success logging failed")
        create_event_calls.append((args, kwargs))

    with patch.object(AuditService, "create_event", side_effect=mock_create_event):
        result = await service.create_workflow(workflow_id=uuid.uuid4())

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
    ctx_role.set(role)

    create_event_calls = []

    async def mock_create_event(*args, **kwargs):
        # Succeed on attempt, fail on failure logging
        if kwargs.get("status") == AuditEventStatus.FAILURE:
            raise Exception("Failure logging failed")
        create_event_calls.append((args, kwargs))

    with patch.object(AuditService, "create_event", side_effect=mock_create_event):
        with pytest.raises(ValueError, match="Original error"):
            await service.create_workflow(workflow_id=uuid.uuid4())

    # Verify attempt was logged
    assert len(create_event_calls) == 1
    assert create_event_calls[0][1]["status"] == AuditEventStatus.ATTEMPT
