"""Tests for TriggersService: event sources, triggers and bindings."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import (
    AgentPreset,
    ObjectStorageTrigger,
    Trigger,
    TriggerBinding,
    Workflow,
    Workspace,
)
from tracecat.exceptions import ScopeDeniedError
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams
from tracecat.triggers import service as trigger_service
from tracecat.triggers.enums import (
    EventSourceKind,
    SubscriptionStatus,
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
    EventSourceUpdate,
    ObjectStorageTriggerConfig,
    ObjectStorageTriggerConfigRead,
    TriggerBindingCreate,
    TriggerBindingUpdate,
    TriggerCreate,
    TriggerUpdate,
    WorkflowBindingTarget,
)
from tracecat.triggers.service import TriggersService, read_event_source
from tracecat.triggers.types import build_occurrence_key

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

TOPIC = "arn:aws:sns:us-east-1:123456789012:bucket-events"


def _sns(topic: str = TOPIC) -> AwsSnsSourceConfig:
    return AwsSnsSourceConfig(kind=EventSourceKind.AWS_SNS, topic_arn=topic)


@pytest.fixture(autouse=True)
def entitled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the org object storage triggers; the service checks each kind's."""

    async def _entitled(self: TriggersService, entitlement: object) -> bool:
        return True

    monkeypatch.setattr(TriggersService, "has_entitlement", _entitled)


@pytest.fixture
def service(session: AsyncSession, svc_role: Role) -> TriggersService:
    return TriggersService(session, svc_role)


@pytest.fixture
async def other_service(
    session: AsyncSession, svc_workspace: Workspace
) -> TriggersService:
    """The same organization, another workspace."""
    other = Workspace(name="other", organization_id=svc_workspace.organization_id)
    session.add(other)
    await session.commit()
    role = Role(
        type="user",
        workspace_id=other.id,
        organization_id=other.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )
    return TriggersService(session, role)


@pytest.fixture
async def workflow(session: AsyncSession, svc_workspace: Workspace) -> Workflow:
    workflow = Workflow(
        workspace_id=svc_workspace.id, title="On upload", description=""
    )
    session.add(workflow)
    await session.commit()
    return workflow


@pytest.fixture
async def preset(session: AsyncSession, svc_workspace: Workspace) -> AgentPreset:
    preset = AgentPreset(
        workspace_id=svc_workspace.id,
        name="Triage",
        slug=f"triage-{uuid.uuid4().hex[:8]}",
        model_name="m",
        model_provider="p",
    )
    session.add(preset)
    await session.commit()
    return preset


def _os(config: object) -> ObjectStorageTriggerConfigRead:
    """Narrow a read config to the object storage kind."""
    assert isinstance(config, ObjectStorageTriggerConfigRead)
    return config


async def _trigger(service: TriggersService, prefix: str = "") -> uuid.UUID:
    source = await service.create_event_source(
        EventSourceCreate(name="bucket", config=_sns(f"{TOPIC}-{uuid.uuid4().hex}"))
    )
    trigger = await service.create_trigger(
        TriggerCreate(
            name="uploads",
            config=ObjectStorageTriggerConfig(
                kind=TriggerKind.OBJECT_STORAGE, source_id=source.id, key_prefix=prefix
            ),
        )
    )
    return trigger.id


# ----- Event sources -----


async def test_create_event_source_gives_a_stable_unguessable_url(
    service: TriggersService,
) -> None:
    source = await service.create_event_source(
        EventSourceCreate(name="bucket", config=_sns())
    )
    read = read_event_source(source, service.role)

    assert read.status is TriggerStatus.OFFLINE
    assert read.subscription_status is SubscriptionStatus.PENDING
    assert read.config == _sns()
    assert len(source.endpoint_token) >= 43
    assert read.endpoint_url is not None
    assert read.endpoint_url.endswith(f"/ingest/sns/{source.endpoint_token}")

    renamed = await service.update_event_source(
        source.id, EventSourceUpdate(name="renamed", status=TriggerStatus.ONLINE)
    )
    assert read_event_source(renamed, service.role).endpoint_url == read.endpoint_url

    # A viewer sees the source but not the URL that reaches its ingest endpoint,
    # nor the subscription ARN that is enough to unsubscribe it.
    source.subscription_ref = f"{TOPIC}:sub"
    assert read_event_source(source, service.role).subscription_ref == f"{TOPIC}:sub"
    viewer = service.role.model_copy(update={"scopes": frozenset({"trigger:read"})})
    viewer_read = read_event_source(source, viewer)
    assert viewer_read.endpoint_url is None
    assert viewer_read.subscription_ref is None


