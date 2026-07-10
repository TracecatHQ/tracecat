from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditEvent
from tracecat.auth.types import Role
from tracecat.db.models import Webhook, WebhookApiKey
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookCreate, WebhookUpdate
from tracecat.workflow.executions import internal_router
from tracecat.workflow.management import router as workflow_router

type CapturedEvents = list[tuple[object, AuditEvent]]

# fmt: off
CONFIG_CASES = [
    ("create", WebhookCreate(status="online", methods=["GET", "POST"], allowlisted_cidrs=["10.10.0.0/16"], include_headers=True), {"fields": ["allowlisted_cidrs", "include_headers", "methods", "status"], "status": "online", "method_count": 2, "allowlisted_cidr_count": 1, "include_headers": True}),
    ("update", WebhookUpdate(status="offline", methods=[], allowlisted_cidrs=[], include_headers=True), {"fields": ["allowlisted_cidrs", "include_headers", "methods", "status"], "status": "offline", "method_count": 0, "allowlisted_cidr_count": 0, "include_headers": True}),
]
# fmt: on


class _GeneratedKey:
    raw = "tc_wh_secret_raw"
    hashed = "hashed-secret-material"
    salt_b64 = "salt-secret-material"

    def preview(self) -> str:
        return "preview-secret"


class _SensitiveDBError(Exception):
    orig = SimpleNamespace(sqlstate="23505")


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None


def _role(workspace_id: uuid.UUID, *, user: bool = True) -> Role:
    return Role(
        type="user" if user else "service",
        user_id=uuid.uuid4() if user else None,
        organization_id=uuid.uuid4(),
        workspace_id=workspace_id,
        service_id="tracecat-api" if user else "tracecat-executor",
        scopes=frozenset({"*"}),
    )


