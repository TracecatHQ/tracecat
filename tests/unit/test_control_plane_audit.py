from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from temporalio.service import RPCError, RPCStatusCode

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.logger import AuditCallContext
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditEvent
from tracecat.auth.types import Role
from tracecat.contexts import ctx_client_ip, ctx_role, ctx_user_agent
from tracecat.db.models import WebhookApiKey
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import ScopeDeniedError, TracecatValidationError
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import (
    WorkflowUUID,
    exec_id_to_parts,
    generate_exec_id,
)
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookCreate, WebhookUpdate
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerConfig,
    CaseTriggerUpdate,
)
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.graph.service import WorkflowGraphService
from tracecat.workflow.management import draft as workflow_management_draft
from tracecat.workflow.management import router as workflow_management_router
from tracecat.workflow.management.draft import (
    build_workflow_edit_document,
    persist_workflow_edit_document,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import (
    GraphOperation,
    GraphOperationType,
    WorkflowUpdate,
)
from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleUpdate
from tracecat.workflow.schedules.service import WorkflowSchedulesService

type AuditCall = dict[str, object]
type Runner = Callable[
    [pytest.MonkeyPatch, Role], Awaitable[tuple[AuditCall, AuditCall]]
]

_ACTOR_LABEL = "actor@example.test"
_CLIENT_IP = "192.0.2.10"
_USER_AGENT = "TracecatAuditTest/1.0"
_WEBHOOK_URL = "https://audit.example.test/events"


@dataclass(frozen=True)
class AuditCase:
    name: str
    run: Runner

    def __str__(self) -> str:
        return self.name


def _role(*, internal: bool = False) -> Role:
    return Role(
        type="service" if internal else "user",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        service_id="tracecat-executor" if internal else "tracecat-api",
        scopes=frozenset({"*"}),
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        add=MagicMock(),
        commit=AsyncMock(),
        delete=AsyncMock(),
        execute=AsyncMock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        scalar=AsyncMock(),
    )


def _event(
    resource_type: str,
    action: str,
    resource_id: uuid.UUID | None,
    data: Mapping[str, object] | None,
    status: AuditEventStatus,
) -> AuditCall:
    return {
        "resource_type": resource_type,
        "action": action,
        "resource_id": resource_id,
        "data": dict(data) if data is not None else None,
        "status": status,
    }


def _pair(
    resource_type: str,
    action: str,
    attempt_id: uuid.UUID | None,
    terminal_id: uuid.UUID | None,
    data: Mapping[str, object] | None = None,
    *,
    terminal_status: AuditEventStatus = AuditEventStatus.SUCCESS,
) -> tuple[AuditCall, AuditCall]:
    return (
        _event(resource_type, action, attempt_id, data, AuditEventStatus.ATTEMPT),
        _event(resource_type, action, terminal_id, data, terminal_status),
    )


def _capture_events(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[AuditCall],
    wire_events: list[dict[str, object]] | None = None,
) -> None:
    class CaptureAuditService(AuditService):
        async def _get_webhook_url(self) -> str:
            return _WEBHOOK_URL

        async def _get_actor_label(self) -> str:
            return _ACTOR_LABEL

        async def _post_event(self, *, webhook_url: str, payload: AuditEvent) -> None:
            assert webhook_url == _WEBHOOK_URL
            calls.append(
                _event(
                    payload.resource_type,
                    payload.action,
                    payload.resource_id,
                    payload.data,
                    payload.status,
                )
            )
            if wire_events is not None:
                wire_events.append(payload.model_dump(mode="json"))

    @asynccontextmanager
    async def with_session(
        cls: type[AuditService],
        role: Role | None = None,
        *,
        session: object | None = None,
        audit_sink: object | None = None,
    ) -> AsyncGenerator[AuditService, None]:
        del cls
        yield CaptureAuditService(
            cast(Any, session if session is not None else _session()),
            role=role,
            audit_sink=cast(Any, audit_sink),
        )

    monkeypatch.setattr(AuditService, "with_session", classmethod(with_session))


def _expected_wire_event(role: Role, event: AuditCall) -> dict[str, object]:
    resource_id = event["resource_id"]
    assert resource_id is None or isinstance(resource_id, uuid.UUID)
    status = event["status"]
    assert isinstance(status, AuditEventStatus)
    return {
        "organization_id": str(role.organization_id),
        "workspace_id": str(role.workspace_id),
        "actor_type": "USER",
        "actor_id": str(role.actor_id),
        "actor_label": _ACTOR_LABEL,
        "ip_address": _CLIENT_IP,
        "user_agent": _USER_AGENT,
        "resource_type": event["resource_type"],
        "resource_id": str(resource_id) if resource_id is not None else None,
        "action": event["action"],
        "status": status.value,
        "data": event["data"],
    }


def _assert_wire_event(
    role: Role, payload: dict[str, object], expected: AuditCall
) -> None:
    actual = dict(payload)
    created_at = actual.pop("created_at")
    assert isinstance(created_at, str)
    assert (
        datetime.fromisoformat(created_at.replace("Z", "+00:00")).utcoffset()
        is not None
    )
    assert actual == _expected_wire_event(role, expected)


async def _workflow_update(mp: pytest.MonkeyPatch, role: Role):
    workflow_id = WorkflowUUID.new_uuid4()
    session = _session()
    session.execute.return_value = SimpleNamespace(
        scalar_one=MagicMock(return_value=SimpleNamespace(id=workflow_id))
    )
    service = WorkflowsManagementService(cast(Any, session), role=role)
    await service.update_workflow(workflow_id, WorkflowUpdate(status="online"))
    return _pair(
        "workflow", "update", workflow_id, workflow_id, {"changed_fields": ["status"]}
    )


def _edit_source() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Audit workflow",
        description="",
        status="offline",
        alias=None,
        error_handler=None,
        entrypoint=None,
        expects=None,
        config=None,
        returns=None,
        trigger_position_x=None,
        trigger_position_y=None,
        viewport_x=None,
        viewport_y=None,
        viewport_zoom=None,
        actions=[],
        schedules=[],
        case_trigger=None,
    )