async def test_one_source_per_topic_per_workspace(
    service: TriggersService, other_service: TriggersService
) -> None:
    await service.create_event_source(EventSourceCreate(name="a", config=_sns()))
    # Another workspace may subscribe to the same topic.
    await other_service.create_event_source(EventSourceCreate(name="a", config=_sns()))

    with pytest.raises(TriggerError) as exc:
        await service.create_event_source(EventSourceCreate(name="b", config=_sns()))
    assert exc.value.code is TriggerErrorCode.CONFLICT


async def test_changing_the_topic_resets_the_subscription(
    service: TriggersService,
) -> None:
    source = await service.create_event_source(
        EventSourceCreate(name="bucket", config=_sns())
    )
    source.subscription_status = SubscriptionStatus.CONFIRMED.value
    source.subscription_ref = f"{TOPIC}:sub"
    await service.session.commit()

    same = await service.update_event_source(
        source.id, EventSourceUpdate(config=_sns())
    )
    assert same.subscription_status == SubscriptionStatus.CONFIRMED

    moved = await service.update_event_source(
        source.id, EventSourceUpdate(config=_sns(f"{TOPIC}-2"))
    )
    assert moved.subscription_status == SubscriptionStatus.PENDING
    assert moved.subscription_ref is None
    assert moved.config == {"topic_arn": f"{TOPIC}-2"}


async def test_list_event_sources_pages_newest_first(service: TriggersService) -> None:
    for i in range(3):
        await service.create_event_source(
            EventSourceCreate(name=f"s{i}", config=_sns(f"{TOPIC}-{i}"))
        )

    first = await service.list_event_sources(PageParams(limit=2))
    second = await service.list_event_sources(
        PageParams(limit=2, cursor=first.next_cursor)
    )

    assert [s.name for s in first.items] == ["s2", "s1"]
    assert [s.name for s in second.items] == ["s0"]


# ----- Triggers -----


async def test_trigger_header_and_settings_round_trip(
    service: TriggersService, session: AsyncSession
) -> None:
    trigger_id = await _trigger(service, prefix="incoming/")

    header = await session.scalar(select(Trigger).where(Trigger.id == trigger_id))
    settings = await session.get(ObjectStorageTrigger, trigger_id)
    assert header is not None and settings is not None
    assert header.kind == TriggerKind.OBJECT_STORAGE
    assert settings.key_prefix == "incoming/"

    read = await service.get_trigger(trigger_id)
    assert read.kind is TriggerKind.OBJECT_STORAGE
    assert read.config == ObjectStorageTriggerConfigRead(
        kind=TriggerKind.OBJECT_STORAGE,
        source_id=settings.source_id,
        source_name="bucket",
        key_prefix="incoming/",
    )
    assert read.status is TriggerStatus.OFFLINE
    assert read.bindings == []

    await service.update_trigger(
        trigger_id,
        TriggerUpdate(
            config=ObjectStorageTriggerConfig(
                kind=TriggerKind.OBJECT_STORAGE,
                source_id=settings.source_id,
                key_prefix="other/",
            ),
            status=TriggerStatus.ONLINE,
        ),
    )
    updated = await service.get_trigger(trigger_id)
    assert _os(updated.config).key_prefix == "other/"
    assert updated.status is TriggerStatus.ONLINE
    assert updated.updated_at >= read.updated_at


async def test_a_refused_trigger_leaves_no_header(
    service: TriggersService,
    other_service: TriggersService,
    session: AsyncSession,
) -> None:
    """Header and settings are written together or not at all."""
    foreign = await other_service.create_event_source(
        EventSourceCreate(name="theirs", config=_sns())
    )

    with pytest.raises(TriggerError) as exc:
        await service.create_trigger(
            TriggerCreate(
                name="t",
                config=ObjectStorageTriggerConfig(
                    kind=TriggerKind.OBJECT_STORAGE, source_id=foreign.id
                ),
            )
        )
    assert exc.value.code is TriggerErrorCode.NOT_FOUND

    headers = await session.scalars(
        select(Trigger.id).where(Trigger.workspace_id == service.workspace_id)
    )
    assert headers.all() == []
    assert (await service.list_triggers(PageParams())).items == []


