"""Case triggers as ``case_trigger`` triggers: every entry point writes the
same rows."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import service as base_service
from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import VIEWER_SCOPES
from tracecat.cases.enums import CaseEventType
from tracecat.db.models import (
    AgentPreset,
    CaseTag,
    CaseTrigger,
    ManagedResource,
    Trigger,
    TriggerBinding,
    Webhook,
    Workflow,
    WorkflowDefinition,
    Workspace,
)
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import EntitlementRequired, ScopeDeniedError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.managed_resources.guard import ManagedResourceLockedError
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
    CaseEventTriggerConfig,
    CaseEventTriggerConfigRead,
    EventSourceCreate,
    ObjectStorageTriggerConfig,
    ObjectStorageTriggerConfigRead,
    ScheduleTriggerConfig,
    ScheduleTriggerConfigRead,
    TriggerBindingCreate,
    TriggerBindingUpdate,
    TriggerCreate,
    TriggerUpdate,
    WebhookTriggerConfig,
    WebhookTriggerConfigRead,
    WorkflowBindingTarget,
)
from tracecat.triggers.service import TriggersService
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig, CaseTriggerUpdate
from tracecat.workflow.case_triggers.service import (
    CaseTriggersService,
    default_trigger_name,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.schedules import bridge
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteCaseTrigger
from tracecat.workspace_sync.enums import SyncResourceType

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

_CLOSED = [CaseEventType.CASE_CLOSED]


def _definition() -> dict[str, Any]:
    dsl = DSLInput.model_validate(
        {
            "title": "Runnable workflow",
            "description": "Test workflow",
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
    return dsl.model_dump(exclude_unset=True)


async def _add_workflow(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    title: str,
    *,
    published: bool = True,
) -> SimpleNamespace:
    """A workflow with its webhook and offline case trigger, as plain values.

    Every workflow gets both rows at creation; most stay offline. A service
    rolls the session back on a refused request, which expires loaded rows;
    reading them afterwards would need IO outside the loop.
    """
    workflow = Workflow(
        workspace_id=workspace_id,
        title=title,
        description="",
        version=1 if published else None,
    )
    session.add(workflow)
    await session.flush()
    if published:
        session.add(
            WorkflowDefinition(
                workspace_id=workspace_id,
                workflow_id=workflow.id,
                version=1,
                content=_definition(),
            )
        )
    session.add(Webhook(workspace_id=workspace_id, workflow_id=workflow.id))
    row = CaseTrigger(
        workspace_id=workspace_id,
        workflow_id=workflow.id,
        status="offline",
        event_types=[],
        tag_filters=[],
    )
    session.add(row)
    await session.commit()
    return SimpleNamespace(
        id=workflow.id,
        workspace_id=workspace_id,
        title=title,
        case_trigger_id=row.id,
    )


@pytest.fixture
async def workflow(session: AsyncSession, svc_workspace: Workspace) -> SimpleNamespace:
    return await _add_workflow(session, svc_workspace.id, "Closed cases")


@pytest.fixture
def triggers(session: AsyncSession, svc_role: Role) -> TriggersService:
    return TriggersService(session, svc_role)


def _config(workflow: SimpleNamespace, **settings: Any) -> CaseEventTriggerConfig:
    return CaseEventTriggerConfig(
        kind=TriggerKind.CASE_TRIGGER,
        workflow_id=WorkflowUUID.new(workflow.id),
        **settings,
    )


def _case_triggers(session: AsyncSession, role: Role) -> CaseTriggersService:
    return CaseTriggersService(session, role=role)


async def _turn(
    session: AsyncSession, role: Role, workflow: SimpleNamespace, status: str
) -> CaseTrigger:
    """Turn the case trigger on or off as the workflow page does."""
    params = (
        CaseTriggerUpdate(status="online", event_types=_CLOSED)
        if status == "online"
        else CaseTriggerUpdate(status="offline")
    )
    return await _case_triggers(session, role).update_case_trigger(
        WorkflowUUID.new(workflow.id), params
    )


async def _case_trigger(
    session: AsyncSession, workflow: SimpleNamespace
) -> CaseTrigger:
    row = await session.scalar(
        select(CaseTrigger)
        .where(CaseTrigger.workflow_id == workflow.id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def _rows(
    session: AsyncSession, trigger_id: uuid.UUID
) -> tuple[Trigger, list[TriggerBinding]]:
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
    return trigger, bindings


async def _count(session: AsyncSession, table: str, workspace_id: uuid.UUID) -> int:
    result = await session.execute(
        text(f'SELECT count(*) FROM "{table}" WHERE workspace_id = :ws'),
        {"ws": workspace_id},
    )
    return result.scalar_one()


def _shape(trigger: Trigger, bindings: list[TriggerBinding]) -> tuple[Any, ...]:
    [binding] = bindings
    return (
        trigger.kind,
        trigger.status,
        binding.target_type,
        binding.workflow_id,
        binding.status,
    )


def _without_case_addons(monkeypatch: pytest.MonkeyPatch) -> None:
    """The organization's plan lacks ``case_addons``, for every service."""
    real = base_service.is_org_entitled

    async def _entitled(session: Any, org_id: Any, entitlement: Entitlement) -> bool:
        if entitlement is Entitlement.CASE_ADDONS:
            return False
        return await real(session, org_id, entitlement)

    monkeypatch.setattr(base_service, "is_org_entitled", _entitled)