async def _workflow_edit(mp: pytest.MonkeyPatch, role: Role):
    source = _edit_source()
    original = build_workflow_edit_document(cast(Any, source))
    updated = original.model_copy(
        update={"metadata": original.metadata.model_copy(update={"status": "online"})}
    )
    changed_sections = (
        workflow_management_draft.workflow_edit_document_changed_sections(
            original,
            updated,
        )
    )
    changed_sections_computation = MagicMock(
        side_effect=AssertionError("changed sections should be reused")
    )
    mp.setattr(
        workflow_management_draft,
        "workflow_edit_document_changed_sections",
        changed_sections_computation,
    )
    await persist_workflow_edit_document(
        role=role,
        service=WorkflowsManagementService(cast(Any, _session()), role=role),
        workflow=cast(Any, source),
        original_document=original,
        updated_document=updated,
        changed_sections=changed_sections,
    )
    changed_sections_computation.assert_not_called()
    return _pair(
        "workflow", "update", source.id, source.id, {"changed_fields": ["metadata"]}
    )


async def _workflow_graph(mp: pytest.MonkeyPatch, role: Role):
    workflow_id = WorkflowUUID.new_uuid4()
    session = _session()
    session.execute.return_value = SimpleNamespace(
        scalar_one_or_none=MagicMock(return_value=SimpleNamespace())
    )
    service = WorkflowGraphService(cast(Any, session), role=role)
    mp.setattr(
        service,
        "apply_operations_to_locked_workflow",
        AsyncMock(return_value=SimpleNamespace()),
    )
    await service.apply_operations(
        workflow_id,
        1,
        [GraphOperation(type=GraphOperationType.UPDATE_NODE, payload={})],
    )
    return _pair(
        "workflow",
        "update",
        workflow_id,
        workflow_id,
        {"changed_fields": ["definition"]},
    )


async def _workflow_publish(mp: pytest.MonkeyPatch, role: Role):
    workflow_id = WorkflowUUID.new_uuid4()
    service = WorkflowsManagementService(cast(Any, _session()), role=role)
    mp.setattr(service, "get_workflow", AsyncMock(return_value=SimpleNamespace()))
    mp.setattr(
        service,
        "build_dsl_from_workflow",
        AsyncMock(side_effect=TracecatValidationError("invalid draft")),
    )
    await service.publish_workflow(workflow_id)
    return _pair(
        "workflow",
        "publish",
        workflow_id,
        workflow_id,
        terminal_status=AuditEventStatus.FAILURE,
    )