async def test_workspaces_cannot_see_or_change_each_others_rows(
    service: TriggersService, other_service: TriggersService
) -> None:
    trigger_id = await _trigger(service)
    source_id = _os((await service.get_trigger(trigger_id)).config).source_id

    for call in (
        other_service.get_trigger(trigger_id),
        other_service.update_trigger(trigger_id, TriggerUpdate(name="x")),
        other_service.delete_trigger(trigger_id),
        other_service.get_event_source(source_id),
        other_service.update_event_source(source_id, EventSourceUpdate(name="x")),
    ):
        with pytest.raises(TriggerError) as exc:
            await call
        assert exc.value.code is TriggerErrorCode.NOT_FOUND

    assert (await other_service.list_triggers(PageParams())).items == []
    assert (await other_service.list_event_sources(PageParams())).items == []
    assert (await service.get_trigger(trigger_id)).name == "uploads"


async def test_delete_trigger_removes_its_bindings(
    service: TriggersService, workflow: Workflow
) -> None:
    trigger_id = await _trigger(service)
    binding = await service.create_binding(
        trigger_id,
        TriggerBindingCreate(
            target=WorkflowBindingTarget(
                type=TriggerTargetType.WORKFLOW,
                workflow_id=WorkflowUUID.new(workflow.id),
            )
        ),
    )

    await service.delete_trigger(trigger_id)

    with pytest.raises(TriggerError):
        await service.get_trigger(trigger_id)
    with pytest.raises(TriggerError):
        await service.update_binding(
            trigger_id, binding.id, TriggerBindingUpdate(status=TriggerStatus.OFFLINE)
        )


# ----- Bindings -----


async def test_bindings_for_both_target_types(
    service: TriggersService, workflow: Workflow, preset: AgentPreset
) -> None:
    trigger_id = await _trigger(service)

    wf = await service.create_binding(
        trigger_id,
        TriggerBindingCreate(
            target=WorkflowBindingTarget(
                type=TriggerTargetType.WORKFLOW,
                workflow_id=WorkflowUUID.new(workflow.id).short(),
            )
        ),
    )
    agent = await service.create_binding(
        trigger_id,
        TriggerBindingCreate(
            target=AgentBindingTarget(
                type=TriggerTargetType.AGENT,
                agent_preset_id=preset.id,
                instruction="Summarize the file",
            )
        ),
    )

    read = await service.get_trigger(trigger_id)
    by_type = {b.target_type: b for b in read.bindings}
    assert by_type[TriggerTargetType.WORKFLOW].target_id == workflow.id
    assert by_type[TriggerTargetType.WORKFLOW].target_name == "On upload"
    assert by_type[TriggerTargetType.WORKFLOW].instruction is None
    assert by_type[TriggerTargetType.AGENT].target_id == preset.id
    assert by_type[TriggerTargetType.AGENT].target_name == "Triage"
    assert by_type[TriggerTargetType.AGENT].instruction == "Summarize the file"

    updated = await service.update_binding(
        trigger_id,
        agent.id,
        TriggerBindingUpdate(instruction="Classify", status=TriggerStatus.OFFLINE),
    )
    assert updated.instruction == "Classify"
    assert updated.status == TriggerStatus.OFFLINE

    with pytest.raises(TriggerError) as exc:
        await service.update_binding(
            trigger_id, wf.id, TriggerBindingUpdate(instruction="nope")
        )
    assert exc.value.code is TriggerErrorCode.INVALID

    await service.delete_binding(trigger_id, wf.id)
    assert [b.id for b in (await service.get_trigger(trigger_id)).bindings] == [
        agent.id
    ]