def _session(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "add": MagicMock(),
        "commit": AsyncMock(),
        "refresh": AsyncMock(),
        "delete": AsyncMock(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def webhook_case() -> SimpleNamespace:
    workspace_id = uuid.uuid4()
    return SimpleNamespace(
        workspace_id=workspace_id,
        workflow_id=WorkflowUUID.new_uuid4(),
        webhook_id=uuid.uuid4(),
        api_key_id=uuid.uuid4(),
        role=_role(workspace_id),
        session=_session(),
    )


@pytest.fixture
def audit_events(monkeypatch: pytest.MonkeyPatch) -> CapturedEvents:
    events: CapturedEvents = []

    async def publish(self: AuditService, event: AuditEvent) -> None:
        events.append((self.session, event))

    monkeypatch.setattr(
        "tracecat.audit.service.config.TRACECAT__AUDIT_DELIVERY_ENABLED", True
    )
    monkeypatch.setattr(
        AuditService, "_has_configured_sink", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(AuditService, "_get_actor_label", AsyncMock(return_value=None))
    monkeypatch.setattr(AuditService, "_publish_event", publish)
    return events


def _assert_no_key_material(events: object) -> None:
    text = repr(events)
    assert all(
        secret not in text
        for secret in (
            _GeneratedKey.raw,
            _GeneratedKey.hashed,
            _GeneratedKey.salt_b64,
            _GeneratedKey().preview(),
        )
    )


@pytest.mark.anyio
@pytest.mark.parametrize(("operation", "params", "expected"), CONFIG_CASES)
async def test_webhook_config_success_payload_and_attribution(
    audit_events: CapturedEvents,
    webhook_case: SimpleNamespace,
    operation: str,
    params: WebhookCreate | WebhookUpdate,
    expected: dict[str, object],
) -> None:
    async def refresh(instance: object) -> None:
        if isinstance(instance, Webhook):
            instance.id = webhook_case.webhook_id

    webhook_case.session.refresh.side_effect = refresh
    with patch.object(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=SimpleNamespace(id=webhook_case.webhook_id)),
    ):
        service = webhook_service.WebhookConfigService(
            cast(Any, webhook_case.session), role=webhook_case.role
        )
        await getattr(service, f"{operation}_webhook")(webhook_case.workflow_id, params)

    assert len(audit_events) == 1
    used_session, event = audit_events[0]
    assert used_session is webhook_case.session
    assert (
        event.organization_id,
        event.workspace_id,
        event.actor_id,
        event.data,
    ) == (
        webhook_case.role.organization_id,
        webhook_case.workspace_id,
        webhook_case.role.user_id,
        expected,
    )
    assert (event.action, event.status) == (operation, AuditEventStatus.SUCCESS)
    assert "10.10.0.0/16" not in repr(audit_events)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["create", "rotate", "revoke", "delete"])
async def test_webhook_key_lifecycle_attempt_terminal_payloads_and_dedup(
    audit_events: CapturedEvents,
    webhook_case: SimpleNamespace,
    operation: str,
) -> None:
    api_key = (
        None
        if operation == "create"
        else SimpleNamespace(
            id=webhook_case.api_key_id, revoked_at=None, revoked_by=None
        )
    )
    webhook = SimpleNamespace(
        id=webhook_case.webhook_id,
        workspace_id=webhook_case.workspace_id,
        api_key=api_key,
    )

    def assign_id(instance: object) -> None:
        if isinstance(instance, WebhookApiKey):
            instance.id = webhook_case.api_key_id

    webhook_case.session.add.side_effect = assign_id
    handler_name = (
        "generate_webhook_api_key"
        if operation in {"create", "rotate"}
        else f"{operation}_webhook_api_key"
    )
    with (
        patch.object(webhook_service, "get_webhook", AsyncMock(return_value=webhook)),
        patch.object(workflow_router, "generate_api_key", return_value=_GeneratedKey()),
    ):
        result = await getattr(workflow_router, handler_name)(
            role=webhook_case.role,
            session=cast(Any, webhook_case.session),
            workflow_id=webhook_case.workflow_id,
        )

    assert [event.status for _, event in audit_events] == [AuditEventStatus.ATTEMPT, AuditEventStatus.SUCCESS]  # fmt: skip
    assert [event.action for _, event in audit_events] == [operation, operation]
    assert [event.data for _, event in audit_events] == [{"key_operation": operation}, {"key_operation": operation, "key_present": True, "api_key_id": str(webhook_case.api_key_id)}]  # fmt: skip
    assert all(
        event.workspace_id == webhook_case.workspace_id for _, event in audit_events
    )
    if operation in {"create", "rotate"}:
        assert result.api_key == _GeneratedKey.raw
    elif operation == "revoke":
        assert api_key is not None
        assert api_key.revoked_by == webhook_case.role.user_id
    else:
        webhook_case.session.delete.assert_awaited_once_with(api_key)
        assert webhook.api_key is None
    _assert_no_key_material([event.data for _, event in audit_events])


@pytest.mark.anyio
async def test_key_commit_failure_emits_failure_once_with_fresh_session(
    audit_events: CapturedEvents,
    webhook_case: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = RuntimeError(
        "tc_wh_secret_raw hashed-secret-material salt-secret-material preview-secret"
    )
    session = _session(commit=AsyncMock(side_effect=error))
    fresh_session = SimpleNamespace()
    session.add.side_effect = lambda key: setattr(key, "id", webhook_case.api_key_id)
    monkeypatch.setattr(
        "tracecat.audit.service.get_async_session_context_manager",
        lambda: _AsyncContext(fresh_session),
    )
    webhook = SimpleNamespace(
        id=webhook_case.webhook_id,
        workspace_id=webhook_case.workspace_id,
        api_key=None,
    )
    with (
        patch.object(webhook_service, "get_webhook", AsyncMock(return_value=webhook)),
        patch.object(workflow_router, "generate_api_key", return_value=_GeneratedKey()),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await workflow_router.generate_webhook_api_key(
            role=webhook_case.role,
            session=cast(Any, session),
            workflow_id=webhook_case.workflow_id,
        )

    assert exc_info.value is error
    assert [(used_session, event.status) for used_session, event in audit_events] == [(session, AuditEventStatus.ATTEMPT), (fresh_session, AuditEventStatus.FAILURE)]  # fmt: skip
    assert audit_events[1][1].data == {"key_operation": "create", "key_present": True, "api_key_id": str(webhook_case.api_key_id)}  # fmt: skip
    _assert_no_key_material([event.data for _, event in audit_events])


@pytest.mark.anyio
async def test_internal_webhook_update_emits_no_user_audit_event(
    audit_events: CapturedEvents,
) -> None:
    workspace_id, workflow_id = uuid.uuid4(), WorkflowUUID.new_uuid4()
    role, session = _role(workspace_id, user=False), _session()
    with patch.object(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    ):
        await internal_router.update_webhook(
            role=role,
            session=cast(Any, session),
            workflow_id=workflow_id,
            params=WebhookUpdate(status="online"),
        )
    assert audit_events == []


@pytest.mark.anyio
async def test_webhook_audit_failure_log_excludes_exception_and_key_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[str, dict[str, object]]] = []

    async def fail(_self: AuditService) -> bool:
        raise _SensitiveDBError(
            "tc_wh_secret_raw hashed-secret-material salt-secret-material preview-secret"
        )

    monkeypatch.setattr(
        "tracecat.audit.service.config.TRACECAT__AUDIT_DELIVERY_ENABLED", True
    )
    monkeypatch.setattr(AuditService, "_has_configured_sink", fail)
    monkeypatch.setattr(
        webhook_service.logger,
        "warning",
        lambda message, **kwargs: warnings.append((message, kwargs)),
    )
    role = _role(uuid.uuid4())
    await webhook_service.emit_webhook_audit_event(
        role,
        cast(Any, _session()),
        workflow_id=WorkflowUUID.new_uuid4(),
        action="create",
        status=AuditEventStatus.FAILURE,
        data={"key_operation": "create"},
    )
    assert warnings == [("Webhook audit log failed", {"error_type": "_SensitiveDBError", "db_error_code": "23505"})]  # fmt: skip
    _assert_no_key_material(warnings)