async def _workflow_restore(mp: pytest.MonkeyPatch, role: Role):
    del mp
    workflow = SimpleNamespace(id=uuid.uuid4())
    service = WorkflowsManagementService(cast(Any, _session()), role=role)
    with pytest.raises(TracecatValidationError):
        await service.restore_workflow_definition(
            cast(Any, workflow), cast(Any, SimpleNamespace(workflow_id=uuid.uuid4()))
        )
    return _pair(
        "workflow",
        "update",
        workflow.id,
        workflow.id,
        terminal_status=AuditEventStatus.FAILURE,
    )


async def _external_create(mp: pytest.MonkeyPatch, role: Role):
    workflow_id = uuid.uuid4()
    service = WorkflowsManagementService(cast(Any, _session()), role=role)
    dsl = DSLInput.model_validate(
        {
            "title": "Audit workflow",
            "description": "",
            "entrypoint": {"ref": "start"},
            "actions": [
                {
                    "ref": "start",
                    "action": "core.transform.reshape",
                    "args": {"value": "ok"},
                }
            ],
        }
    )
    mp.setattr(
        service,
        "correlate_agent_catalog_ids",
        AsyncMock(return_value=dsl),
    )
    mp.setattr(
        service,
        "create_db_workflow_from_dsl",
        AsyncMock(return_value=SimpleNamespace(id=workflow_id)),
    )
    await service.create_workflow_from_external_definition(
        {"definition": dsl.model_dump()}
    )
    return _pair("workflow", "create", None, workflow_id)


async def _execution_control(mp: pytest.MonkeyPatch, role: Role, operation: str):
    workflow_id = WorkflowUUID.new_uuid4()
    execution_id = generate_exec_id(workflow_id)
    service = WorkflowExecutionsService(MagicMock(), role=role)
    mp.setattr(service, "require_execution", AsyncMock())
    handle = MagicMock()
    setattr(handle, operation, AsyncMock())
    mp.setattr(service, "handle", MagicMock(return_value=handle))
    await getattr(service, f"{operation}_workflow_execution")(execution_id)
    data = {"execution_id": execution_id, "operation": operation}
    return _pair("workflow_execution", "cancel", workflow_id, workflow_id, data)


async def _execution_cancel(mp: pytest.MonkeyPatch, role: Role):
    return await _execution_control(mp, role, "cancel")


async def _execution_terminate(mp: pytest.MonkeyPatch, role: Role):
    return await _execution_control(mp, role, "terminate")


async def _execution_reset(mp: pytest.MonkeyPatch, role: Role):
    workflow_id = WorkflowUUID.new_uuid4()
    execution_id = generate_exec_id(workflow_id)
    client = MagicMock()
    client.workflow_service.reset_workflow_execution = AsyncMock(
        return_value=SimpleNamespace(run_id="new-run")
    )
    service = WorkflowExecutionsService(client, role=role)
    mp.setattr(
        service,
        "require_execution",
        AsyncMock(return_value=SimpleNamespace(id=execution_id, run_id="run")),
    )
    mp.setattr(service, "_resolve_reset_event_id", AsyncMock(return_value=1))
    await service.reset_workflow_execution(execution_id, event_id=None)
    data = {"execution_id": execution_id, "operation": "reset"}
    return _pair("workflow_execution", "create", workflow_id, workflow_id, data)


async def _schedule(mp: pytest.MonkeyPatch, role: Role, action: str):
    schedule_id = ScheduleUUID.new_uuid4()
    service = WorkflowSchedulesService(cast(Any, _session()), role=role)
    if action == "create":
        mp.setattr(
            service,
            "_create_schedule_impl",
            AsyncMock(return_value=SimpleNamespace(id=schedule_id)),
        )
        await service.create_schedule(
            ScheduleCreate(workflow_id=WorkflowUUID.new_uuid4(), cron="0 * * * *")
        )
        return _pair("schedule", action, None, schedule_id)
    if action == "update":
        mp.setattr(
            service,
            "_get_schedule_with_workflow_lock",
            AsyncMock(return_value=SimpleNamespace(id=schedule_id)),
        )
        mp.setattr(
            "tracecat.workflow.schedules.service.add_after_commit_callback",
            MagicMock(),
        )
        await service.update_schedule(schedule_id, ScheduleUpdate(status="offline"))
        return _pair(
            "schedule", action, schedule_id, schedule_id, {"changed_fields": ["status"]}
        )
    mp.setattr(service, "_delete_schedule_impl", AsyncMock())
    await service.delete_schedule(schedule_id)
    return _pair("schedule", action, schedule_id, schedule_id)


async def _schedule_create(mp: pytest.MonkeyPatch, role: Role):
    return await _schedule(mp, role, "create")


