"""Schedules as ``schedule`` triggers: both entry points write the same rows."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import EDITOR_SCOPES
from tracecat.db.models import (
    AgentPreset,
    Schedule,
    Trigger,
    TriggerBinding,
    Workflow,
    Workspace,
)
from tracecat.exceptions import EntitlementRequired, ScopeDeniedError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams
from tracecat.tiers.enums import Entitlement
from tracecat.triggers.enums import (
    EventSourceKind,
    TriggerErrorCode,
    TriggerKind,
    TriggerStatus,
    TriggerTargetType,
)
from tracecat.triggers.exceptions import TriggerError
from tracecat.triggers.schemas import (
    AgentBindingTarget,
    AwsSnsSourceConfig,
    EventSourceCreate,
    ObjectStorageTriggerConfig,
    ScheduleTriggerConfig,
    ScheduleTriggerConfigRead,
    TriggerBindingCreate,
    TriggerBindingUpdate,
    TriggerCreate,
    TriggerUpdate,
    WorkflowBindingTarget,
)
from tracecat.triggers.service import TriggersService
from tracecat.triggers.types import ENTITLEMENT_BY_TRIGGER_KIND
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleUpdate
from tracecat.workflow.schedules.service import (
    WorkflowSchedulesService,
    default_trigger_name,
)

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


class _Temporal:
    """Records the bridge calls the services make after commit."""

    def __init__(self) -> None:
        self.created: list[uuid.UUID] = []
        self.updated: list[tuple[uuid.UUID, ScheduleUpdate]] = []
        self.deleted: list[uuid.UUID] = []

    async def create_schedule(self, *, schedule_id: uuid.UUID, **_: Any) -> Any:
        self.created.append(schedule_id)
        return SimpleNamespace(id=str(schedule_id))

    async def update_schedule(self, schedule_id: Any, params: ScheduleUpdate) -> None:
        self.updated.append((uuid.UUID(str(schedule_id)), params))

    async def delete_schedule(self, schedule_id: Any) -> None:
        self.deleted.append(uuid.UUID(str(schedule_id)))


@pytest.fixture
def temporal(monkeypatch: pytest.MonkeyPatch) -> _Temporal:
    fake = _Temporal()
    monkeypatch.setattr(bridge, "create_schedule", fake.create_schedule)
    monkeypatch.setattr(bridge, "update_schedule", fake.update_schedule)
    monkeypatch.setattr(bridge, "delete_schedule", fake.delete_schedule)
    return fake


async def _add_workflow(
    session: AsyncSession, workspace_id: uuid.UUID, title: str, version: int | None
) -> SimpleNamespace:
    """A workflow row, returned as plain values.

    A service rolls the session back on a refused request, which expires
    loaded rows; reading them afterwards would need IO outside the loop.
    """
    workflow = Workflow(
        workspace_id=workspace_id, title=title, description="", version=version
    )
    session.add(workflow)
    await session.commit()
    return SimpleNamespace(id=workflow.id, workspace_id=workspace_id, title=title)


@pytest.fixture
async def workflow(session: AsyncSession, svc_workspace: Workspace) -> SimpleNamespace:
    return await _add_workflow(session, svc_workspace.id, "Nightly report", 1)


@pytest.fixture
def triggers(session: AsyncSession, svc_role: Role) -> TriggersService:
    return TriggersService(session, svc_role)


@pytest.fixture
def schedules(session: AsyncSession, svc_role: Role) -> WorkflowSchedulesService:
    return WorkflowSchedulesService(session, role=svc_role)


def _config(workflow: SimpleNamespace, **settings: Any) -> ScheduleTriggerConfig:
    defaults: dict[str, Any] = {"every": timedelta(hours=1)}
    return ScheduleTriggerConfig(
        kind=TriggerKind.SCHEDULE,
        workflow_id=WorkflowUUID.new(workflow.id),
        **(defaults | settings),
    )


async def _rows(
    session: AsyncSession, trigger_id: uuid.UUID
) -> tuple[Trigger, list[TriggerBinding], Schedule]:
    fresh = {"populate_existing": True}
    trigger = await session.scalar(
        select(Trigger).where(Trigger.id == trigger_id).execution_options(**fresh)
    )
    assert trigger is not None
    bindings = list(
        await session.scalars(
            select(TriggerBinding)
            .where(TriggerBinding.trigger_id == trigger_id)
            .execution_options(**fresh)
        )
    )
    schedule = await session.scalar(
        select(Schedule)
        .where(Schedule.trigger_id == trigger_id)
        .execution_options(**fresh)
    )
    assert schedule is not None
    return trigger, bindings, schedule


async def _trigger_count(session: AsyncSession, trigger_id: uuid.UUID) -> int:
    result = await session.execute(
        text("SELECT count(*) FROM trigger_binding WHERE trigger_id = :id"),
        {"id": trigger_id},
    )
    bindings = result.scalar_one()
    result = await session.execute(
        text('SELECT count(*) FROM "trigger" WHERE id = :id'), {"id": trigger_id}
    )
    return result.scalar_one() + bindings


# ----- Both entry points write the same rows -----


async def test_both_entry_points_write_the_same_rows(
    session: AsyncSession,
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    from_schedules = await schedules.create_schedule(
        ScheduleCreate(
            workflow_id=WorkflowUUID.new(workflow.id),
            cron="0 9 * * *",
            status="offline",
        )
    )
    from_triggers = await triggers.create_trigger(
        TriggerCreate(
            name="Nightly report schedule",
            status=TriggerStatus.OFFLINE,
            config=_config(workflow, every=None, cron="0 9 * * *"),
        )
    )

    assert from_schedules.trigger_id is not None
    shapes = []
    for trigger_id in (from_schedules.trigger_id, from_triggers.id):
        trigger, bindings, schedule = await _rows(session, trigger_id)
        [binding] = bindings
        shapes.append(
            (
                trigger.kind,
                trigger.name,
                trigger.status,
                binding.target_type,
                binding.workflow_id,
                binding.status,
                schedule.workflow_id,
                schedule.cron,
                schedule.every,
                schedule.status,
            )
        )
    assert shapes[0] == shapes[1]
    assert shapes[0] == (
        "schedule",
        "Nightly report schedule",
        "offline",
        "workflow",
        workflow.id,
        "online",
        workflow.id,
        "0 9 * * *",
        None,
        "offline",
    )
    # Each entry point started its Temporal schedule after commit.
    _, _, made_in_tab = await _rows(session, from_triggers.id)
    assert temporal.created == [from_schedules.id, made_in_tab.id]


async def test_the_trigger_reads_back_its_schedule(
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    schedule = await schedules.create_schedule(
        ScheduleCreate(
            workflow_id=WorkflowUUID.new(workflow.id),
            every=timedelta(minutes=30),
            offset=timedelta(minutes=5),
            inputs={"region": "eu"},
            timeout=60,
        )
    )
    assert schedule.trigger_id is not None

    read = await triggers.get_trigger(schedule.trigger_id)

    assert read.kind is TriggerKind.SCHEDULE
    assert read.status is TriggerStatus.ONLINE
    assert read.config == ScheduleTriggerConfigRead.model_construct(
        kind=TriggerKind.SCHEDULE,
        workflow_id=WorkflowUUID.new(workflow.id),
        schedule_id=schedule.id,
        cron=None,
        every=timedelta(minutes=30),
        offset=timedelta(minutes=5),
        start_at=None,
        end_at=None,
        timeout=60,
        inputs={"region": "eu"},
    )
    [binding] = read.bindings
    assert binding.target_type is TriggerTargetType.WORKFLOW
    assert binding.target_id == workflow.id
    assert binding.target_name == "Nightly report"


def test_default_name_comes_from_the_workflow_title() -> None:
    assert default_trigger_name("Nightly report") == "Nightly report schedule"
    assert default_trigger_name("  ") == "Schedule"
    assert default_trigger_name(None) == "Schedule"
    assert len(default_trigger_name("x" * 400)) == 255


# ----- Status and settings stay together -----


async def test_status_moves_together_from_either_side(
    session: AsyncSession,
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    schedule = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    assert schedule.trigger_id is not None
    trigger_id = schedule.trigger_id

    await schedules.update_schedule(schedule.id, ScheduleUpdate(status="offline"))
    trigger, _, row = await _rows(session, trigger_id)
    assert (trigger.status, row.status) == ("offline", "offline")

    await triggers.update_trigger(
        trigger_id, TriggerUpdate(status=TriggerStatus.ONLINE)
    )
    trigger, _, row = await _rows(session, trigger_id)
    assert (trigger.status, row.status) == ("online", "online")
    # The Temporal schedule is paused and resumed through the same bridge call.
    assert [params.status for _, params in temporal.updated] == ["offline", "online"]
    assert {schedule_id for schedule_id, _ in temporal.updated} == {schedule.id}


async def test_a_status_written_straight_to_schedule_reaches_the_trigger(
    session: AsyncSession,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """The previous release writes only ``schedule``; the database trigger
    keeps ``trigger.status`` in step during a rollout."""
    schedule = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    assert schedule.trigger_id is not None

    await session.execute(
        text("UPDATE schedule SET status = 'offline' WHERE id = :id"),
        {"id": schedule.id},
    )

    trigger, _, _ = await _rows(session, schedule.trigger_id)
    assert trigger.status == "offline"


async def test_a_config_replaces_every_schedule_setting(
    session: AsyncSession,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(
            name="Hourly",
            config=_config(workflow, every=None, cron="0 * * * *", inputs={"a": 1}),
        )
    )
    created_id = created.id

    await triggers.update_trigger(
        created_id,
        TriggerUpdate(
            name="Every 2h", config=_config(workflow, every=timedelta(hours=2))
        ),
    )

    trigger, _, schedule = await _rows(session, created_id)
    assert trigger.name == "Every 2h"
    assert (schedule.cron, schedule.every, schedule.inputs) == (
        None,
        timedelta(hours=2),
        None,
    )
    [(_, params)] = temporal.updated
    assert params.cron is None
    assert params.every == timedelta(hours=2)


async def test_a_config_update_leaves_unchanged_inputs_out(
    schedules: WorkflowSchedulesService,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """Unchanged inputs (None and {} alike) aren't sent to the Temporal
    schedule; the spec always goes whole."""
    made_with_inputs = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, inputs={"a": 1}))
    )
    made_without = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    assert made_without.inputs == {}
    without_id = made_without.trigger_id
    assert without_id is not None

    await triggers.update_trigger(
        made_with_inputs.id,
        TriggerUpdate(
            config=_config(workflow, every=timedelta(hours=2), inputs={"a": 1})
        ),
    )
    await triggers.update_trigger(
        without_id,
        TriggerUpdate(config=_config(workflow, every=None, cron="5 * * * *")),
    )

    for _, params in temporal.updated:
        assert "inputs" not in params.model_fields_set
        assert {"cron", "every", "offset"} <= params.model_fields_set
    assert [params.cron for _, params in temporal.updated] == [None, "5 * * * *"]


async def test_a_config_keeping_the_cron_still_clears_a_stale_interval(
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """/schedules can leave cron and interval both stored. Replacing the config
    with the same cron must send the cron too, not only every=None, or the
    Temporal schedule would be left with no run times."""
    schedule = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    trigger_id = schedule.trigger_id
    assert trigger_id is not None
    await schedules.update_schedule(
        schedule.id, ScheduleUpdate(every=timedelta(hours=1))
    )
    temporal.updated.clear()

    await triggers.update_trigger(
        trigger_id,
        TriggerUpdate(config=_config(workflow, every=None, cron="0 * * * *")),
    )

    [(_, params)] = temporal.updated
    assert (params.cron, params.every) == ("0 * * * *", None)


async def test_a_row_with_cron_and_interval_still_reads(
    session: AsyncSession,
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """/schedules can leave both set; one such row must not fail the list."""
    schedule = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    trigger_id = schedule.trigger_id
    assert trigger_id is not None
    await schedules.update_schedule(
        schedule.id, ScheduleUpdate(every=timedelta(hours=1))
    )

    page = await triggers.list_triggers(PageParams(limit=10))

    [item] = page.items
    assert isinstance(item.config, ScheduleTriggerConfigRead)
    assert (item.config.cron, item.config.every) == ("0 * * * *", timedelta(hours=1))


async def test_an_update_without_status_leaves_the_trigger_status_alone(
    session: AsyncSession,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """A partial update must not copy a status it read before a concurrent
    status change committed."""
    schedule = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    trigger_id = schedule.trigger_id
    assert trigger_id is not None
    # Stands in for the other request's committed status change.
    await session.execute(
        text("UPDATE \"trigger\" SET status = 'offline' WHERE id = :id"),
        {"id": trigger_id},
    )

    await schedules.update_schedule(schedule.id, ScheduleUpdate(timeout=5))

    trigger, _, _ = await _rows(session, trigger_id)
    assert trigger.status == "offline"


async def test_a_schedule_cannot_move_to_another_workflow(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other", 1)
    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    created_id = created.id

    with pytest.raises(TriggerError) as exc:
        await triggers.update_trigger(created_id, TriggerUpdate(config=_config(other)))

    assert exc.value.code is TriggerErrorCode.INVALID
    _, _, schedule = await _rows(session, created_id)
    assert schedule.workflow_id == workflow.id


async def test_updating_a_schedule_without_a_trigger_adopts_it(
    session: AsyncSession,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """A schedule the previous release made during a rollout has no trigger."""
    legacy = Schedule(
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        every=timedelta(hours=1),
        status="online",
    )
    session.add(legacy)
    await session.commit()

    updated = await schedules.update_schedule(
        legacy.id, ScheduleUpdate(status="offline")
    )

    assert updated.trigger_id is not None
    trigger, [binding], _ = await _rows(session, updated.trigger_id)
    assert (trigger.kind, trigger.name, trigger.status) == (
        "schedule",
        "Nightly report schedule",
        "offline",
    )
    assert binding.workflow_id == workflow.id


# ----- Delete -----


async def test_deleting_from_either_side_removes_both(
    session: AsyncSession,
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    via_schedules = await schedules.create_schedule(
        ScheduleCreate(workflow_id=WorkflowUUID.new(workflow.id), cron="0 * * * *")
    )
    via_triggers = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    _, _, tab_schedule = await _rows(session, via_triggers.id)
    assert via_schedules.trigger_id is not None

    await schedules.delete_schedule(via_schedules.id)
    await triggers.delete_trigger(via_triggers.id)

    assert await _trigger_count(session, via_schedules.trigger_id) == 0
    assert await _trigger_count(session, via_triggers.id) == 0
    assert (
        await session.scalar(
            select(Schedule).where(Schedule.workflow_id == workflow.id)
        )
        is None
    )
    assert temporal.deleted == [via_schedules.id, tab_schedule.id]


async def test_deleting_the_workflow_deletes_its_schedule_triggers(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    created_id = created.id

    await WorkflowsManagementService(session, role=svc_role).delete_workflow(
        WorkflowUUID.new(workflow.id)
    )

    assert await _trigger_count(session, created_id) == 0
    page = await triggers.list_triggers(PageParams(limit=10))
    assert page.items == []


# ----- Scopes -----


async def test_the_tab_needs_the_schedule_scopes_too(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    created_id = created.id
    editor = TriggersService(
        session,
        svc_role.model_copy(
            update={"scopes": EDITOR_SCOPES | {"trigger:delete", "trigger:create"}}
        ),
    )
    assert "schedule:delete" not in EDITOR_SCOPES

    with pytest.raises(ScopeDeniedError):
        await editor.delete_trigger(created_id)

    no_create = TriggersService(
        session,
        svc_role.model_copy(
            update={"scopes": (svc_role.scopes or frozenset()) - {"schedule:create"}}
        ),
    )
    with pytest.raises(ScopeDeniedError):
        await no_create.create_trigger(
            TriggerCreate(name="s", config=_config(workflow))
        )

    await _rows(session, created_id)


async def test_an_unsaved_workflow_cannot_get_a_schedule(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    temporal: _Temporal,
) -> None:
    workspace_id = svc_workspace.id
    draft = await _add_workflow(session, workspace_id, "Draft", None)

    with pytest.raises(TriggerError) as exc:
        await triggers.create_trigger(TriggerCreate(name="s", config=_config(draft)))

    assert exc.value.code is TriggerErrorCode.CONFLICT
    assert (
        await session.scalar(
            select(Trigger.id).where(Trigger.workspace_id == workspace_id)
        )
        is None
    )


async def test_schedule_triggers_need_schedule_read(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """A schedule's settings, inputs included, are as private as on /schedules."""
    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow, inputs={"token": "x"}))
    )
    created_id = created.id
    # Every schedule scope: schedule:update also grants schedule:read.
    scopes = frozenset(
        scope
        for scope in svc_role.scopes or frozenset()
        if not scope.startswith("schedule:")
    )
    no_read = TriggersService(session, svc_role.model_copy(update={"scopes": scopes}))

    page = await no_read.list_triggers(PageParams(limit=10))
    assert page.items == []
    with pytest.raises(ScopeDeniedError):
        await no_read.get_trigger(created_id)
    assert (await triggers.get_trigger(created_id)).kind is TriggerKind.SCHEDULE