async def test_a_trigger_binds_a_target_once(
    service: TriggersService, workflow: Workflow
) -> None:
    trigger_id = await _trigger(service)
    params = TriggerBindingCreate(
        target=WorkflowBindingTarget(
            type=TriggerTargetType.WORKFLOW, workflow_id=WorkflowUUID.new(workflow.id)
        )
    )
    await service.create_binding(trigger_id, params)

    with pytest.raises(TriggerError) as exc:
        await service.create_binding(trigger_id, params)
    assert exc.value.code is TriggerErrorCode.CONFLICT


async def test_binding_targets_must_be_in_the_workspace(
    service: TriggersService,
    other_service: TriggersService,
    workflow: Workflow,
    preset: AgentPreset,
    session: AsyncSession,
) -> None:
    trigger_id = await _trigger(other_service)
    for target in (
        WorkflowBindingTarget(
            type=TriggerTargetType.WORKFLOW, workflow_id=WorkflowUUID.new(workflow.id)
        ),
        AgentBindingTarget(
            type=TriggerTargetType.AGENT, agent_preset_id=preset.id, instruction="i"
        ),
    ):
        with pytest.raises(TriggerError) as exc:
            await other_service.create_binding(
                trigger_id, TriggerBindingCreate(target=target)
            )
        assert exc.value.code is TriggerErrorCode.NOT_FOUND

    # A soft-deleted preset is not a target either.
    own_trigger = await _trigger(service)
    preset.deleted_at = preset.created_at
    await session.commit()
    with pytest.raises(TriggerError) as exc:
        await service.create_binding(
            own_trigger,
            TriggerBindingCreate(
                target=AgentBindingTarget(
                    type=TriggerTargetType.AGENT,
                    agent_preset_id=preset.id,
                    instruction="i",
                )
            ),
        )
    assert exc.value.code is TriggerErrorCode.NOT_FOUND


async def test_list_triggers_filters_and_returns_bindings_in_one_page(
    service: TriggersService, workflow: Workflow, preset: AgentPreset
) -> None:
    to_workflow = await _trigger(service)
    to_agent = await _trigger(service)
    unbound = await _trigger(service)
    await service.create_binding(
        to_workflow,
        TriggerBindingCreate(
            target=WorkflowBindingTarget(
                type=TriggerTargetType.WORKFLOW,
                workflow_id=WorkflowUUID.new(workflow.id),
            )
        ),
    )
    await service.create_binding(
        to_agent,
        TriggerBindingCreate(
            target=AgentBindingTarget(
                type=TriggerTargetType.AGENT, agent_preset_id=preset.id, instruction="i"
            )
        ),
    )

    everything = await service.list_triggers(PageParams())
    assert [t.id for t in everything.items] == [unbound, to_agent, to_workflow]
    assert [len(t.bindings) for t in everything.items] == [0, 1, 1]
    assert {_os(t.config).source_name for t in everything.items} == {"bucket"}

    by_workflow = await service.list_triggers(
        PageParams(), workflow_id=WorkflowUUID.new(workflow.id)
    )
    assert [t.id for t in by_workflow.items] == [to_workflow]
    by_agent = await service.list_triggers(PageParams(), agent_preset_id=preset.id)
    assert [t.id for t in by_agent.items] == [to_agent]

    source_id = _os(by_agent.items[0].config).source_id
    by_source = await service.list_triggers(PageParams(), source_id=source_id)
    assert [t.id for t in by_source.items] == [to_agent]
    by_kind = await service.list_triggers(PageParams(), kind=TriggerKind.OBJECT_STORAGE)
    assert len(by_kind.items) == 3