async def _schedule_update(mp: pytest.MonkeyPatch, role: Role):
    return await _schedule(mp, role, "update")


async def _schedule_delete(mp: pytest.MonkeyPatch, role: Role):
    return await _schedule(mp, role, "delete")


async def _case_upsert(mp: pytest.MonkeyPatch, role: Role, exists: bool):
    trigger_id = uuid.uuid4()
    session = _session()
    session.scalar.return_value = SimpleNamespace(id=trigger_id) if exists else None
    service = CaseTriggersService(cast(Any, session), role=role)
    mp.setattr(service, "require_entitlement", AsyncMock())
    mp.setattr(
        service,
        "_upsert_case_trigger",
        AsyncMock(return_value=SimpleNamespace(id=trigger_id)),
    )
    await service.upsert_case_trigger(
        WorkflowUUID.new_uuid4(), CaseTriggerConfig(status="offline")
    )
    action = "update" if exists else "create"
    data = (
        {"changed_fields": sorted(CaseTriggerConfig.model_fields)} if exists else None
    )
    return _pair(
        "case_trigger", action, trigger_id if exists else None, trigger_id, data
    )


async def _case_create(mp: pytest.MonkeyPatch, role: Role):
    return await _case_upsert(mp, role, False)


async def _case_upsert_update(mp: pytest.MonkeyPatch, role: Role):
    return await _case_upsert(mp, role, True)


async def _case_update(mp: pytest.MonkeyPatch, role: Role):
    trigger_id = uuid.uuid4()
    service = CaseTriggersService(cast(Any, _session()), role=role)
    mp.setattr(service, "require_entitlement", AsyncMock())
    mp.setattr(
        service,
        "_update_case_trigger",
        AsyncMock(return_value=SimpleNamespace(id=trigger_id)),
    )
    await service.update_case_trigger(
        WorkflowUUID.new_uuid4(), CaseTriggerUpdate(status="online")
    )
    return _pair(
        "case_trigger", "update", None, trigger_id, {"changed_fields": ["status"]}
    )


def _webhook_session() -> SimpleNamespace:
    session = _session()
    session.add.side_effect = lambda obj: setattr(obj, "id", obj.id or uuid.uuid4())
    return session


async def _webhook_create(mp: pytest.MonkeyPatch, role: Role):
    session = _webhook_session()

    async def get_created_webhook(**_kwargs: object):
        return session.add.call_args.args[0]

    mp.setattr(webhook_service, "get_webhook", get_created_webhook)
    await workflow_management_router.create_webhook(
        role=role,
        session=cast(Any, session),
        workflow_id=WorkflowUUID.new_uuid4(),
        params=WebhookCreate(status="online"),
    )
    webhook = session.add.call_args.args[0]
    return _pair("webhook", "create", None, webhook.id)


async def _webhook_update(mp: pytest.MonkeyPatch, role: Role):
    webhook_id = uuid.uuid4()
    session = _session()
    mp.setattr(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=SimpleNamespace(id=webhook_id, status="offline")),
    )
    await webhook_service.update_webhook(
        role=role,
        session=cast(Any, session),
        workflow_id=WorkflowUUID.new_uuid4(),
        params=WebhookUpdate(status="online"),
    )
    return _pair(
        "webhook",
        "update",
        webhook_id,
        webhook_id,
        {"changed_fields": ["status"]},
    )


class _GeneratedKey:
    raw, hashed, salt_b64 = "tc_wh_synthetic", "hash", "salt"

    def preview(self) -> str:
        return "preview"


