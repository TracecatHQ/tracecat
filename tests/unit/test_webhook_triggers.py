"""Webhooks as ``webhook`` triggers: both entry points write the same rows."""

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
from tracecat.authz.scopes import VIEWER_SCOPES
from tracecat.db.models import (
    AgentPreset,
    ManagedResource,
    Trigger,
    TriggerBinding,
    Webhook,
    Workflow,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError
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
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookCreate, WebhookUpdate
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.schedules import bridge
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWebhook
from tracecat.workspace_sync.enums import SyncResourceType

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


async def _add_workflow(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    title: str,
    *,
    webhook: str = "offline",
) -> SimpleNamespace:
    """A workflow and its webhook, returned as plain values.

    Every workflow gets a webhook row at creation; most stay offline. A
    service rolls the session back on a refused request, which expires loaded
    rows; reading them afterwards would need IO outside the loop.
    """
    workflow = Workflow(workspace_id=workspace_id, title=title, description="")
    session.add(workflow)
    await session.flush()
    row = Webhook(workspace_id=workspace_id, workflow_id=workflow.id, status=webhook)
    session.add(row)
    await session.commit()
    return SimpleNamespace(
        id=workflow.id,
        workspace_id=workspace_id,
        title=title,
        webhook_id=row.id,
        url=row.url,
    )


@pytest.fixture
async def workflow(session: AsyncSession, svc_workspace: Workspace) -> SimpleNamespace:
    return await _add_workflow(session, svc_workspace.id, "Alert intake")


@pytest.fixture
def triggers(session: AsyncSession, svc_role: Role) -> TriggersService:
    return TriggersService(session, svc_role)


def _config(workflow: SimpleNamespace, **settings: Any) -> WebhookTriggerConfig:
    return WebhookTriggerConfig(
        kind=TriggerKind.WEBHOOK, workflow_id=WorkflowUUID.new(workflow.id), **settings
    )


async def _turn(
    session: AsyncSession, role: Role, workflow: SimpleNamespace, status: str
) -> Webhook:
    """Turn the webhook on or off as the workflow page does."""
    return await webhook_service.update_webhook(
        role=role,
        session=session,
        workflow_id=WorkflowUUID.new(workflow.id),
        params=WebhookUpdate.model_validate({"status": status}),
    )


async def _webhook(session: AsyncSession, workflow: SimpleNamespace) -> Webhook:
    webhook = await session.scalar(
        select(Webhook)
        .where(Webhook.workflow_id == workflow.id)
        .execution_options(populate_existing=True)
    )
    assert webhook is not None
    return webhook


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


# ----- Turning a webhook on makes it a trigger -----


async def test_turning_a_webhook_on_gives_it_a_trigger_it_keeps(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    webhook = await _webhook(session, workflow)
    assert webhook.trigger_id is None

    await _turn(session, svc_role, workflow, "online")
    webhook = await _webhook(session, workflow)
    trigger_id = webhook.trigger_id
    assert trigger_id is not None
    trigger, bindings = await _rows(session, trigger_id)
    assert trigger.name == "Alert intake webhook"
    assert _shape(trigger, bindings) == (
        "webhook",
        "online",
        "workflow",
        workflow.id,
        "online",
    )

    # Offline keeps the row, so the webhook stays listed; on again reuses it.
    await _turn(session, svc_role, workflow, "offline")
    trigger, _ = await _rows(session, trigger_id)
    assert trigger.status == "offline"
    await _turn(session, svc_role, workflow, "online")
    assert (await _webhook(session, workflow)).trigger_id == trigger_id
    assert await _count(session, "trigger", workflow.workspace_id) == 1
    # Nothing that runs the webhook changed.
    assert (await _webhook(session, workflow)).url == workflow.url


async def test_settings_changes_alone_do_not_make_a_trigger(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    await webhook_service.update_webhook(
        role=svc_role,
        session=session,
        workflow_id=WorkflowUUID.new(workflow.id),
        params=WebhookUpdate(methods=["GET", "POST"], allowlisted_cidrs=["10.0.0.1"]),
    )

    assert (await _webhook(session, workflow)).trigger_id is None
    assert await _count(session, "trigger", workflow.workspace_id) == 0


async def test_creating_an_online_webhook_gives_it_a_trigger(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    workflow = Workflow(workspace_id=svc_workspace.id, title="Legacy", description="")
    session.add(workflow)
    await session.commit()

    webhook = await webhook_service.create_webhook(
        role=svc_role,
        session=session,
        workflow_id=WorkflowUUID.new(workflow.id),
        params=WebhookCreate(status="online"),
    )

    assert webhook.trigger_id is not None
    trigger, _ = await _rows(session, webhook.trigger_id)
    assert (trigger.kind, trigger.name) == ("webhook", "Legacy webhook")


async def test_importing_an_online_webhook_gives_it_a_trigger(
    session: AsyncSession, svc_role: Role, workflow: SimpleNamespace
) -> None:
    """Git sync and managed rollouts write webhooks through the import."""
    loaded = await session.scalar(select(Workflow).where(Workflow.id == workflow.id))
    assert loaded is not None
    await session.refresh(loaded, ["webhook"])
    importer = WorkflowImportService(session, role=svc_role)

    await importer._update_webhook(
        loaded, RemoteWebhook(methods=["POST"], status="online")
    )
    await session.commit()

    webhook = await _webhook(session, workflow)
    assert webhook.status == "online"
    assert webhook.trigger_id is not None
    trigger, bindings = await _rows(session, webhook.trigger_id)
    assert _shape(trigger, bindings)[:2] == ("webhook", "online")

    # An import that turns it off keeps the row, in step.
    await importer._update_webhook(
        loaded, RemoteWebhook(methods=["POST"], status="offline")
    )
    await session.commit()
    trigger, _ = await _rows(session, webhook.trigger_id)
    assert trigger.status == "offline"


# ----- The Triggers tab -----


async def test_both_entry_points_write_the_same_rows(
    session: AsyncSession,
    svc_role: Role,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other")
    await _turn(session, svc_role, workflow, "online")
    from_tab = await triggers.create_trigger(
        TriggerCreate(
            name="Made in the tab",
            status=TriggerStatus.ONLINE,
            config=_config(other),
        )
    )

    from_panel = (await _webhook(session, workflow)).trigger_id
    assert from_panel is not None
    shapes = [_shape(*await _rows(session, from_panel))]
    shapes.append(_shape(*await _rows(session, from_tab.id)))
    assert shapes == [
        ("webhook", "online", "workflow", workflow.id, "online"),
        ("webhook", "online", "workflow", other.id, "online"),
    ]
    # The tab used the workflow's existing webhook: same row, same URL.
    webhook = await _webhook(session, other)
    assert (webhook.id, webhook.url, webhook.trigger_id, webhook.status) == (
        other.webhook_id,
        other.url,
        from_tab.id,
        "online",
    )
    assert from_tab.name == "Made in the tab"


async def test_creating_in_the_tab_applies_the_settings(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(
            name="Intake",
            config=_config(
                workflow,
                methods=["GET"],
                allowlisted_cidrs=["10.0.0.1"],
                include_headers=True,
            ),
        )
    )

    webhook = await _webhook(session, workflow)
    assert (
        webhook.status,
        webhook.methods,
        webhook.allowlisted_cidrs,
        webhook.include_headers,
    ) == ("offline", ["GET"], ["10.0.0.1/32"], True)
    read = await triggers.get_trigger(created.id)
    assert read.config == WebhookTriggerConfigRead(
        kind=TriggerKind.WEBHOOK,
        workflow_id=WorkflowUUID.new(workflow.id),
        webhook_id=workflow.webhook_id,
        methods=["GET"],
        allowlisted_cidrs=["10.0.0.1/32"],
        include_headers=True,
        url=workflow.url,
        api_key=None,
    )
    [binding] = read.bindings
    assert (binding.target_type, binding.target_name) == (
        TriggerTargetType.WORKFLOW,
        "Alert intake",
    )


async def test_a_webhook_becomes_a_trigger_once(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    await triggers.create_trigger(TriggerCreate(name="a", config=_config(workflow)))

    with pytest.raises(TriggerError) as exc:
        await triggers.create_trigger(TriggerCreate(name="b", config=_config(workflow)))

    assert exc.value.code is TriggerErrorCode.CONFLICT
    assert await _count(session, "trigger", workflow.workspace_id) == 1


async def test_status_moves_together_from_either_side(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    await _turn(session, svc_role, workflow, "online")
    trigger_id = (await _webhook(session, workflow)).trigger_id
    assert trigger_id is not None

    await triggers.update_trigger(
        trigger_id, TriggerUpdate(status=TriggerStatus.OFFLINE)
    )
    trigger, _ = await _rows(session, trigger_id)
    assert (trigger.status, (await _webhook(session, workflow)).status) == (
        "offline",
        "offline",
    )

    # The previous release writes only ``webhook``; the database trigger
    # keeps ``trigger.status`` in step during a rollout.
    await session.execute(
        text("UPDATE webhook SET status = 'online' WHERE workflow_id = :id"),
        {"id": workflow.id},
    )
    await session.commit()
    trigger, _ = await _rows(session, trigger_id)
    assert trigger.status == "online"


async def test_a_config_replaces_every_webhook_setting(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    created = await triggers.create_trigger(
        TriggerCreate(
            name="a",
            config=_config(workflow, methods=["GET", "POST"], include_headers=True),
        )
    )

    await triggers.update_trigger(
        created.id,
        TriggerUpdate(
            name="renamed", config=_config(workflow, allowlisted_cidrs=["10.1.0.0/16"])
        ),
    )

    webhook = await _webhook(session, workflow)
    assert (webhook.methods, webhook.allowlisted_cidrs, webhook.include_headers) == (
        ["POST"],
        ["10.1.0.0/16"],
        False,
    )
    trigger, _ = await _rows(session, created.id)
    assert (trigger.name, trigger.status) == ("renamed", "offline")


async def test_a_webhook_cannot_move_to_another_workflow(
    session: AsyncSession,
    svc_workspace: Workspace,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    other = await _add_workflow(session, svc_workspace.id, "Other")
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    created_id = created.id

    with pytest.raises(TriggerError) as exc:
        await triggers.update_trigger(created_id, TriggerUpdate(config=_config(other)))

    assert exc.value.code is TriggerErrorCode.INVALID
    assert (await _webhook(session, other)).trigger_id is None


async def test_deleting_in_the_tab_turns_the_webhook_off_and_keeps_it(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    await _turn(session, svc_role, workflow, "online")
    trigger_id = (await _webhook(session, workflow)).trigger_id
    assert trigger_id is not None

    await triggers.delete_trigger(trigger_id)

    webhook = await _webhook(session, workflow)
    assert (webhook.id, webhook.url, webhook.status, webhook.trigger_id) == (
        workflow.webhook_id,
        workflow.url,
        "offline",
        None,
    )
    assert await _count(session, "trigger", workflow.workspace_id) == 0
    assert await _count(session, "trigger_binding", workflow.workspace_id) == 0
    assert (await triggers.list_triggers(PageParams(limit=10))).items == []

    # Turned on again, it is a trigger again, with a new row.
    await _turn(session, svc_role, workflow, "online")
    again = (await _webhook(session, workflow)).trigger_id
    assert again is not None and again != trigger_id


async def test_deleting_the_workflow_deletes_its_webhook_trigger(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "delete_schedule", AsyncMock())
    await _turn(session, svc_role, workflow, "online")
    trigger_id = (await _webhook(session, workflow)).trigger_id
    assert trigger_id is not None

    await WorkflowsManagementService(session, role=svc_role).delete_workflow(
        WorkflowUUID.new(workflow.id)
    )

    assert await _count(session, "trigger", workflow.workspace_id) == 0
    assert await _count(session, "trigger_binding", workflow.workspace_id) == 0
    assert (await triggers.list_triggers(PageParams(limit=10))).items == []


# ----- Managed workflows -----


async def _manage(session: AsyncSession, workflow: SimpleNamespace) -> None:
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


async def test_a_managed_webhook_toggles_in_the_tab_but_keeps_its_settings(
    session: AsyncSession, triggers: TriggersService, workflow: SimpleNamespace
) -> None:
    """As on the workflow page: on and off is the customer's, the rest the
    package's."""
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    created_id = created.id
    await _manage(session, workflow)

    await triggers.update_trigger(
        created_id,
        TriggerUpdate(status=TriggerStatus.ONLINE, config=_config(workflow)),
    )
    assert (await _webhook(session, workflow)).status == "online"

    with pytest.raises(ManagedResourceLockedError):
        await triggers.update_trigger(
            created_id, TriggerUpdate(config=_config(workflow, methods=["GET"]))
        )
    assert (await _webhook(session, workflow)).methods == ["POST"]


async def test_a_rollout_landing_mid_request_still_hits_the_managed_check(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    """Security r1 / thermos r1: the request carries the settings from before
    a rollout changed them. Compared with the row read before the ownership
    lock it looks like a status toggle; compared with the row under the lock
    it is a settings change, so the managed check must run."""
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    created_id = created.id
    await _manage(session, workflow)
    real_hold = webhook_service.hold_for_customer_write

    async def _rollout_then_hold(db: AsyncSession, workspace_id: uuid.UUID) -> None:
        # The rollout commits while the request waits for the lock.
        await db.execute(
            text("""UPDATE webhook SET methods = '["GET"]' WHERE workflow_id = :id"""),
            {"id": workflow.id},
        )
        await real_hold(db, workspace_id)

    monkeypatch.setattr(webhook_service, "hold_for_customer_write", _rollout_then_hold)

    with pytest.raises(ManagedResourceLockedError):
        await triggers.update_trigger(
            created_id,
            TriggerUpdate(status=TriggerStatus.ONLINE, config=_config(workflow)),
        )


# ----- Bindings are fixed until webhooks read them -----


async def test_a_webhook_trigger_keeps_its_one_workflow_target(
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


# ----- Scopes and the URL secret -----


async def test_only_callers_who_see_the_url_today_can_read_webhook_triggers(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    """The URL holds the webhook secret; ``workflow:read`` shows it on the
    workflow page, so the same scope gates the trigger."""
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    created_id = created.id

    viewer = TriggersService(
        session, svc_role.model_copy(update={"scopes": VIEWER_SCOPES})
    )
    read = await viewer.get_trigger(created_id)
    assert isinstance(read.config, WebhookTriggerConfigRead)
    assert read.config.url == workflow.url

    no_workflow_read = TriggersService(
        session,
        svc_role.model_copy(update={"scopes": frozenset({"trigger:read"})}),
    )
    assert (await no_workflow_read.list_triggers(PageParams(limit=10))).items == []
    with pytest.raises(ScopeDeniedError):
        await no_workflow_read.get_trigger(created_id)


async def test_the_tab_needs_the_webhook_scopes_too(
    session: AsyncSession,
    svc_role: Role,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    """Changing a webhook needs ``workflow:update`` on the workflow page; the
    tab is not a way around it."""
    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
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
    assert (await _webhook(session, other)).trigger_id is None
    trigger, _ = await _rows(session, created_id)
    assert (trigger.name, trigger.status) == ("a", "offline")


async def test_the_tab_writes_webhook_audit_records(
    monkeypatch: pytest.MonkeyPatch,
    triggers: TriggersService,
    workflow: SimpleNamespace,
) -> None:
    """An auditor filtering on webhooks sees changes made in the tab."""
    create_event = AsyncMock()
    monkeypatch.setattr(AuditService, "create_event", create_event)

    created = await triggers.create_trigger(
        TriggerCreate(name="a", config=_config(workflow))
    )
    await triggers.update_trigger(
        created.id, TriggerUpdate(status=TriggerStatus.ONLINE)
    )
    await triggers.delete_trigger(created.id)

    webhook_events = [
        call.kwargs.get("data")
        for call in create_event.await_args_list
        if call.kwargs["resource_type"] == "webhook"
        and call.kwargs["status"] is AuditEventStatus.SUCCESS
    ]
    assert len(webhook_events) == 3
    # Nothing in the records carries the URL or its secret.
    assert workflow.url.rsplit("/", 1)[-1] not in repr(create_event.await_args_list)


# ----- Listing -----


async def test_list_pages_through_three_kinds(
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
    saved = Workflow(
        workspace_id=svc_workspace.id, title="Saved", description="", version=1
    )
    session.add(saved)
    await session.commit()
    saved_id = saved.id
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
                    workflow_id=WorkflowUUID.new(saved_id),
                    every=timedelta(hours=1),
                ),
            )
        )
        await triggers.create_trigger(
            TriggerCreate(name=f"webhook {i}", config=_config(hooked))
        )
        names = [f"webhook {i}", f"schedule {i}", f"uploads {i}", *names]

    first = await triggers.list_triggers(PageParams(limit=4))
    assert first.has_more and first.next_cursor
    second = await triggers.list_triggers(PageParams(limit=4, cursor=first.next_cursor))

    items = [*first.items, *second.items]
    assert [item.name for item in items] == names
    assert {item.kind: type(item.config) for item in items} == {
        TriggerKind.OBJECT_STORAGE: ObjectStorageTriggerConfigRead,
        TriggerKind.SCHEDULE: ScheduleTriggerConfigRead,
        TriggerKind.WEBHOOK: WebhookTriggerConfigRead,
    }
    kinds = await triggers.list_triggers(PageParams(limit=10), kind=TriggerKind.WEBHOOK)
    assert [item.name for item in kinds.items] == ["webhook 1", "webhook 0"]