async def test_rows_of_a_kind_this_version_does_not_know_are_skipped(
    service: TriggersService,
    session: AsyncSession,
    svc_workspace: Workspace,
    workflow: Workflow,
) -> None:
    """A newer version's kind (rolling deploy, rollback) never fails a read."""
    known = await _trigger(service)
    future = Trigger(
        id=uuid.uuid4(),
        workspace_id=svc_workspace.id,
        kind="future_kind",
        name="from a newer version",
        status=TriggerStatus.ONLINE.value,
    )
    session.add(future)
    await session.flush()
    binding = TriggerBinding(
        workspace_id=svc_workspace.id,
        trigger_id=future.id,
        target_type=TriggerTargetType.WORKFLOW.value,
        workflow_id=workflow.id,
        status=TriggerStatus.ONLINE.value,
    )
    session.add(binding)
    await session.commit()
    future_id, binding_id = future.id, binding.id

    page = await service.list_triggers(PageParams(limit=1))
    assert [t.id for t in page.items] == [known]
    assert not page.has_more
    by_workflow = await service.list_triggers(
        PageParams(), workflow_id=WorkflowUUID.new(workflow.id)
    )
    assert by_workflow.items == []

    workflow_target = WorkflowBindingTarget(
        type=TriggerTargetType.WORKFLOW, workflow_id=WorkflowUUID.new(workflow.id)
    )
    for call in (
        service.get_trigger(future_id),
        service.update_trigger(future_id, TriggerUpdate(name="x")),
        service.delete_trigger(future_id),
        service.create_binding(future_id, TriggerBindingCreate(target=workflow_target)),
        service.update_binding(
            future_id, binding_id, TriggerBindingUpdate(status=TriggerStatus.OFFLINE)
        ),
        service.delete_binding(future_id, binding_id),
    ):
        with pytest.raises(TriggerError) as exc:
            await call
        assert exc.value.code is TriggerErrorCode.NOT_FOUND

    # Left as the newer version wrote it.
    stored = await session.scalar(select(Trigger.name).where(Trigger.id == future_id))
    assert stored == "from a newer version"
    kept = await session.scalar(
        select(TriggerBinding.id).where(TriggerBinding.id == binding_id)
    )
    assert kept == binding_id


async def test_making_a_binding_live_needs_the_right_to_run_its_target(
    service: TriggersService,
    session: AsyncSession,
    svc_role: Role,
    workflow: Workflow,
    preset: AgentPreset,
) -> None:
    trigger_id = await _trigger(service)
    agent = await service.create_binding(
        trigger_id,
        TriggerBindingCreate(
            target=AgentBindingTarget(
                type=TriggerTargetType.AGENT, agent_preset_id=preset.id, instruction="i"
            ),
            status=TriggerStatus.OFFLINE,
        ),
    )
    # Configures triggers, may not run workflows or agents.
    configurer = TriggersService(
        session,
        svc_role.model_copy(
            update={
                "scopes": frozenset(
                    {"trigger:read", "trigger:create", "trigger:update"}
                )
            }
        ),
    )

    for target in (
        WorkflowBindingTarget(
            type=TriggerTargetType.WORKFLOW, workflow_id=WorkflowUUID.new(workflow.id)
        ),
        AgentBindingTarget(
            type=TriggerTargetType.AGENT, agent_preset_id=preset.id, instruction="i"
        ),
    ):
        with pytest.raises(ScopeDeniedError):
            await configurer.create_binding(
                trigger_id, TriggerBindingCreate(target=target)
            )
    for update in (
        TriggerBindingUpdate(status=TriggerStatus.ONLINE),
        TriggerBindingUpdate(instruction="Do something else"),
    ):
        with pytest.raises(ScopeDeniedError):
            await configurer.update_binding(trigger_id, agent.id, update)

    # Switching a binding off stays open to trigger:update alone.
    off = await configurer.update_binding(
        trigger_id, agent.id, TriggerBindingUpdate(status=TriggerStatus.OFFLINE)
    )
    assert off.status == TriggerStatus.OFFLINE
    assert off.instruction == "i"