async def _key(mp: pytest.MonkeyPatch, role: Role, action: str):
    webhook_id, key_id = uuid.uuid4(), uuid.uuid4()
    existing = (
        None
        if action == "create"
        else SimpleNamespace(
            id=key_id,
            workspace_id=role.workspace_id,
            hashed="old",
            salt="old",
            preview="old",
            last_used_at=None,
            revoked_at=None,
            revoked_by=None,
            created_at=None,
            updated_at=None,
        )
    )
    session = _webhook_session()

    async def get_api_key_audit_target(
        _context: object,
    ) -> tuple[uuid.UUID, uuid.UUID | None]:
        if action == "create" and session.add.called:
            return webhook_id, cast(WebhookApiKey, session.add.call_args.args[0]).id
        return webhook_id, None if action == "create" else key_id

    mp.setattr(
        workflow_management_router,
        "_get_webhook_key_audit_target",
        get_api_key_audit_target,
    )
    mp.setattr(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=SimpleNamespace(id=webhook_id, api_key=existing)),
    )
    data = {"webhook_id": str(webhook_id)}
    if action in {"create", "rotate"}:
        mp.setattr(
            workflow_management_router,
            "generate_api_key",
            MagicMock(return_value=_GeneratedKey()),
        )
        await workflow_management_router.generate_webhook_api_key(
            role=role,
            session=cast(Any, session),
            workflow_id=WorkflowUUID.new_uuid4(),
        )
        terminal_id = (
            cast(WebhookApiKey, session.add.call_args.args[0]).id
            if action == "create"
            else key_id
        )
        return _pair(
            "webhook_api_key",
            action,
            None if action == "create" else key_id,
            terminal_id,
            data,
        )
    if action == "revoke":
        await workflow_management_router.revoke_webhook_api_key(
            role=role,
            session=cast(Any, session),
            workflow_id=WorkflowUUID.new_uuid4(),
        )
    else:
        await workflow_management_router.delete_webhook_api_key(
            role=role,
            session=cast(Any, session),
            workflow_id=WorkflowUUID.new_uuid4(),
        )
    return _pair("webhook_api_key", action, key_id, key_id, data)


async def _key_create(mp: pytest.MonkeyPatch, role: Role):
    return await _key(mp, role, "create")


async def _key_rotate(mp: pytest.MonkeyPatch, role: Role):
    return await _key(mp, role, "rotate")


async def _key_revoke(mp: pytest.MonkeyPatch, role: Role):
    return await _key(mp, role, "revoke")


async def _key_delete(mp: pytest.MonkeyPatch, role: Role):
    return await _key(mp, role, "delete")


CASES = tuple(
    AuditCase(name, runner)
    for name, runner in (
        ("workflow_update", _workflow_update),
        ("workflow_edit", _workflow_edit),
        ("workflow_graph", _workflow_graph),
        ("workflow_publish", _workflow_publish),
        ("workflow_restore", _workflow_restore),
        ("external_create", _external_create),
        ("execution_reset", _execution_reset),
        ("execution_cancel", _execution_cancel),
        ("execution_terminate", _execution_terminate),
        ("schedule_create", _schedule_create),
        ("schedule_update", _schedule_update),
        ("schedule_delete", _schedule_delete),
        ("case_create", _case_create),
        ("case_upsert_update", _case_upsert_update),
        ("case_update", _case_update),
        ("webhook_create", _webhook_create),
        ("webhook_update", _webhook_update),
        ("key_create", _key_create),
        ("key_rotate", _key_rotate),
        ("key_revoke", _key_revoke),
        ("key_delete", _key_delete),
    )
)


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=str)
async def test_control_plane_audit_wiring(
    monkeypatch: pytest.MonkeyPatch, case: AuditCase
) -> None:
    role, calls, wire_events = _role(), [], []
    _capture_events(monkeypatch, calls, wire_events)
    role_token = ctx_role.set(role)
    client_ip_token = ctx_client_ip.set(_CLIENT_IP)
    user_agent_token = ctx_user_agent.set(_USER_AGENT)
    try:
        expected = await case.run(monkeypatch, role)
    finally:
        ctx_user_agent.reset(user_agent_token)
        ctx_client_ip.reset(client_ip_token)
        ctx_role.reset(role_token)
    expected_events = list(expected)
    assert calls == expected_events
    assert len(wire_events) == len(expected_events)
    for payload, expected_event in zip(wire_events, expected_events, strict=True):
        _assert_wire_event(role, payload, expected_event)


@pytest.mark.anyio
async def test_completed_execution_control_emits_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role, calls = _role(), []
    _capture_events(monkeypatch, calls)
    service = WorkflowExecutionsService(MagicMock(), role=role)
    monkeypatch.setattr(service, "require_execution", AsyncMock())
    handle = MagicMock()
    handle.cancel = AsyncMock(
        side_effect=RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
    )
    monkeypatch.setattr(service, "handle", MagicMock(return_value=handle))
    execution_id = generate_exec_id(WorkflowUUID.new_uuid4())
    await service.cancel_workflow_execution(execution_id)
    assert [call["status"] for call in calls] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.SUCCESS,
    ]


