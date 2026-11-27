"""Service for event sources, triggers and trigger bindings.

Every query filters on the caller's workspace, on top of RLS. Callers check
route scopes before calling in; writes check their trigger kind's
entitlement here (``ENTITLEMENT_BY_TRIGGER_KIND``).

A ``schedule`` trigger is a workflow schedule: its settings are the
``schedule`` row, which ``WorkflowSchedulesService`` writes with the trigger
row and its one workflow binding. Until schedules start their targets
through bindings, that binding cannot be added to, changed or removed.

A ``webhook`` trigger is a workflow's webhook: its settings are the
``webhook`` row, which ``tracecat.webhooks.service`` writes with the trigger
row and binding. Its binding is fixed the same way.
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
    Schedule,
    Trigger,
    TriggerBinding,
    Webhook,
    Workflow,
)
from tracecat.db.soft_delete import with_deleted
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers.schedules import ScheduleUUID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.enums import Entitlement
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
    LockedTriggerCount,
    ObjectStorageTriggerConfig,
    ObjectStorageTriggerConfigRead,
    ScheduleTriggerConfig,
    ScheduleTriggerConfigRead,
    TriggerBindingCreate,
    TriggerBindingRead,
    TriggerBindingUpdate,
    TriggerConfig,
    TriggerConfigRead,
    TriggerCreate,
    TriggerListResponse,
    TriggerRead,
    TriggerUpdate,
    WebhookTriggerConfig,
    WebhookTriggerConfigRead,
    WorkflowBindingTarget,
)
from tracecat.triggers.types import (
    ENTITLEMENT_BY_TRIGGER_KIND,
    EXECUTE_SCOPE_BY_TARGET,
    SOURCE_KINDS_BY_TRIGGER_KIND,
    event_source_endpoint_url,
)
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookApiKeyRead, WebhookUpdate
from tracecat.workflow.schedules.schemas import ScheduleCreate, ScheduleUpdate
from tracecat.workflow.schedules.service import WorkflowSchedulesService

_UQ_ENDPOINT_TOKEN = "uq_event_source_endpoint_token"
_UQ_SNS_TOPIC = "uq_event_source_aws_sns_topic"
_UQ_BINDING_WORKFLOW = "uq_trigger_binding_trigger_id_workflow_id"
_UQ_BINDING_AGENT = "uq_trigger_binding_trigger_id_agent_preset_id"


# A newer version may have written rows of a kind this one does not know
# (during a rolling deploy, or after an app-only rollback). Every read of the
# ``trigger`` table keeps to these kinds, so such rows are left out of lists
# and answer not found, instead of failing in ``TriggerKind(...)``.
_KNOWN_KINDS = tuple(kind.value for kind in TriggerKind)


def _not_found(what: str) -> TriggerError:
    return TriggerError(TriggerErrorCode.NOT_FOUND, f"{what} not found")


# Until schedules start their targets through bindings, the Temporal schedule
# runs the schedule's workflow and ignores every binding.
_SCHEDULE_TARGET_FIXED = (
    "A schedule trigger runs its schedule's workflow only, so its target"
    " cannot be added, changed or removed. Create another schedule for"
    " another workflow."
)
_WEBHOOK_TARGET_FIXED = (
    "A webhook trigger runs its own workflow only, so its target cannot be"
    " added, changed or removed. Use the webhook of the other workflow"
    " instead."
)
_FIXED_TARGET_BY_KIND: dict[TriggerKind, str] = {
    TriggerKind.SCHEDULE: _SCHEDULE_TARGET_FIXED,
    TriggerKind.WEBHOOK: _WEBHOOK_TARGET_FIXED,
}

# The scope a kind's settings need to be read, on top of ``trigger:read``: the
# same as on the kind's own routes. A webhook's URL holds its secret, and
# ``workflow:read`` is what shows it on the workflow page.
_READ_SCOPE_BY_KIND: dict[TriggerKind, str] = {
    TriggerKind.SCHEDULE: "schedule:read",
    TriggerKind.WEBHOOK: "workflow:read",
}


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

    The endpoint URL and the subscription ref go only to callers who may
    change the source: anyone holding the URL can reach the ingest endpoint
    (it is never rotated), and an SNS subscription ARN is enough to
    unsubscribe a subscription confirmed without authentication.
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
        subscription_ref=source.subscription_ref if can_manage else None,
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
    ) -> TriggerListResponse:
        """List triggers newest first, each with its config and bindings.

        Kinds the caller may not read (``_check_kind_readable``) and kinds
        this version does not know are left out. Those left out because the
        plan lacks their feature are counted in ``locked``, so the tab can say
        they exist. ``source_id`` keeps triggers that read that source.
        ``workflow_id`` and ``agent_preset_id`` keep triggers with a binding
        to that target; every binding of a kept trigger is still returned.
        Besides the page itself, one query reads the page's bindings, one per
        kind reads settings, and one counts locked kinds when there are any.
        """
        readable: list[str] = []
        locked: dict[str, Entitlement] = {}
        for each in TriggerKind:
            entitlement = ENTITLEMENT_BY_TRIGGER_KIND[each]
            if entitlement is not None and not await self.has_entitlement(entitlement):
                locked[each.value] = entitlement
            elif await self._can_read(each):
                readable.append(each.value)
        filters: list[ColumnElement[bool]] = [Trigger.workspace_id == self.workspace_id]
        if kind is not None:
            filters.append(Trigger.kind == kind.value)
        if source_id is not None:
            filters.append(
                exists().where(
                    ObjectStorageTrigger.workspace_id == Trigger.workspace_id,
                    ObjectStorageTrigger.trigger_id == Trigger.id,
                    ObjectStorageTrigger.source_id == source_id,
                )
            )
        if workflow_id is not None:
            filters.append(_has_binding(TriggerBinding.workflow_id == workflow_id))
        if agent_preset_id is not None:
            filters.append(
                _has_binding(TriggerBinding.agent_preset_id == agent_preset_id)
            )
        result = await paginate(
            self.session,
            select(Trigger).where(*filters, Trigger.kind.in_(readable)),
            page=page,
            order_by=(Trigger.created_at.desc(), Trigger.surrogate_id.desc()),
        )
        counts: list[LockedTriggerCount] = []
        if locked:
            rows = await self.session.execute(
                select(Trigger.kind, func.count())
                .where(*filters, Trigger.kind.in_(locked))
                .group_by(Trigger.kind)
                .order_by(Trigger.kind)
            )
            counts = [
                LockedTriggerCount(
                    kind=TriggerKind(locked_kind),
                    entitlement=locked[locked_kind],
                    count=count,
                )
                for locked_kind, count in rows.tuples()
            ]
        return TriggerListResponse(
            items=await self._read_triggers(result.items),
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
            locked=counts,
        )

    async def get_trigger(self, trigger_id: uuid.UUID) -> TriggerRead:
        """Get a trigger with its config and bindings.

        Raises:
            EntitlementRequired: If the plan lacks the kind's feature.
            ScopeDeniedError: Without the kind's read scope
                (``_READ_SCOPE_BY_KIND``).
            TriggerError: ``not_found`` if the workspace has no such trigger.
        """
        trigger = await self._get_trigger_row(trigger_id)
        await self._check_kind_readable(TriggerKind(trigger.kind))
        (item,) = await self._read_triggers([trigger])
        return item

    @audit_log(resource_type="trigger", action="create")
    async def create_trigger(self, params: TriggerCreate) -> Trigger:
        """Create a trigger and its kind's settings in one transaction.

        A ``schedule`` trigger is created with its schedule and bound to the
        schedule's workflow; see ``_create_schedule_trigger``. A ``webhook``
        trigger is the workflow's existing webhook, given these settings and
        bound to the workflow; see ``_create_webhook_trigger``.

        Raises:
            EntitlementRequired: If the plan lacks the kind's feature.
            ScopeDeniedError: For a schedule, without ``schedule:create`` or
                ``workflow:execute``; for a webhook, without
                ``workflow:update`` or ``workflow:execute``.
            TriggerError: ``not_found`` if the source or workflow is not in
                the workspace, ``invalid`` if the source's kind cannot feed
                this trigger kind, ``conflict`` if the workflow was never
                saved (schedule) or its webhook already is a trigger.
        """
        # Checked up front: the routes answer with the read view, and a write
        # that commits and then fails to read back would look like a failure.
        await self._check_kind_readable(params.config.kind)
        if isinstance(params.config, ScheduleTriggerConfig):
            return await self._create_schedule_trigger(params, params.config)
        if isinstance(params.config, WebhookTriggerConfig):
            return await self._create_webhook_trigger(params, params.config)
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

        For a ``schedule`` trigger the config and status go to its schedule
        (as ``POST /schedules/{id}`` does), which needs ``schedule:update``.
        For a ``webhook`` trigger they go to its webhook (as
        ``PATCH /workflows/{id}/webhook`` does), which needs
        ``workflow:update``.

        Raises:
            EntitlementRequired: If the plan lacks the kind's feature.
            ScopeDeniedError: If the change needs the right to run a bound
                target and the caller lacks it.
            TriggerError: ``not_found``, or ``invalid`` if the config is of
                another kind, names a source that cannot feed it, or moves a
                schedule or webhook to another workflow.
        """
        await self._lock_config()
        trigger = await self._get_trigger_row(trigger_id)
        await self._check_kind_readable(TriggerKind(trigger.kind))
        if params.config is not None and params.config.kind != trigger.kind:
            raise TriggerError(
                TriggerErrorCode.INVALID, "A trigger's kind cannot change"
            )
        if _makes_live(
            trigger.status, params.status, changed=params.config is not None
        ):
            await self._check_can_run_bound([trigger.id])
        try:
            if trigger.kind == TriggerKind.SCHEDULE:
                # The schedule service writes the name, status and settings
                # and commits. It locks the workflow before the trigger row,
                # the same order as /schedules, so the header must not be
                # dirtied (and flushed) here first.
                await self._update_schedule_trigger(trigger, params)
                # Committed: nothing after this may fail and report the
                # change as failed. Callers read the trigger back themselves.
                return trigger
            if trigger.kind == TriggerKind.WEBHOOK:
                # As for schedules: the webhook service locks the workflow
                # and webhook before it writes the trigger row, and commits.
                await self._update_webhook_trigger(trigger, params)
                return trigger
            if params.name is not None:
                trigger.name = params.name
            if params.status is not None:
                trigger.status = params.status.value
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

        Deleting a ``schedule`` trigger deletes its schedule, Temporal
        schedule included, and needs ``schedule:delete``. Deleting a
        ``webhook`` trigger turns the webhook offline and keeps it (every
        workflow has one), and needs ``workflow:update``.

        Raises:
            EntitlementRequired: If the plan lacks the kind's feature.
            TriggerError: ``not_found`` if the workspace has no such trigger.
        """
        trigger = await self._get_trigger_row(trigger_id)
        await self._check_kind_entitled(TriggerKind(trigger.kind))
        if trigger.kind == TriggerKind.SCHEDULE:
            schedules = WorkflowSchedulesService(self.session, self.role)
            try:
                # Commits. The schedule's database trigger deletes the
                # trigger row.
                await schedules.delete_trigger_schedule(trigger.id)
            except TracecatNotFoundError as e:
                await self.session.rollback()
                raise _not_found("Trigger") from e
            except BaseException:
                await self.session.rollback()
                raise
            return
        if trigger.kind == TriggerKind.WEBHOOK:
            try:
                # Commits. Turns the webhook offline and deletes the trigger
                # row; the webhook stays.
                await webhook_service.delete_trigger_webhook(
                    role=self.role, session=self.session, trigger_id=trigger.id
                )
            except TracecatNotFoundError as e:
                await self.session.rollback()
                raise _not_found("Trigger") from e
            except BaseException:
                await self.session.rollback()
                raise
            return
        deleted = await self.session.scalar(
            delete(Trigger)
            .where(Trigger.workspace_id == self.workspace_id, Trigger.id == trigger_id)
            .returning(Trigger.id)
        )
        if deleted is None:
            raise _not_found("Trigger")
        await self.session.commit()

    async def _create_schedule_trigger(
        self, params: TriggerCreate, config: ScheduleTriggerConfig
    ) -> Trigger:
        """Create a schedule, its trigger row and its workflow binding.

        The schedule service writes all three. Creating the schedule needs
        ``schedule:create``, as ``POST /schedules`` does; the binding makes
        the schedule run the workflow, so it also needs ``workflow:execute``.
        """
        _check_can_run(self.role, TriggerTargetType.WORKFLOW)
        schedules = WorkflowSchedulesService(self.session, self.role)
        try:
            schedule = await schedules.create_trigger_schedule(
                ScheduleCreate(
                    workflow_id=config.workflow_id,
                    status=params.status.value,
                    **_schedule_settings(config),
                ),
                trigger_name=params.name,
            )
        except TracecatNotFoundError as e:
            await self.session.rollback()
            raise _not_found("Workflow") from e
        except TracecatConflictError as e:
            await self.session.rollback()
            raise TriggerError(TriggerErrorCode.CONFLICT, str(e)) from e
        except BaseException:
            await self.session.rollback()
            raise
        if schedule.trigger_id is None:
            raise RuntimeError(f"Schedule {schedule.id} was created without a trigger")
        return await self._get_trigger_row(schedule.trigger_id)

    async def _update_schedule_trigger(
        self, trigger: Trigger, params: TriggerUpdate
    ) -> None:
        """Send a trigger update to its schedule, which commits it.

        A config replaces every schedule setting; its workflow must stay the
        schedule's. The schedule service also writes the name and status to
        the trigger row.
        """
        update: dict[str, Any] = {}
        if isinstance(params.config, ScheduleTriggerConfig):
            workflow_id = await self.session.scalar(
                select(Schedule.workflow_id).where(
                    Schedule.workspace_id == self.workspace_id,
                    Schedule.trigger_id == trigger.id,
                )
            )
            if workflow_id != WorkflowUUID.new(params.config.workflow_id):
                raise TriggerError(
                    TriggerErrorCode.INVALID,
                    "A schedule's workflow cannot change. Create a schedule for"
                    " the other workflow instead.",
                )
            update |= _schedule_settings(params.config)
        if params.status is not None:
            update["status"] = params.status.value
        schedules = WorkflowSchedulesService(self.session, self.role)
        try:
            await schedules.update_trigger_schedule(
                trigger.id, ScheduleUpdate(**update), trigger_name=params.name
            )
        except TracecatNotFoundError as e:
            raise _not_found("Trigger") from e

    async def _create_webhook_trigger(
        self, params: TriggerCreate, config: WebhookTriggerConfig
    ) -> Trigger:
        """Make the workflow's webhook a trigger, with its trigger row and binding.

        The webhook service writes all three. Changing a webhook needs
        ``workflow:update``, as on the workflow page; the binding makes every
        request run the workflow, so it also needs ``workflow:execute``.
        """
        _check_can_run(self.role, TriggerTargetType.WORKFLOW)
        try:
            webhook = await webhook_service.create_trigger_webhook(
                role=self.role,
                session=self.session,
                workflow_id=WorkflowUUID.new(config.workflow_id),
                params=_webhook_update(config, params.status),
                trigger_name=params.name,
            )
        except TracecatNotFoundError as e:
            await self.session.rollback()
            raise _not_found("Workflow") from e
        except TracecatConflictError as e:
            await self.session.rollback()
            raise TriggerError(TriggerErrorCode.CONFLICT, str(e)) from e
        except BaseException:
            await self.session.rollback()
            raise
        if webhook.trigger_id is None:
            raise RuntimeError(f"Webhook {webhook.id} was saved without a trigger")
        return await self._get_trigger_row(webhook.trigger_id)

    async def _update_webhook_trigger(
        self, trigger: Trigger, params: TriggerUpdate
    ) -> None:
        """Send a trigger update to its webhook, which commits it.

        A config replaces every webhook setting; its workflow must stay the
        webhook's. The webhook service also writes the name and status to the
        trigger row.
        """
        status = params.status
        if isinstance(params.config, WebhookTriggerConfig):
            workflow_id = await self.session.scalar(
                select(Webhook.workflow_id).where(
                    Webhook.workspace_id == self.workspace_id,
                    Webhook.trigger_id == trigger.id,
                )
            )
            if workflow_id != WorkflowUUID.new(params.config.workflow_id):
                raise TriggerError(
                    TriggerErrorCode.INVALID,
                    "A webhook's workflow cannot change. Use the other"
                    " workflow's webhook instead.",
                )
            update = _webhook_update(params.config, status)
        else:
            update = WebhookUpdate.model_validate(
                {"status": status.value} if status is not None else {}
            )
        try:
            await webhook_service.update_trigger_webhook(
                role=self.role,
                session=self.session,
                trigger_id=trigger.id,
                params=update,
                trigger_name=params.name,
            )
        except TracecatNotFoundError as e:
            raise _not_found("Trigger") from e

    async def _write_config(self, trigger: Trigger, config: TriggerConfig) -> None:
        """Insert or replace the kind's settings row of ``trigger``.

        Schedules and webhooks are written by their own services instead
        (``_create_schedule_trigger``, ``_create_webhook_trigger`` and the
        matching updates).
        """
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
            case ScheduleTriggerConfig():
                raise TypeError("The schedule service writes schedule settings")
            case WebhookTriggerConfig():
                raise TypeError("The webhook service writes webhook settings")

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
                case TriggerKind.SCHEDULE:
                    schedules = await self.session.scalars(
                        select(Schedule).where(
                            Schedule.workspace_id == self.workspace_id,
                            Schedule.trigger_id.in_(trigger_ids),
                        )
                    )
                    for schedule in schedules:
                        if schedule.trigger_id is not None:
                            configs[schedule.trigger_id] = _schedule_config(schedule)
                case TriggerKind.WEBHOOK:
                    # The API key loads with the webhook (one more query).
                    webhooks = await self.session.scalars(
                        select(Webhook).where(
                            Webhook.workspace_id == self.workspace_id,
                            Webhook.trigger_id.in_(trigger_ids),
                        )
                    )
                    for webhook in webhooks:
                        if webhook.trigger_id is not None:
                            configs[webhook.trigger_id] = _webhook_config(webhook)
        return configs

    # ----- Bindings -----

    @audit_log(resource_type="trigger_binding", action="create")
    async def create_binding(
        self, trigger_id: uuid.UUID, params: TriggerBindingCreate
    ) -> TriggerBinding:
        """Bind a trigger to a workflow or agent preset of this workspace.

        Raises:
            EntitlementRequired: If the plan lacks the trigger kind's feature.
            ScopeDeniedError: If the caller may not run the target.
            TriggerError: ``not_found`` if the trigger or target is not in the
                workspace, ``conflict`` if the trigger already has that target,
                ``invalid`` for a ``schedule`` or ``webhook`` trigger, whose
                one target is fixed.
        """
        _check_can_run(self.role, params.target.type)
        await self._lock_config()
        trigger = await self._get_trigger_row(trigger_id)
        await self._check_bindings_editable(trigger)
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
            EntitlementRequired: If the plan lacks the trigger kind's feature.
            TriggerError: ``not_found``, or ``invalid`` for an instruction on a
                workflow binding or for any change to a ``schedule`` or
                ``webhook`` trigger's binding.
        """
        await self._lock_config()
        binding = await self._get_binding_row(trigger_id, binding_id)
        await self._check_bindings_editable(await self._get_trigger_row(trigger_id))
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
            EntitlementRequired: If the plan lacks the trigger kind's feature.
            TriggerError: ``not_found`` if the trigger has no such binding,
                ``invalid`` for a ``schedule`` or ``webhook`` trigger, whose
                one target is fixed.
        """
        await self._get_binding_row(trigger_id, binding_id)
        await self._check_bindings_editable(await self._get_trigger_row(trigger_id))
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

    async def _can_read(self, kind: TriggerKind) -> bool:
        """Whether the caller may read triggers of ``kind`` at all.

        A kind needs its plan feature and its read scope
        (``_READ_SCOPE_BY_KIND``): a schedule's settings, inputs included, are
        as private as on ``/schedules``, and a webhook's URL as on the
        workflow page.
        """
        entitlement = ENTITLEMENT_BY_TRIGGER_KIND[kind]
        if entitlement is not None and not await self.has_entitlement(entitlement):
            return False
        if (scope := _READ_SCOPE_BY_KIND.get(kind)) is not None:
            return has_scope(self.role.scopes or frozenset(), scope)
        return True

    async def _check_kind_readable(self, kind: TriggerKind) -> None:
        """Require the kind's plan feature and its read scope, if any.

        Raises:
            EntitlementRequired: If the organization lacks the feature (403).
            ScopeDeniedError: Without the kind's ``_READ_SCOPE_BY_KIND`` scope.
        """
        await self._check_kind_entitled(kind)
        if (scope := _READ_SCOPE_BY_KIND.get(kind)) is not None:
            check_scopes(self.role, scope)

    async def _check_kind_entitled(self, kind: TriggerKind) -> None:
        """Require the plan feature of a trigger kind, if it has one.

        Raises:
            EntitlementRequired: If the organization lacks it (403).
        """
        entitlement = ENTITLEMENT_BY_TRIGGER_KIND[kind]
        if entitlement is not None and not await self.has_entitlement(entitlement):
            raise EntitlementRequired(entitlement.value)

    async def _check_bindings_editable(self, trigger: Trigger) -> None:
        """Refuse binding changes the trigger's kind cannot honour yet."""
        kind = TriggerKind(trigger.kind)
        await self._check_kind_entitled(kind)
        if (fixed := _FIXED_TARGET_BY_KIND.get(kind)) is not None:
            raise TriggerError(TriggerErrorCode.INVALID, fixed)

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
        """Get a trigger row of a kind this version knows (``_KNOWN_KINDS``)."""
        trigger = await self.session.scalar(
            select(Trigger).where(
                Trigger.workspace_id == self.workspace_id,
                Trigger.id == trigger_id,
                Trigger.kind.in_(_KNOWN_KINDS),
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


def _schedule_settings(config: ScheduleTriggerConfig) -> dict[str, Any]:
    """A schedule trigger config's schedule settings: all but kind and workflow."""
    return config.model_dump(exclude={"kind", "workflow_id"})


def _schedule_config(schedule: Schedule) -> ScheduleTriggerConfigRead:
    """The trigger view of a schedule row.

    Built without validation: a row the ``/schedules`` routes wrote is shown
    as it is, even one an update left without cron or interval.
    """
    return ScheduleTriggerConfigRead.model_construct(
        kind=TriggerKind.SCHEDULE,
        workflow_id=WorkflowUUID.new(schedule.workflow_id),
        schedule_id=ScheduleUUID.new(schedule.id),
        cron=schedule.cron,
        every=schedule.every,
        offset=schedule.offset,
        start_at=schedule.start_at,
        end_at=schedule.end_at,
        timeout=schedule.timeout or 0,
        inputs=schedule.inputs,
    )


def _webhook_update(
    config: WebhookTriggerConfig, status: TriggerStatus | None
) -> WebhookUpdate:
    """A webhook trigger config as a webhook update: the settings the caller
    gave, and the status when one is given.

    Settings left out keep the webhook's saved values. Their defaults must not
    be written: an empty ``allowlisted_cidrs`` allows every address.
    """
    values: dict[str, Any] = config.model_dump(
        exclude={"kind", "workflow_id"}, exclude_unset=True
    )
    if status is not None:
        values["status"] = status.value
    return WebhookUpdate.model_validate(values)


def _webhook_config(webhook: Webhook) -> WebhookTriggerConfigRead:
    """The trigger view of a webhook row, URL included.

    Only callers with ``workflow:read`` get here (``_READ_SCOPE_BY_KIND``),
    the same callers ``GET /workflows/{id}/webhook`` shows the URL to.
    """
    api_key = webhook.api_key
    return WebhookTriggerConfigRead(
        kind=TriggerKind.WEBHOOK,
        workflow_id=WorkflowUUID.new(webhook.workflow_id),
        webhook_id=webhook.id,
        methods=list(webhook.methods or []),
        allowlisted_cidrs=list(webhook.allowlisted_cidrs or []),
        include_headers=bool(webhook.include_headers),
        url=webhook.url,
        api_key=(
            WebhookApiKeyRead.model_validate(api_key, from_attributes=True)
            if api_key is not None
            else None
        ),
    )


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
