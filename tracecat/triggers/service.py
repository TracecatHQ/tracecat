"""Service for event sources, triggers and trigger bindings.

Every query filters on the caller's workspace, on top of RLS. Callers check
the ``object_triggers`` entitlement and route scopes before calling in.
"""

from __future__ import annotations

import secrets
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import check_scopes, has_scope
from tracecat.db.errors import is_unique_violation
from tracecat.db.locks import derive_lock_key_from_parts, pg_advisory_xact_lock
from tracecat.db.models import (
    AgentPreset,
    EventSource,
    ObjectStorageTrigger,
    Trigger,
    TriggerBinding,
    Workflow,
)
from tracecat.db.soft_delete import with_deleted
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.service import BaseWorkspaceService
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
    EventSourceConfig,
    EventSourceCreate,
    EventSourceRead,
    EventSourceUpdate,
    ObjectStorageTriggerConfig,
    ObjectStorageTriggerConfigRead,
    TriggerBindingCreate,
    TriggerBindingRead,
    TriggerBindingUpdate,
    TriggerConfig,
    TriggerConfigRead,
    TriggerCreate,
    TriggerRead,
    TriggerUpdate,
    WorkflowBindingTarget,
)
from tracecat.triggers.types import (
    EXECUTE_SCOPE_BY_TARGET,
    SOURCE_KINDS_BY_TRIGGER_KIND,
    event_source_endpoint_url,
)

_UQ_ENDPOINT_TOKEN = "uq_event_source_endpoint_token"
_UQ_SNS_TOPIC = "uq_event_source_aws_sns_topic"
_UQ_BINDING_WORKFLOW = "uq_trigger_binding_trigger_id_workflow_id"
_UQ_BINDING_AGENT = "uq_trigger_binding_trigger_id_agent_preset_id"


def _not_found(what: str) -> TriggerError:
    return TriggerError(TriggerErrorCode.NOT_FOUND, f"{what} not found")


# ----- Per-kind mapping between API configs and stored columns -----


def _source_config(source: EventSource) -> EventSourceConfig:
    match EventSourceKind(source.kind):
        case EventSourceKind.AWS_SNS:
            return AwsSnsSourceConfig.model_validate(
                {**source.config, "kind": source.kind}
            )


def _source_identity(config: EventSourceConfig) -> tuple[str, ...]:
    """What identifies where a source's events come from."""
    match config:
        case AwsSnsSourceConfig(topic_arn=topic_arn):
            return (topic_arn,)