@pytest.mark.anyio
async def test_bulk_reset_emits_one_event_pair_per_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role, calls = _role(), []
    _capture_events(monkeypatch, calls)
    client = MagicMock()
    client.workflow_service.reset_workflow_execution = AsyncMock(
        return_value=SimpleNamespace(run_id="new-run")
    )
    service = WorkflowExecutionsService(client, role=role)
    monkeypatch.setattr(
        service,
        "require_execution",
        AsyncMock(return_value=SimpleNamespace(id="temporal-id", run_id="run")),
    )
    monkeypatch.setattr(service, "_resolve_reset_event_id", AsyncMock(return_value=1))
    execution_ids = [
        generate_exec_id(WorkflowUUID.new_uuid4()),
        generate_exec_id(WorkflowUUID.new_uuid4()),
    ]

    results = await service.bulk_reset_workflow_executions(
        execution_ids,
        event_id=None,
    )

    assert all(result.ok for result in results)
    for execution_id in execution_ids:
        workflow_id, _ = exec_id_to_parts(execution_id)
        matching = [
            call
            for call in calls
            if call["data"] == {"execution_id": execution_id, "operation": "reset"}
        ]
        assert matching == list(
            _pair(
                "workflow_execution",
                "create",
                workflow_id,
                workflow_id,
                {"execution_id": execution_id, "operation": "reset"},
            )
        )


@pytest.mark.anyio
async def test_missing_webhook_key_operation_emits_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role, calls = _role(), []
    _capture_events(monkeypatch, calls)
    session = _session()
    monkeypatch.setattr(
        workflow_management_router,
        "_get_webhook_key_audit_target",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=None),
    )
    with pytest.raises(HTTPException):
        await workflow_management_router.generate_webhook_api_key(
            role=role,
            session=cast(Any, session),
            workflow_id=WorkflowUUID.new_uuid4(),
        )
    assert [call["status"] for call in calls] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.FAILURE,
    ]
    assert all(call["resource_type"] == "webhook_api_key" for call in calls)
    assert all(call["action"] == "create" for call in calls)
    assert all(call["data"] == {"webhook_id": None} for call in calls)


@pytest.mark.anyio
async def test_webhook_update_requires_workflow_update_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = _role().model_copy(update={"scopes": frozenset({"workflow:read"})})
    get_webhook = AsyncMock()
    monkeypatch.setattr(webhook_service, "get_webhook", get_webhook)

    with pytest.raises(ScopeDeniedError):
        await webhook_service.update_webhook(
            role=role,
            session=cast(Any, _session()),
            workflow_id=WorkflowUUID.new_uuid4(),
            params=WebhookUpdate(status="online"),
        )

    get_webhook.assert_not_awaited()


@pytest.mark.anyio
async def test_failed_webhook_update_audit_preserves_resource_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role, calls = _role(), []
    _capture_events(monkeypatch, calls)
    webhook_id = uuid.uuid4()
    webhook = SimpleNamespace(id=webhook_id, status="offline")
    session = _session()
    session.commit.side_effect = RuntimeError("synthetic commit failure")
    monkeypatch.setattr(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=webhook),
    )

    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await webhook_service.update_webhook(
            role=role,
            session=cast(Any, session),
            workflow_id=WorkflowUUID.new_uuid4(),
            params=WebhookUpdate(status="online"),
        )

    assert [call["status"] for call in calls] == [
        AuditEventStatus.ATTEMPT,
        AuditEventStatus.FAILURE,
    ]
    assert all(call["resource_id"] == webhook_id for call in calls)


@pytest.mark.anyio
async def test_webhook_key_audit_target_uses_selected_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = _role()
    workflow_id = WorkflowUUID.new_uuid4()
    webhook_id = uuid.uuid4()
    api_key_id = uuid.uuid4()
    session = _session()
    session.scalar.return_value = api_key_id
    monkeypatch.setattr(
        webhook_service,
        "get_webhook",
        AsyncMock(return_value=SimpleNamespace(id=webhook_id)),
    )
    context = AuditCallContext(
        target=None,
        arguments={
            "role": role,
            "session": session,
            "workflow_id": workflow_id,
        },
    )

    target = await workflow_management_router._get_webhook_key_audit_target(context)

    assert target == (webhook_id, api_key_id)
    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_internal_publish_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    role, calls = _role(internal=True), []
    _capture_events(monkeypatch, calls)
    service = WorkflowsManagementService(cast(Any, _session()), role=role)
    monkeypatch.setattr(
        service, "get_workflow", AsyncMock(side_effect=TracecatValidationError("stop"))
    )
    with pytest.raises(TracecatValidationError):
        await service.publish_workflow(WorkflowUUID.new_uuid4())
    assert calls == []