def test_default_name_matches_the_backfill() -> None:
    assert default_trigger_name("Closed cases") == "Closed cases case trigger"
    assert default_trigger_name("  ") == "Case trigger"
    assert default_trigger_name(None) == "Case trigger"
    long = default_trigger_name("x" * 300)
    assert len(long) == 255 and long.endswith(" case trigger")


# ----- Turning a case trigger on makes it a trigger -----


async def test_turning_a_case_trigger_on_gives_it_a_trigger_it_keeps(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    assert (await _case_trigger(session, workflow)).trigger_id is None

    await _turn(session, svc_role, workflow, "online")
    trigger_id = (await _case_trigger(session, workflow)).trigger_id
    assert trigger_id is not None
    trigger, bindings = await _rows(session, trigger_id)
    assert trigger.name == "Closed cases case trigger"
    assert _shape(trigger, bindings) == (
        "case_trigger",
        "online",
        "workflow",
        workflow.id,
        "online",
    )

    # Offline keeps the row, so it stays listed; on again reuses it.
    await _turn(session, svc_role, workflow, "offline")
    trigger, _ = await _rows(session, trigger_id)
    assert trigger.status == "offline"
    await _turn(session, svc_role, workflow, "online")
    assert (await _case_trigger(session, workflow)).trigger_id == trigger_id
    assert await _count(session, "trigger", workflow.workspace_id) == 1


async def test_settings_changes_alone_do_not_make_a_trigger(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    await _case_triggers(session, svc_role).update_case_trigger(
        WorkflowUUID.new(workflow.id), CaseTriggerUpdate(event_types=_CLOSED)
    )
    assert (await _case_trigger(session, workflow)).trigger_id is None
    assert await _count(session, "trigger", workflow.workspace_id) == 0


async def test_upserting_an_online_case_trigger_gives_it_a_trigger(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    """``POST /workflows/{id}/case-trigger`` and the workflow YAML editors."""
    await _case_triggers(session, svc_role).upsert_case_trigger(
        WorkflowUUID.new(workflow.id),
        CaseTriggerConfig(status="online", event_types=_CLOSED),
    )
    row = await _case_trigger(session, workflow)
    assert row.trigger_id is not None
    trigger, bindings = await _rows(session, row.trigger_id)
    assert _shape(trigger, bindings)[:2] == ("case_trigger", "online")


async def test_importing_an_online_case_trigger_gives_it_a_trigger(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    """Git sync and managed rollouts write case triggers through the import."""
    loaded = await session.scalar(select(Workflow).where(Workflow.id == workflow.id))
    assert loaded is not None
    importer = WorkflowImportService(session, role=svc_role)

    await importer._update_case_trigger(
        loaded, RemoteCaseTrigger(status="online", event_types=_CLOSED)
    )
    await session.commit()

    row = await _case_trigger(session, workflow)
    assert row.status == "online"
    assert row.trigger_id is not None
    trigger, bindings = await _rows(session, row.trigger_id)
    assert _shape(trigger, bindings)[:2] == ("case_trigger", "online")

    # An import that clears it keeps the row, in step.
    await importer._update_case_trigger(loaded, None)
    await session.commit()
    trigger, _ = await _rows(session, row.trigger_id)
    assert trigger.status == "offline"


async def test_the_dispatcher_turning_it_off_reaches_the_trigger(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    """The runtime writes ``case_trigger.status`` directly (an invalid
    trigger is disabled); the database trigger copies it."""
    await _turn(session, svc_role, workflow, "online")
    trigger_id = (await _case_trigger(session, workflow)).trigger_id
    assert trigger_id is not None

    await session.execute(
        text("UPDATE case_trigger SET status = 'offline' WHERE workflow_id = :id"),
        {"id": workflow.id},
    )
    await session.commit()

    trigger, _ = await _rows(session, trigger_id)
    assert trigger.status == "offline"


# ----- The Triggers tab -----


async def test_both_entry_points_write_the_same_rows(
    session: AsyncSession,
    svc_workspace: Workspace,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other")
    await _turn(session, svc_role, workflow, "online")
    created = await triggers.create_trigger(
        TriggerCreate(
            name="Closed",
            config=_config(other, event_types=_CLOSED),
            status=TriggerStatus.ONLINE,
        )
    )

    page_row = await _case_trigger(session, workflow)
    tab_row = await _case_trigger(session, other)
    assert page_row.trigger_id is not None and tab_row.trigger_id == created.id
    page_shape = _shape(*await _rows(session, page_row.trigger_id))
    tab_shape = _shape(*await _rows(session, created.id))
    assert page_shape[:3] == tab_shape[:3] == ("case_trigger", "online", "workflow")
    assert (tab_row.status, tab_row.event_types) == ("online", ["case_closed"])

    read = await triggers.get_trigger(created.id)
    assert read.name == "Closed"
    assert read.config == CaseEventTriggerConfigRead(
        kind=TriggerKind.CASE_TRIGGER,
        workflow_id=WorkflowUUID.new(other.id),
        case_trigger_id=other.case_trigger_id,
        event_types=_CLOSED,
        tag_filters=[],
    )


async def test_creating_in_the_tab_applies_the_settings(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    session.add(
        CaseTag(
            workspace_id=workflow.workspace_id,
            name="phishing",
            ref="phishing",
            color=None,
        )
    )
    await session.commit()

    created = await triggers.create_trigger(
        TriggerCreate(
            name="Closed",
            config=_config(
                workflow, event_types=_CLOSED, tag_filters=[" phishing ", "phishing"]
            ),
        )
    )

    row = await _case_trigger(session, workflow)
    # No status given: an offline case trigger stays offline, now listed.
    assert (row.trigger_id, row.status, row.event_types, row.tag_filters) == (
        created.id,
        "offline",
        ["case_closed"],
        ["phishing"],
    )
    trigger, _ = await _rows(session, created.id)
    assert (trigger.name, trigger.status) == ("Closed", "offline")


async def test_creating_for_a_live_unlinked_case_trigger_keeps_it_online(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    """An online case trigger the backfill could not link yet: making it a
    trigger in the tab must not turn it off."""
    await session.execute(
        text(
            "UPDATE case_trigger SET status = 'online',"
            """ event_types = '["case_closed"]' WHERE workflow_id = :id"""
        ),
        {"id": workflow.id},
    )
    await session.commit()

    created = await triggers.create_trigger(
        TriggerCreate(name="Closed", config=_config(workflow))
    )

    row = await _case_trigger(session, workflow)
    assert (row.status, row.event_types) == ("online", ["case_closed"])
    trigger, _ = await _rows(session, created.id)
    assert trigger.status == "online"


async def test_creating_with_an_explicit_offline_turns_it_off(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    await session.execute(
        text(
            "UPDATE case_trigger SET status = 'online',"
            """ event_types = '["case_closed"]' WHERE workflow_id = :id"""
        ),
        {"id": workflow.id},
    )
    await session.commit()

    created = await triggers.create_trigger(
        TriggerCreate(
            name="Closed", config=_config(workflow), status=TriggerStatus.OFFLINE
        )
    )
    assert (await _case_trigger(session, workflow)).status == "offline"
    trigger, _ = await _rows(session, created.id)
    assert trigger.status == "offline"


async def test_a_case_trigger_becomes_a_trigger_once(
    triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    await triggers.create_trigger(TriggerCreate(name="a", config=_config(workflow)))
    with pytest.raises(TriggerError) as refused:
        await triggers.create_trigger(TriggerCreate(name="b", config=_config(workflow)))
    assert refused.value.code is TriggerErrorCode.CONFLICT


async def test_online_needs_event_types_and_a_published_workflow(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    with pytest.raises(TriggerError) as no_events:
        await triggers.create_trigger(
            TriggerCreate(
                name="a", config=_config(workflow), status=TriggerStatus.ONLINE
            )
        )
    assert no_events.value.code is TriggerErrorCode.INVALID
    assert "event_types" in str(no_events.value)

    # The refusal rolled the session back; loaded rows are expired.
    draft = await _add_workflow(
        session, workflow.workspace_id, "Draft", published=False
    )
    with pytest.raises(TriggerError, match="Publish the workflow") as unpublished:
        await triggers.create_trigger(
            TriggerCreate(
                name="b",
                config=_config(draft, event_types=_CLOSED),
                status=TriggerStatus.ONLINE,
            )
        )
    assert unpublished.value.code is TriggerErrorCode.INVALID
    assert await _count(session, "trigger", workflow.workspace_id) == 0

    with pytest.raises(TriggerError) as missing_tag:
        await triggers.create_trigger(
            TriggerCreate(name="c", config=_config(workflow, tag_filters=["nope"]))
        )
    assert missing_tag.value.code is TriggerErrorCode.NOT_FOUND
    assert "nope" in str(missing_tag.value)


async def test_status_moves_together_from_either_side(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, event_types=_CLOSED))
    )
    created_id = created.id

    await triggers.update_trigger(
        created_id, TriggerUpdate(status=TriggerStatus.ONLINE)
    )
    assert (await _case_trigger(session, workflow)).status == "online"
    trigger, _ = await _rows(session, created_id)
    assert trigger.status == "online"

    await _turn(session, svc_role, workflow, "offline")
    trigger, _ = await _rows(session, created_id)
    assert trigger.status == "offline"


async def test_a_config_sets_only_the_settings_it_names(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, event_types=_CLOSED))
    )
    created_id = created.id

    await triggers.update_trigger(
        created_id,
        TriggerUpdate(
            name="renamed", config=_config(workflow), status=TriggerStatus.ONLINE
        ),
    )

    row = await _case_trigger(session, workflow)
    assert (row.status, row.event_types) == ("online", ["case_closed"])
    trigger, _ = await _rows(session, created_id)
    assert (trigger.name, trigger.status) == ("renamed", "online")

    await triggers.update_trigger(
        created_id,
        TriggerUpdate(
            config=_config(
                workflow,
                event_types=[CaseEventType.CASE_CREATED, CaseEventType.CASE_CLOSED],
            )
        ),
    )
    assert (await _case_trigger(session, workflow)).event_types == [
        "case_created",
        "case_closed",
    ]


async def test_a_case_trigger_cannot_move_to_another_workflow(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other")
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    with pytest.raises(TriggerError, match="workflow cannot change"):
        await triggers.update_trigger(created.id, TriggerUpdate(config=_config(other)))
    assert (await _case_trigger(session, other)).trigger_id is None


async def test_deleting_in_the_tab_turns_it_off_and_keeps_it(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    await _turn(session, svc_role, workflow, "online")
    trigger_id = (await _case_trigger(session, workflow)).trigger_id
    assert trigger_id is not None

    await triggers.delete_trigger(trigger_id)

    row = await _case_trigger(session, workflow)
    assert (row.id, row.status, row.event_types, row.trigger_id) == (
        workflow.case_trigger_id,
        "offline",
        ["case_closed"],
        None,
    )
    assert await _count(session, "trigger", workflow.workspace_id) == 0
    assert await _count(session, "trigger_binding", workflow.workspace_id) == 0
    assert (await triggers.list_triggers(PageParams(limit=10))).items == []

    # Turned on again, it is a trigger again, with a new row.
    await _turn(session, svc_role, workflow, "online")
    again = (await _case_trigger(session, workflow)).trigger_id
    assert again is not None and again != trigger_id


async def test_deleting_the_workflow_deletes_its_case_trigger_trigger(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "delete_schedule", AsyncMock())
    await _turn(session, svc_role, workflow, "online")
    assert (await _case_trigger(session, workflow)).trigger_id is not None

    await WorkflowsManagementService(session, role=svc_role).delete_workflow(
        WorkflowUUID.new(workflow.id)
    )

    assert await _count(session, "trigger", workflow.workspace_id) == 0
    assert await _count(session, "trigger_binding", workflow.workspace_id) == 0
    assert (await triggers.list_triggers(PageParams(limit=10))).items == []


# ----- Managed workflows -----


async def test_a_managed_case_trigger_is_refused_in_the_tab_too(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    """The workflow page refuses every case trigger change on a managed
    workflow; the tab does the same."""
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, event_types=_CLOSED))
    )
    created_id = created.id
    session.add(
        ManagedResource(
            workspace_id=workflow.workspace_id,
            resource_type=SyncResourceType.WORKFLOW.value,
            local_id=workflow.id,
            source_id=f"source-{workflow.id.hex[:8]}",
            source_path=f"workflows/{workflow.id.hex[:8]}.yml",
            deployed_tag="v1.0.0",
            deployed_commit_sha="a" * 40,
        )
    )
    await session.commit()

    for call in (
        triggers.update_trigger(created_id, TriggerUpdate(status=TriggerStatus.ONLINE)),
        triggers.delete_trigger(created_id),
    ):
        with pytest.raises(ManagedResourceLockedError):
            await call
    row = await _case_trigger(session, workflow)
    assert (row.status, row.trigger_id) == ("offline", created_id)


# ----- Bindings are fixed until case triggers read them -----


async def test_a_case_trigger_keeps_its_one_workflow_target(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other")
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
        TriggerCreate(name="a", config=_config(workflow))
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
        with pytest.raises(TriggerError, match="runs its own workflow only"):
            await call

    _, bindings = await _rows(session, created_id)
    assert [(b.id, b.status) for b in bindings] == [(binding.id, "online")]


# ----- Scopes and the plan -----


async def test_reading_needs_the_scope_the_workflow_page_needs(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    created_id = created.id

    viewer = TriggersService(
        session, svc_role.model_copy(update={"scopes": VIEWER_SCOPES})
    )
    read = await viewer.get_trigger(created_id)
    assert isinstance(read.config, CaseEventTriggerConfigRead)

    no_workflow_read = TriggersService(
        session,
        svc_role.model_copy(update={"scopes": frozenset({"trigger:read"})}),
    )
    assert (await no_workflow_read.list_triggers(PageParams(limit=10))).items == []
    with pytest.raises(ScopeDeniedError):
        await no_workflow_read.get_trigger(created_id)


async def test_the_tab_needs_the_case_trigger_scopes_too(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    """Changing a case trigger needs ``workflow:update`` on the workflow
    page; the tab is not a way around it."""
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, event_types=_CLOSED))
    )
    created_id = created.id
    scopes = frozenset(
        {"trigger:read", "trigger:create", "trigger:update", "trigger:delete"}
        | {"workflow:read", "workflow:execute"}
    )
    limited = TriggersService(session, svc_role.model_copy(update={"scopes": scopes}))

    for call in (
        limited.update_trigger(created_id, TriggerUpdate(status=TriggerStatus.ONLINE)),
        limited.update_trigger(created_id, TriggerUpdate(name="renamed")),
        limited.delete_trigger(created_id),
    ):
        with pytest.raises(ScopeDeniedError):
            await call

    other = await _add_workflow(session, workflow.workspace_id, "Other")
    for missing in ("workflow:update", "workflow:execute"):
        refused = TriggersService(
            session,
            svc_role.model_copy(
                update={"scopes": (svc_role.scopes or frozenset()) - {missing}}
            ),
        )
        with pytest.raises(ScopeDeniedError):
            await refused.create_trigger(TriggerCreate(name="b", config=_config(other)))
    assert (await _case_trigger(session, other)).trigger_id is None
    trigger, _ = await _rows(session, created_id)
    assert (trigger.name, trigger.status) == ("a", "offline")


async def test_without_case_addons_the_kind_is_locked(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, event_types=_CLOSED))
    )
    created_id = created.id
    _without_case_addons(monkeypatch)
    locked = TriggersService(session, svc_role)

    page = await locked.list_triggers(PageParams(limit=10))
    assert page.items == []
    assert [(c.kind, c.entitlement, c.count) for c in page.locked] == [
        (TriggerKind.CASE_TRIGGER, Entitlement.CASE_ADDONS, 1)
    ]
    for call in (
        locked.get_trigger(created_id),
        locked.update_trigger(created_id, TriggerUpdate(status=TriggerStatus.ONLINE)),
        locked.delete_trigger(created_id),
        locked.create_trigger(
            TriggerCreate(
                name="b",
                config=_config(
                    await _add_workflow(session, workflow.workspace_id, "Other")
                ),
            )
        ),
    ):
        with pytest.raises(EntitlementRequired):
            await call

    # The workflow page's routes refuse as they did; sync still applies.
    case_triggers = _case_triggers(session, svc_role)
    with pytest.raises(EntitlementRequired):
        await case_triggers.update_case_trigger(
            WorkflowUUID.new(workflow.id), CaseTriggerUpdate(status="online")
        )
    with pytest.raises(EntitlementRequired):
        await case_triggers.get_case_trigger(WorkflowUUID.new(workflow.id))
    await case_triggers.sync_case_trigger(
        WorkflowUUID.new(workflow.id),
        CaseTriggerConfig(status="online", event_types=_CLOSED),
    )
    row = await _case_trigger(session, workflow)
    assert (row.status, row.trigger_id) == ("online", created_id)


async def test_the_tab_writes_case_trigger_audit_records(
    monkeypatch: pytest.MonkeyPatch,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    """An auditor filtering on case triggers sees changes made in the tab."""
    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)

    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow, event_types=_CLOSED))
    )
    await triggers.update_trigger(
        created.id, TriggerUpdate(status=TriggerStatus.ONLINE)
    )
    await triggers.delete_trigger(created.id)

    case_trigger_events = [
        call.kwargs.get("data")
        for call in create_event.await_args_list
        if call.kwargs["resource_type"] == "case_trigger"
        and call.kwargs["status"] is AuditEventStatus.SUCCESS
    ]
    assert len(case_trigger_events) == 3


# ----- Listing -----


async def test_list_pages_through_four_kinds(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
) -> None:
    async def _entitled(entitlement: Entitlement) -> bool:
        return True

    monkeypatch.setattr(triggers, "has_entitlement", _entitled)
    monkeypatch.setattr(bridge, "create_schedule", AsyncMock())
    source = await triggers.create_event_source(
        EventSourceCreate(
            name="bucket",
            config=AwsSnsSourceConfig(
                kind=EventSourceKind.AWS_SNS,
                topic_arn=f"arn:aws:sns:us-east-1:123456789012:t{uuid.uuid4().hex}",
            ),
        )
    )
    saved = await _add_workflow(session, svc_workspace.id, "Saved")
    names: list[str] = []
    for i in range(2):
        hooked = await _add_workflow(session, svc_workspace.id, f"Hook {i}")
        await triggers.create_trigger(
            TriggerCreate(
                name=f"uploads {i}",
                config=ObjectStorageTriggerConfig(
                    kind=TriggerKind.OBJECT_STORAGE, source_id=source.id
                ),
            )
        )
        await triggers.create_trigger(
            TriggerCreate(
                name=f"schedule {i}",
                config=ScheduleTriggerConfig(
                    kind=TriggerKind.SCHEDULE,
                    workflow_id=WorkflowUUID.new(saved.id),
                    every=timedelta(hours=1),
                ),
            )
        )
        await triggers.create_trigger(
            TriggerCreate(
                name=f"webhook {i}",
                config=WebhookTriggerConfig(
                    kind=TriggerKind.WEBHOOK, workflow_id=WorkflowUUID.new(hooked.id)
                ),
            )
        )
        await triggers.create_trigger(
            TriggerCreate(name=f"cases {i}", config=_config(hooked))
        )
        names = [
            f"cases {i}",
            f"webhook {i}",
            f"schedule {i}",
            f"uploads {i}",
            *names,
        ]

    first = await triggers.list_triggers(PageParams(limit=5))
    assert first.has_more and first.next_cursor
    second = await triggers.list_triggers(PageParams(limit=5, cursor=first.next_cursor))

    items = [*first.items, *second.items]
    assert [item.name for item in items] == names
    assert {item.kind: type(item.config) for item in items} == {
        TriggerKind.OBJECT_STORAGE: ObjectStorageTriggerConfigRead,
        TriggerKind.SCHEDULE: ScheduleTriggerConfigRead,
        TriggerKind.WEBHOOK: WebhookTriggerConfigRead,
        TriggerKind.CASE_TRIGGER: CaseEventTriggerConfigRead,
    }
    kinds = await triggers.list_triggers(
        PageParams(limit=10), kind=TriggerKind.CASE_TRIGGER
    )
    assert [item.name for item in kinds.items] == ["cases 1", "cases 0"]