def read_event_source(source: EventSource, role: Role) -> EventSourceRead:
    """Build the API view of a source.

    The endpoint URL goes only to callers who may change the source: anyone
    holding it can reach the ingest endpoint, and it is never rotated.
    """
    kind = EventSourceKind(source.kind)
    can_manage = has_scope(role.scopes or frozenset(), "trigger:update")
    return EventSourceRead(
        id=source.id,
        kind=kind,
        name=source.name,
        config=_source_config(source),
        status=TriggerStatus(source.status),
        subscription_status=SubscriptionStatus(source.subscription_status),
        subscription_ref=source.subscription_ref,
        endpoint_url=(
            event_source_endpoint_url(kind, source.endpoint_token)
            if can_manage
            else None
        ),
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


class TriggersService(BaseWorkspaceService):
    """Manage a workspace's event sources, triggers and bindings."""

    service_name = "triggers"

    # ----- Event sources -----

    async def list_event_sources(
        self, page: PageParams
    ) -> CursorPaginatedResponse[EventSourceRead]:
        """List event sources, newest first."""
        statement = select(EventSource).where(
            EventSource.workspace_id == self.workspace_id
        )
        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(EventSource.created_at.desc(), EventSource.surrogate_id.desc()),
        )
        return CursorPaginatedResponse(
            items=[read_event_source(source, self.role) for source in result.items],
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
        )

    async def get_event_source(self, source_id: uuid.UUID) -> EventSource:
        """Get a source of the workspace.

        Raises:
            TriggerError: ``not_found`` if the workspace has no such source.
        """
        source = await self.session.scalar(
            select(EventSource).where(
                EventSource.workspace_id == self.workspace_id,
                EventSource.id == source_id,
            )
        )
        if source is None:
            raise _not_found("Event source")
        return source

    @audit_log(resource_type="event_source", action="create")
    async def create_event_source(self, params: EventSourceCreate) -> EventSource:
        """Create a source with a new endpoint token.

        Raises:
            TriggerError: ``conflict`` if the workspace already has a source for
                the same feed (for ``aws_sns``, the same topic).
        """
        source = EventSource(
            workspace_id=self.workspace_id,
            kind=params.config.kind.value,
            name=params.name,
            config=params.config.model_dump(mode="json", exclude={"kind"}),
            status=params.status.value,
            subscription_status=SubscriptionStatus.PENDING.value,
            # 256 random bits. The token routes ingest and is never rotated,
            # since the customer's subscription is bound to the URL.
            endpoint_token=secrets.token_urlsafe(32),
        )
        self.session.add(source)
        await self._commit_source()
        await self.session.refresh(source)
        return source

    @audit_log(
        resource_type="event_source", action="update", resource_id_attr="source_id"
    )
    async def update_event_source(
        self, source_id: uuid.UUID, params: EventSourceUpdate
    ) -> EventSource:
        """Rename a source, change its feed, or take it online or offline.

        Changing where events come from resets the subscription: the new feed
        has to confirm again. Turning an online source's feed on or pointing it
        elsewhere changes what the online triggers on it run, so it needs the
        right to run their online bindings' targets.

        Raises:
            ScopeDeniedError: If the change needs the right to run a bound
                target and the caller lacks it.
            TriggerError: ``not_found``, ``invalid`` if the config changes
                kind, or ``conflict`` as for create.
        """
        await self._lock_config()
        source = await self.get_event_source(source_id)
        moves_feed = params.config is not None and _source_identity(
            params.config
        ) != _source_identity(_source_config(source))
        if _makes_live(source.status, params.status, changed=moves_feed):
            await self._check_can_run_bound(
                select(ObjectStorageTrigger.trigger_id)
                .join(
                    Trigger,
                    (Trigger.id == ObjectStorageTrigger.trigger_id)
                    & (Trigger.workspace_id == ObjectStorageTrigger.workspace_id),
                )
                .where(
                    ObjectStorageTrigger.workspace_id == self.workspace_id,
                    ObjectStorageTrigger.source_id == source.id,
                    Trigger.status == TriggerStatus.ONLINE.value,
                )
            )
        if params.name is not None:
            source.name = params.name
        if params.status is not None:
            source.status = params.status.value
        if params.config is not None:
            if params.config.kind != source.kind:
                raise TriggerError(
                    TriggerErrorCode.INVALID, "A source's kind cannot change"
                )
            if moves_feed:
                source.subscription_status = SubscriptionStatus.PENDING.value
                source.subscription_ref = None
            source.config = params.config.model_dump(mode="json", exclude={"kind"})
        await self._commit_source()
        await self.session.refresh(source)
        return source

    async def _commit_source(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            if is_unique_violation(e, _UQ_SNS_TOPIC):
                raise TriggerError(
                    TriggerErrorCode.CONFLICT,
                    "This workspace already has a source for that topic",
                ) from e
            if is_unique_violation(e, _UQ_ENDPOINT_TOKEN):
                # 256 random bits: not reachable in practice.
                raise TriggerError(
                    TriggerErrorCode.CONFLICT, "Endpoint token collision, retry"
                ) from e
            raise

    # ----- Triggers -----

    async def list_triggers(
        self,
        page: PageParams,
        *,
        kind: TriggerKind | None = None,
        source_id: uuid.UUID | None = None,
        workflow_id: WorkflowUUID | None = None,
        agent_preset_id: uuid.UUID | None = None,
    ) -> CursorPaginatedResponse[TriggerRead]:
        """List triggers newest first, each with its config and bindings.

        ``source_id`` keeps triggers that read that source. ``workflow_id`` and
        ``agent_preset_id`` keep triggers with a binding to that target; every
        binding of a kept trigger is still returned. Besides the page itself,
        one query reads the page's bindings and one per kind reads settings.
        """
        statement = select(Trigger).where(Trigger.workspace_id == self.workspace_id)
        if kind is not None:
            statement = statement.where(Trigger.kind == kind.value)
        if source_id is not None:
            statement = statement.where(
                exists().where(
                    ObjectStorageTrigger.workspace_id == Trigger.workspace_id,
                    ObjectStorageTrigger.trigger_id == Trigger.id,
                    ObjectStorageTrigger.source_id == source_id,
                )
            )
        if workflow_id is not None:
            statement = statement.where(
                _has_binding(TriggerBinding.workflow_id == workflow_id)
            )
        if agent_preset_id is not None:
            statement = statement.where(
                _has_binding(TriggerBinding.agent_preset_id == agent_preset_id)
            )
        result = await paginate(
            self.session,
            statement,
            page=page,
            order_by=(Trigger.created_at.desc(), Trigger.surrogate_id.desc()),
        )
        return CursorPaginatedResponse(
            items=await self._read_triggers(result.items),
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
        )

    async def get_trigger(self, trigger_id: uuid.UUID) -> TriggerRead:
        """Get a trigger with its config and bindings.

        Raises:
            TriggerError: ``not_found`` if the workspace has no such trigger.
        """
        (item,) = await self._read_triggers([await self._get_trigger_row(trigger_id)])
        return item

    @audit_log(resource_type="trigger", action="create")
    async def create_trigger(self, params: TriggerCreate) -> Trigger:
        """Create a trigger and its kind's settings in one transaction.

        Raises:
            TriggerError: ``not_found`` if the source is not in the workspace,
                ``invalid`` if the source's kind cannot feed this trigger kind.
        """
        trigger = Trigger(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            kind=params.config.kind.value,
            name=params.name,
            status=params.status.value,
        )
        try:
            self.session.add(trigger)
            # The settings row's foreign key needs the header first.
            await self.session.flush()
            await self._write_config(trigger, params.config)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        await self.session.refresh(trigger)
        return trigger

    @audit_log(resource_type="trigger", action="update", resource_id_attr="trigger_id")
    async def update_trigger(
        self, trigger_id: uuid.UUID, params: TriggerUpdate
    ) -> Trigger:
        """Rename a trigger, replace its config, or take it online or offline.

        Turning a trigger online, or changing which events an online trigger
        matches, needs the right to run its online bindings' targets, as
        making a binding live does.

        Raises:
            ScopeDeniedError: If the change needs the right to run a bound
                target and the caller lacks it.
            TriggerError: ``not_found``, or ``invalid`` if the config is of
                another kind or names a source that cannot feed it.
        """
        await self._lock_config()
        trigger = await self._get_trigger_row(trigger_id)
        if params.config is not None and params.config.kind != trigger.kind:
            raise TriggerError(
                TriggerErrorCode.INVALID, "A trigger's kind cannot change"
            )
        if _makes_live(
            trigger.status, params.status, changed=params.config is not None
        ):
            await self._check_can_run_bound([trigger.id])
        if params.name is not None:
            trigger.name = params.name
        if params.status is not None:
            trigger.status = params.status.value
        try:
            if params.config is not None:
                await self._write_config(trigger, params.config)
                # The header's timestamp covers its settings too.
                trigger.updated_at = func.now()
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        await self.session.refresh(trigger)
        return trigger

    @audit_log(resource_type="trigger", action="delete", resource_id_attr="trigger_id")
    async def delete_trigger(self, trigger_id: uuid.UUID) -> None:
        """Delete a trigger with its settings and bindings.

        Raises:
            TriggerError: ``not_found`` if the workspace has no such trigger.
        """
        deleted = await self.session.scalar(
            delete(Trigger)
            .where(Trigger.workspace_id == self.workspace_id, Trigger.id == trigger_id)
            .returning(Trigger.id)
        )
        if deleted is None:
            raise _not_found("Trigger")
        await self.session.commit()

    async def _write_config(self, trigger: Trigger, config: TriggerConfig) -> None:
        """Insert or replace the kind's settings row of ``trigger``."""
        match config:
            case ObjectStorageTriggerConfig(source_id=source_id, key_prefix=key_prefix):
                await self._check_source(TriggerKind.OBJECT_STORAGE, source_id)
                values = {"source_id": source_id, "key_prefix": key_prefix}
                await self.session.execute(
                    insert(ObjectStorageTrigger)
                    .values(
                        trigger_id=trigger.id,
                        workspace_id=trigger.workspace_id,
                        **values,
                    )
                    .on_conflict_do_update(
                        index_elements=[ObjectStorageTrigger.trigger_id], set_=values
                    )
                )

    async def _read_configs(
        self, triggers: Sequence[Trigger]
    ) -> dict[uuid.UUID, TriggerConfigRead]:
        """Read the settings of ``triggers``: one query per kind present."""
        ids_by_kind: defaultdict[TriggerKind, list[uuid.UUID]] = defaultdict(list)
        for trigger in triggers:
            ids_by_kind[TriggerKind(trigger.kind)].append(trigger.id)
        configs: dict[uuid.UUID, TriggerConfigRead] = {}
        for kind, trigger_ids in ids_by_kind.items():
            match kind:
                case TriggerKind.OBJECT_STORAGE:
                    rows = await self.session.execute(
                        select(
                            ObjectStorageTrigger.trigger_id,
                            ObjectStorageTrigger.source_id,
                            ObjectStorageTrigger.key_prefix,
                            EventSource.name,
                        )
                        .join(
                            EventSource,
                            (EventSource.id == ObjectStorageTrigger.source_id)
                            & (
                                EventSource.workspace_id
                                == ObjectStorageTrigger.workspace_id
                            ),
                        )
                        .where(
                            ObjectStorageTrigger.workspace_id == self.workspace_id,
                            ObjectStorageTrigger.trigger_id.in_(trigger_ids),
                        )
                    )
                    for trigger_id, source_id, key_prefix, source_name in rows.tuples():
                        configs[trigger_id] = ObjectStorageTriggerConfigRead(
                            kind=TriggerKind.OBJECT_STORAGE,
                            source_id=source_id,
                            key_prefix=key_prefix,
                            source_name=source_name,
                        )
        return configs

    # ----- Bindings -----

    @audit_log(resource_type="trigger_binding", action="create")
    async def create_binding(
        self, trigger_id: uuid.UUID, params: TriggerBindingCreate
    ) -> TriggerBinding:
        """Bind a trigger to a workflow or agent preset of this workspace.

        Raises:
            ScopeDeniedError: If the caller may not run the target.
            TriggerError: ``not_found`` if the trigger or target is not in the
                workspace, ``conflict`` if the trigger already has that target.
        """
        _check_can_run(self.role, params.target.type)
        await self._lock_config()
        trigger = await self._get_trigger_row(trigger_id)
        binding = TriggerBinding(
            workspace_id=self.workspace_id,
            trigger_id=trigger.id,
            status=params.status.value,
        )
        match params.target:
            case WorkflowBindingTarget(workflow_id=workflow_id):
                binding.target_type = TriggerTargetType.WORKFLOW.value
                binding.workflow_id = await self._workflow_target(workflow_id)
            case AgentBindingTarget(
                agent_preset_id=agent_preset_id, instruction=instruction
            ):
                binding.target_type = TriggerTargetType.AGENT.value
                binding.agent_preset_id = await self._agent_target(agent_preset_id)
                binding.instruction = instruction
        self.session.add(binding)
        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            if is_unique_violation(e, _UQ_BINDING_WORKFLOW, _UQ_BINDING_AGENT):
                raise TriggerError(
                    TriggerErrorCode.CONFLICT,
                    "The trigger is already bound to that target",
                ) from e
            raise
        await self.session.refresh(binding)
        return binding

    @audit_log(
        resource_type="trigger_binding", action="update", resource_id_attr="binding_id"
    )
    async def update_binding(
        self,
        trigger_id: uuid.UUID,
        binding_id: uuid.UUID,
        params: TriggerBindingUpdate,
    ) -> TriggerBinding:
        """Take a binding online or offline, or change an agent's instruction.

        Taking a binding offline needs only ``trigger:update``. Turning it on
        or changing what the agent is told needs the right to run the target,
        as creating it does.

        Raises:
            ScopeDeniedError: If the update needs the right to run the target
                and the caller lacks it.
            TriggerError: ``not_found``, or ``invalid`` for an instruction on a
                workflow binding.
        """
        await self._lock_config()
        binding = await self._get_binding_row(trigger_id, binding_id)
        if params.status == TriggerStatus.ONLINE or params.instruction is not None:
            _check_can_run(self.role, TriggerTargetType(binding.target_type))
        if params.status is not None:
            binding.status = params.status.value
        if params.instruction is not None:
            if binding.target_type != TriggerTargetType.AGENT:
                raise TriggerError(
                    TriggerErrorCode.INVALID, "Only agent bindings have an instruction"
                )
            binding.instruction = params.instruction
        await self.session.commit()
        await self.session.refresh(binding)
        return binding

    @audit_log(
        resource_type="trigger_binding", action="delete", resource_id_attr="binding_id"
    )
    async def delete_binding(
        self, trigger_id: uuid.UUID, binding_id: uuid.UUID
    ) -> None:
        """Delete one binding of a trigger.

        Raises:
            TriggerError: ``not_found`` if the trigger has no such binding.
        """
        deleted = await self.session.scalar(
            delete(TriggerBinding)
            .where(
                TriggerBinding.workspace_id == self.workspace_id,
                TriggerBinding.trigger_id == trigger_id,
                TriggerBinding.id == binding_id,
            )
            .returning(TriggerBinding.id)
        )
        if deleted is None:
            raise _not_found("Binding")
        await self.session.commit()

    async def read_binding(self, binding: TriggerBinding) -> TriggerBindingRead:
        """Build the API view of one binding."""
        (item,) = await self._read_bindings(
            select_bindings().where(
                TriggerBinding.workspace_id == self.workspace_id,
                TriggerBinding.trigger_id == binding.trigger_id,
                TriggerBinding.id == binding.id,
            )
        )
        return item

    # ----- Helpers -----

    async def _lock_config(self) -> None:
        """Serialize this workspace's trigger config writes until commit.

        Whether a write needs the execute scope depends on the state of the
        source, trigger and bindings behind it. Every write that checks or
        changes that state takes this lock before reading, so the check never
        runs against a state another request is about to change.
        """
        await pg_advisory_xact_lock(
            self.session,
            derive_lock_key_from_parts(
                "tracecat.triggers.config", str(self.workspace_id)
            ),
        )

    async def _check_can_run_bound(
        self, trigger_ids: Select[Any] | Sequence[uuid.UUID]
    ) -> None:
        """Require the right to run every target online-bound to these triggers."""
        target_types = await self.session.scalars(
            select(TriggerBinding.target_type)
            .where(
                TriggerBinding.workspace_id == self.workspace_id,
                TriggerBinding.trigger_id.in_(trigger_ids),
                TriggerBinding.status == TriggerStatus.ONLINE.value,
            )
            .distinct()
        )
        for target_type in target_types:
            _check_can_run(self.role, TriggerTargetType(target_type))

    async def _read_triggers(self, triggers: Sequence[Trigger]) -> list[TriggerRead]:
        if not triggers:
            return []
        trigger_ids = [trigger.id for trigger in triggers]
        configs = await self._read_configs(triggers)
        bindings = await self._read_bindings(
            select_bindings().where(
                TriggerBinding.workspace_id == self.workspace_id,
                TriggerBinding.trigger_id.in_(trigger_ids),
            )
        )
        by_trigger: dict[uuid.UUID, list[TriggerBindingRead]] = {
            trigger_id: [] for trigger_id in trigger_ids
        }
        for binding in bindings:
            by_trigger[binding.trigger_id].append(binding)
        items: list[TriggerRead] = []
        for trigger in triggers:
            if (config := configs.get(trigger.id)) is None:
                # The service writes header and settings together, so this is
                # a broken row, not a user error.
                raise RuntimeError(
                    f"Trigger {trigger.id} has no {trigger.kind} settings"
                )
            items.append(
                TriggerRead(
                    id=trigger.id,
                    kind=TriggerKind(trigger.kind),
                    name=trigger.name,
                    status=TriggerStatus(trigger.status),
                    config=config,
                    bindings=by_trigger[trigger.id],
                    created_at=trigger.created_at,
                    updated_at=trigger.updated_at,
                )
            )
        return items

    async def _read_bindings(self, statement: Select[Any]) -> list[TriggerBindingRead]:
        result = await self.session.execute(
            # A soft-deleted preset still names its binding until part 4 skips it.
            with_deleted(
                statement.order_by(
                    TriggerBinding.created_at, TriggerBinding.surrogate_id
                )
            )
        )
        return [
            TriggerBindingRead(
                id=binding.id,
                trigger_id=binding.trigger_id,
                target_type=TriggerTargetType(binding.target_type),
                target_id=target_id,
                target_name=target_name,
                instruction=binding.instruction,
                status=TriggerStatus(binding.status),
                created_at=binding.created_at,
                updated_at=binding.updated_at,
            )
            for binding, target_id, target_name in result.tuples()
        ]

    async def _get_trigger_row(self, trigger_id: uuid.UUID) -> Trigger:
        trigger = await self.session.scalar(
            select(Trigger).where(
                Trigger.workspace_id == self.workspace_id, Trigger.id == trigger_id
            )
        )
        if trigger is None:
            raise _not_found("Trigger")
        return trigger

    async def _get_binding_row(
        self, trigger_id: uuid.UUID, binding_id: uuid.UUID
    ) -> TriggerBinding:
        binding = await self.session.scalar(
            select(TriggerBinding).where(
                TriggerBinding.workspace_id == self.workspace_id,
                TriggerBinding.trigger_id == trigger_id,
                TriggerBinding.id == binding_id,
            )
        )
        if binding is None:
            raise _not_found("Binding")
        return binding

    async def _check_source(
        self, kind: TriggerKind, source_id: uuid.UUID | None
    ) -> None:
        """Check the source is in this workspace and can feed ``kind``."""
        allowed = SOURCE_KINDS_BY_TRIGGER_KIND.get(kind, frozenset())
        if source_id is None:
            if allowed:
                raise TriggerError(
                    TriggerErrorCode.INVALID, f"A {kind.value} trigger needs a source"
                )
            return
        source = await self.get_event_source(source_id)
        if source.kind not in allowed:
            raise TriggerError(
                TriggerErrorCode.INVALID,
                f"A {source.kind} source cannot feed a {kind.value} trigger",
            )

    async def _workflow_target(self, workflow_id: AnyWorkflowID) -> uuid.UUID:
        workflow_uuid = WorkflowUUID.new(workflow_id)
        found = await self.session.scalar(
            select(Workflow.id).where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_uuid,
            )
        )
        if found is None:
            raise _not_found("Workflow")
        return found

    async def _agent_target(self, agent_preset_id: uuid.UUID) -> uuid.UUID:
        # Soft-deleted presets are filtered out by default.
        found = await self.session.scalar(
            select(AgentPreset.id).where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.id == agent_preset_id,
            )
        )
        if found is None:
            raise _not_found("Agent preset")
        return found


