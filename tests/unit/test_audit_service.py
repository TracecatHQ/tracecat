from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
import respx

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import AccessLevel, Role


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