async def test_the_tab_writes_schedule_audit_records(
    monkeypatch: pytest.MonkeyPatch,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """An auditor filtering on schedules sees changes made in the tab."""
    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)

    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    created_id = created.id
    await triggers.update_trigger(
        created_id, TriggerUpdate(status=TriggerStatus.ONLINE)
    )
    await triggers.delete_trigger(created_id)

    schedule_events = [
        (call.kwargs["action"], call.kwargs.get("data"))
        for call in create_event.await_args_list
        if call.kwargs["resource_type"] == "schedule"
        and call.kwargs["status"] is AuditEventStatus.SUCCESS
    ]
    assert [action for action, _ in schedule_events] == [
        "create",
        "update",
        "delete",
    ]


# ----- Bindings are fixed until schedules read them -----


async def test_a_schedule_trigger_keeps_its_one_workflow_target(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other", 1)
    preset = AgentPreset(
        workspace_id=svc_workspace.id,
        name="Triage",
        slug=f"triage-{uuid.uuid4().hex[:8]}",
        model_name="m",
        model_provider="p",
    )
    session.add(preset)
    await session.commit()
    preset_id = preset.id
    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    created_id = created.id
    [binding] = (await triggers.get_trigger(created_id)).bindings

    refused = [
        triggers.create_binding(
            created_id,
            TriggerBindingCreate(
                target=WorkflowBindingTarget(
                    type=TriggerTargetType.WORKFLOW,
                    workflow_id=WorkflowUUID.new(other.id),
                )
            ),
        ),
        triggers.create_binding(
            created_id,
            TriggerBindingCreate(
                target=AgentBindingTarget(
                    type=TriggerTargetType.AGENT,
                    agent_preset_id=preset_id,
                    instruction="Look",
                )
            ),
        ),
        triggers.update_binding(
            created_id,
            binding.id,
            TriggerBindingUpdate(status=TriggerStatus.OFFLINE),
        ),
        triggers.delete_binding(created_id, binding.id),
    ]
    for call in refused:
        with pytest.raises(TriggerError, match="runs its schedule's workflow only"):
            await call

    _, bindings, _ = await _rows(session, created_id)
    assert [(b.id, b.status) for b in bindings] == [(binding.id, "online")]


# ----- Entitlement per kind -----


def test_every_kind_names_its_entitlement() -> None:
    assert ENTITLEMENT_BY_TRIGGER_KIND == {
        TriggerKind.OBJECT_STORAGE: Entitlement.OBJECT_TRIGGERS,
        TriggerKind.SCHEDULE: None,
    }


async def test_schedules_need_no_entitlement_and_object_storage_does(
    monkeypatch: pytest.MonkeyPatch,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    async def _not_entitled(entitlement: Entitlement) -> bool:
        return False

    monkeypatch.setattr(triggers, "has_entitlement", _not_entitled)

    created = await triggers.create_trigger(
        TriggerCreate(name="s", config=_config(workflow))
    )
    created_id = created.id
    await triggers.update_trigger(created_id, TriggerUpdate(name="renamed"))
    page = await triggers.list_triggers(PageParams(limit=10))
    assert [item.name for item in page.items] == ["renamed"]

    with pytest.raises(EntitlementRequired):
        await triggers.create_trigger(
            TriggerCreate(
                name="uploads",
                config=ObjectStorageTriggerConfig(
                    kind=TriggerKind.OBJECT_STORAGE, source_id=uuid.uuid4()
                ),
            )
        )


async def test_reads_hide_object_storage_triggers_without_the_plan(
    monkeypatch: pytest.MonkeyPatch,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """A plan downgrade keeps object storage triggers, and their agent
    instructions, out of reads."""
    entitled = True

    async def _entitlement(entitlement: Entitlement) -> bool:
        return entitled

    monkeypatch.setattr(triggers, "has_entitlement", _entitlement)
    source = await triggers.create_event_source(
        EventSourceCreate(
            name="bucket",
            config=AwsSnsSourceConfig(
                kind=EventSourceKind.AWS_SNS,
                topic_arn=f"arn:aws:sns:us-east-1:123456789012:t{uuid.uuid4().hex}",
            ),
        )
    )
    uploads = await triggers.create_trigger(
        TriggerCreate(
            name="uploads",
            config=ObjectStorageTriggerConfig(
                kind=TriggerKind.OBJECT_STORAGE, source_id=source.id
            ),
        )
    )
    uploads_id = uploads.id
    await triggers.create_trigger(TriggerCreate(name="s", config=_config(workflow)))

    entitled = False

    page = await triggers.list_triggers(PageParams(limit=10))
    assert [item.kind for item in page.items] == [TriggerKind.SCHEDULE]
    with pytest.raises(EntitlementRequired):
        await triggers.get_trigger(uploads_id)


async def test_a_schedule_write_without_schedule_read_is_refused_first(
    session: AsyncSession,
    svc_role: Role,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    """The routes answer with the read view, so the write must not commit and
    then fail to read back."""
    scopes = (svc_role.scopes or frozenset()) - {
        "schedule:read",
        "schedule:update",
        "schedule:delete",
    }
    writer = TriggersService(session, svc_role.model_copy(update={"scopes": scopes}))
    assert "schedule:create" in scopes

    with pytest.raises(ScopeDeniedError):
        await writer.create_trigger(TriggerCreate(name="s", config=_config(workflow)))

    assert temporal.created == []


# ----- Listing -----


async def test_list_pages_through_two_kinds(
    monkeypatch: pytest.MonkeyPatch,
    triggers: TriggersService,
    schedules: WorkflowSchedulesService,
    workflow: SimpleNamespace,
    temporal: _Temporal,
) -> None:
    async def _entitled(entitlement: Entitlement) -> bool:
        return True

    monkeypatch.setattr(triggers, "has_entitlement", _entitled)
    source = await triggers.create_event_source(
        EventSourceCreate(
            name="bucket",
            config=AwsSnsSourceConfig(
                kind=EventSourceKind.AWS_SNS,
                topic_arn=f"arn:aws:sns:us-east-1:123456789012:t{uuid.uuid4().hex}",
            ),
        )
    )
    names: list[str] = []
    for i in range(2):
        await triggers.create_trigger(
            TriggerCreate(
                name=f"uploads {i}",
                config=ObjectStorageTriggerConfig(
                    kind=TriggerKind.OBJECT_STORAGE, source_id=source.id
                ),
            )
        )
        await triggers.create_trigger(
            TriggerCreate(name=f"schedule {i}", config=_config(workflow))
        )
        names = [f"schedule {i}", f"uploads {i}", *names]

    first = await triggers.list_triggers(PageParams(limit=3))
    assert first.has_more and first.next_cursor
    second = await triggers.list_triggers(PageParams(limit=3, cursor=first.next_cursor))

    items = [*first.items, *second.items]
    assert [item.name for item in items] == names
    assert {item.kind: type(item.config) for item in items} == {
        TriggerKind.OBJECT_STORAGE: type(first.items[1].config),
        TriggerKind.SCHEDULE: ScheduleTriggerConfigRead,
    }
    # The workflow filter finds schedules through their binding.
    bound = await triggers.list_triggers(
        PageParams(limit=10), workflow_id=WorkflowUUID.new(workflow.id)
    )
    assert {item.name for item in bound.items} == {"schedule 0", "schedule 1"}
    kinds = await triggers.list_triggers(
        PageParams(limit=10), kind=TriggerKind.SCHEDULE
    )
    assert {item.kind for item in kinds.items} == {TriggerKind.SCHEDULE}