def _makes_live(
    current: str, requested: TriggerStatus | None, *, changed: bool
) -> bool:
    """Whether an update turns a row on, or changes what an online row matches."""
    will_be_online = (requested or TriggerStatus(current)) == TriggerStatus.ONLINE
    turned_on = requested == TriggerStatus.ONLINE and current != TriggerStatus.ONLINE
    return will_be_online and (turned_on or changed)


def _check_can_run(role: Role, target_type: TriggerTargetType) -> None:
    """Binding a target means every matching event runs it, so a caller who
    makes a binding live needs the right to run its target."""
    check_scopes(role, EXECUTE_SCOPE_BY_TARGET[target_type])


def _has_binding(target: ColumnElement[bool]) -> ColumnElement[bool]:
    return exists().where(
        TriggerBinding.workspace_id == Trigger.workspace_id,
        TriggerBinding.trigger_id == Trigger.id,
        target,
    )


def select_bindings() -> Select[Any]:
    """Select bindings with their target's ID and display name."""
    return (
        select(
            TriggerBinding,
            func.coalesce(TriggerBinding.workflow_id, TriggerBinding.agent_preset_id),
            func.coalesce(Workflow.title, AgentPreset.name),
        )
        .outerjoin(
            Workflow,
            (Workflow.id == TriggerBinding.workflow_id)
            & (Workflow.workspace_id == TriggerBinding.workspace_id),
        )
        .outerjoin(
            AgentPreset,
            (AgentPreset.id == TriggerBinding.agent_preset_id)
            & (AgentPreset.workspace_id == TriggerBinding.workspace_id),
        )
    )