async def test_turning_on_or_repointing_what_runs_a_target_needs_its_scope(
    service: TriggersService,
    session: AsyncSession,
    svc_role: Role,
    preset: AgentPreset,
) -> None:
    source = await service.create_event_source(
        EventSourceCreate(name="bucket", config=_sns(), status=TriggerStatus.ONLINE)
    )
    config = ObjectStorageTriggerConfig(
        kind=TriggerKind.OBJECT_STORAGE, source_id=source.id
    )
    trigger = await service.create_trigger(TriggerCreate(name="t", config=config))
    await service.create_binding(
        trigger.id,
        TriggerBindingCreate(
            target=AgentBindingTarget(
                type=TriggerTargetType.AGENT, agent_preset_id=preset.id, instruction="i"
            )
        ),
    )
    configurer = TriggersService(
        session,
        svc_role.model_copy(
            update={
                "scopes": frozenset(
                    {"trigger:read", "trigger:create", "trigger:update"}
                )
            }
        ),
    )

    # The trigger is offline with a live agent binding: turning it on runs the agent.
    with pytest.raises(ScopeDeniedError):
        await configurer.update_trigger(
            trigger.id, TriggerUpdate(status=TriggerStatus.ONLINE)
        )
    # Renaming needs nothing more.
    await configurer.update_trigger(trigger.id, TriggerUpdate(name="renamed"))

    await service.update_trigger(trigger.id, TriggerUpdate(status=TriggerStatus.ONLINE))
    # Changing which events an online trigger matches, or pointing its source at
    # another topic, decides what the agent runs on.
    with pytest.raises(ScopeDeniedError):
        await configurer.update_trigger(
            trigger.id,
            TriggerUpdate(config=config.model_copy(update={"key_prefix": "x/"})),
        )
    with pytest.raises(ScopeDeniedError):
        await configurer.update_event_source(
            source.id, EventSourceUpdate(config=_sns(f"{TOPIC}-attacker"))
        )
    await service.update_event_source(
        source.id, EventSourceUpdate(status=TriggerStatus.OFFLINE)
    )
    with pytest.raises(ScopeDeniedError):
        await configurer.update_event_source(
            source.id, EventSourceUpdate(status=TriggerStatus.ONLINE)
        )

    # Switching things off stays open to trigger:update alone.
    off = await configurer.update_trigger(
        trigger.id, TriggerUpdate(status=TriggerStatus.OFFLINE)
    )
    assert off.status == TriggerStatus.OFFLINE
    # With no online trigger on it, the source can be turned on and moved.
    moved = await configurer.update_event_source(
        source.id,
        EventSourceUpdate(config=_sns(f"{TOPIC}-2"), status=TriggerStatus.ONLINE),
    )
    assert moved.status == TriggerStatus.ONLINE


def test_long_topic_names_are_accepted() -> None:
    # AWS allows topic names up to 256 characters.
    prefix = "arn:aws-us-gov:sns:us-gov-west-1:123456789012:"
    assert AwsSnsSourceConfig(
        kind=EventSourceKind.AWS_SNS, topic_arn=prefix + "a" * 256
    )
    with pytest.raises(ValueError):
        AwsSnsSourceConfig(kind=EventSourceKind.AWS_SNS, topic_arn=prefix + "a" * 257)


# ----- Helpers -----


def test_occurrence_key_is_stable_and_distinguishes_versions() -> None:
    key = build_occurrence_key(bucket="b", key="k", version_id="v1", sequencer="s")

    assert len(key) == 64
    assert key == build_occurrence_key(
        bucket="b", key="k", version_id="v1", sequencer="s"
    )
    assert key != build_occurrence_key(
        bucket="b", key="k", version_id="v2", sequencer="s"
    )
    # Field boundaries are kept: ("ab", "c") is not ("a", "bc").
    assert build_occurrence_key(
        bucket="ab", key="c", version_id=None, sequencer=None
    ) != build_occurrence_key(bucket="a", key="bc", version_id=None, sequencer=None)


async def test_gated_writes_take_the_workspace_config_lock(
    service: TriggersService,
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writes whose scope check reads other rows serialize on one lock."""
    trigger_id = await _trigger(service)
    source_id = _os((await service.get_trigger(trigger_id)).config).source_id
    keys: list[int] = []
    real_lock = trigger_service.pg_advisory_xact_lock

    async def recording_lock(session: AsyncSession, key: int) -> None:
        keys.append(key)
        await real_lock(session, key)

    monkeypatch.setattr(trigger_service, "pg_advisory_xact_lock", recording_lock)

    binding = await service.create_binding(
        trigger_id,
        TriggerBindingCreate(
            target=WorkflowBindingTarget(
                type=TriggerTargetType.WORKFLOW,
                workflow_id=WorkflowUUID.new(workflow.id),
            )
        ),
    )
    await service.update_binding(
        trigger_id, binding.id, TriggerBindingUpdate(status=TriggerStatus.ONLINE)
    )
    await service.update_trigger(trigger_id, TriggerUpdate(status=TriggerStatus.ONLINE))
    await service.update_event_source(
        source_id, EventSourceUpdate(status=TriggerStatus.ONLINE)
    )

    assert len(keys) == 4
    assert len(set(keys)) == 1
