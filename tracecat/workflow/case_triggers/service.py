"""Case trigger configuration writes.

A case trigger that has been turned on is also a ``case_trigger`` trigger: a
``trigger`` row with one workflow binding, linked by
``case_trigger.trigger_id``, so it shows in the Triggers tab. Every write here
that turns a case trigger on gives it that row if it has none, in the same
transaction; it keeps the row when it goes offline. A database trigger on
``case_trigger`` deletes the row with the case trigger and copies every status
change to it, so writers outside this service (the dispatcher turning an
invalid trigger off, an import clearing one) keep it in step too.

Locks: every write here locks the workflow (``FOR UPDATE``) and then writes
its case trigger row, as before case triggers became triggers. The trigger
row and binding are new rows, and the binding's key-share lock on the
workflow is already held, so turning a case trigger into a trigger adds no
lock wait. The migration backfill never waits for either row.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from tracecat.audit.logger import AuditEventDetails, audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import (
    CaseTag,
    CaseTrigger,
    Trigger,
    TriggerBinding,
    Workflow,
    WorkflowDefinition,
)
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowID, WorkflowUUID
from tracecat.managed_resources.guard import ensure_unmanaged
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat.triggers.enums import TriggerKind, TriggerStatus, TriggerTargetType
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig, CaseTriggerUpdate
from tracecat.workspace_sync.enums import SyncResourceType

# The trigger name column is 255 characters; leave room for the suffix.
_TITLE_IN_NAME_MAX = 255 - len(" case trigger")


def default_trigger_name(workflow_title: str | None) -> str:
    """The name a case trigger's trigger gets when none is given.

    The workflow title plus " case trigger", or "Case trigger" for an
    untitled workflow. The migration backfill uses the same rule.
    """
    title = (workflow_title or "").strip()
    if not title:
        return "Case trigger"
    return f"{title[:_TITLE_IN_NAME_MAX]} case trigger"


def _tab_audit_details(
    params: CaseTriggerUpdate, trigger_name: str | None
) -> AuditEventDetails:
    changed = set(params.model_fields_set)
    if trigger_name is not None:
        changed.add("name")
    return AuditEventDetails(data={"changed_fields": sorted(changed)})


class CaseTriggersService(BaseWorkspaceService):
    """Manage workflow case trigger configuration."""

    service_name = "case_triggers"

    def _case_trigger_upsert_audit_details(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerConfig,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> AuditEventDetails:
        # commit=False suppresses emission for sync/dry-run callers.
        if commit is False:
            return AuditEventDetails(emit=False)
        return AuditEventDetails(data={"changed_fields": sorted(params.model_dump())})

    async def _case_trigger_update_audit_details(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerUpdate,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> AuditEventDetails:
        if commit is False:
            return AuditEventDetails(emit=False)
        workflow_uuid = WorkflowUUID.new(workflow_id)
        trigger_id = await self.session.scalar(
            select(CaseTrigger.id).where(
                CaseTrigger.workspace_id == self.workspace_id,
                CaseTrigger.workflow_id == workflow_uuid,
            )
        )
        return AuditEventDetails(
            resource_id=trigger_id,
            data={"changed_fields": sorted(params.model_fields_set)},
        )

    async def _lock_workflow(self, workflow_id: WorkflowID) -> str:
        """Lock the workflow until commit and return its title.

        Raises:
            TracecatNotFoundError: If the workspace has no such workflow.
        """
        workflow_uuid = WorkflowUUID.new(workflow_id)
        stmt = (
            select(Workflow.title)
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_uuid,
            )
            .with_for_update()
        )
        # The title column is NOT NULL, so None means no such workflow.
        if (title := await self.session.scalar(stmt)) is None:
            raise TracecatNotFoundError(f"Workflow {workflow_id} not found")
        return title

    async def _ensure_case_trigger_exists(
        self, workflow_id: WorkflowID, *, commit: bool = True
    ) -> CaseTrigger:
        workflow_uuid = WorkflowUUID.new(workflow_id)
        # Read fresh: under the workflow lock this sees whether a concurrent
        # writer gave the case trigger its trigger row.
        stmt = (
            select(CaseTrigger)
            .where(
                CaseTrigger.workspace_id == self.workspace_id,
                CaseTrigger.workflow_id == workflow_uuid,
            )
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        if case_trigger := result.scalar_one_or_none():
            return case_trigger

        workflow_exists_stmt = select(Workflow.id).where(
            Workflow.workspace_id == self.workspace_id,
            Workflow.id == workflow_uuid,
        )
        if await self.session.scalar(workflow_exists_stmt) is None:
            raise TracecatNotFoundError(f"Workflow {workflow_id} not found")

        await self.session.execute(
            insert(CaseTrigger)
            .values(
                workspace_id=self.workspace_id,
                workflow_id=workflow_uuid,
                status="offline",
                event_types=[],
                tag_filters=[],
            )
            .on_conflict_do_nothing(index_elements=[CaseTrigger.workflow_id])
        )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

        result = await self.session.execute(stmt)
        if case_trigger := result.scalar_one_or_none():
            return case_trigger
        raise TracecatValidationError("Failed to ensure case trigger")

    async def _ensure_workflow_has_runnable_definition(
        self, workflow_id: WorkflowID
    ) -> None:
        workflow_uuid = WorkflowUUID.new(workflow_id)
        version_stmt = select(Workflow.version).where(
            Workflow.workspace_id == self.workspace_id,
            Workflow.id == workflow_uuid,
        )
        workflow_version = await self.session.scalar(version_stmt)
        if workflow_version is None:
            raise TracecatValidationError(
                "Publish the workflow before enabling case triggers"
            )

        definition_stmt = select(WorkflowDefinition.content).where(
            WorkflowDefinition.workspace_id == self.workspace_id,
            WorkflowDefinition.workflow_id == workflow_uuid,
            WorkflowDefinition.version == workflow_version,
        )
        content = await self.session.scalar(definition_stmt)
        if not content:
            raise TracecatValidationError(
                "Publish the workflow before enabling case triggers"
            )
        try:
            DSLInput.model_validate(content)
        except ValidationError as e:
            raise TracecatValidationError(
                "Publish the workflow before enabling case triggers"
            ) from e

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def validate_case_trigger_config(
        self, workflow_id: WorkflowID, config: CaseTriggerConfig
    ) -> None:
        """Validate a case-trigger config without writing anything.

        Runs the same online-readiness checks ``_update_case_trigger`` enforces
        (online requires non-empty event types and a published, runnable workflow
        definition) so a dry run reports the same outcome a real apply would,
        instead of reporting ``valid: true`` for a config that fails on persist.

        Raises:
            TracecatValidationError: If the config is not applyable.
        """
        if config.status != "online":
            return
        if not config.event_types:
            raise TracecatValidationError(
                "event_types must be non-empty when status is online"
            )
        await self._ensure_workflow_has_runnable_definition(workflow_id)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def get_case_trigger(self, workflow_id: WorkflowID) -> CaseTrigger:
        return await self._ensure_case_trigger_exists(workflow_id)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    @audit_log(
        resource_type="case_trigger",
        action="upsert",
        resource_id_attr="id",
        attempt_metadata=_case_trigger_upsert_audit_details,
    )
    async def upsert_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerConfig,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> CaseTrigger:
        await ensure_unmanaged(
            self.session,
            workspace_id=self.workspace_id,
            resource_type=SyncResourceType.WORKFLOW,
            local_id=WorkflowUUID.new(workflow_id),
        )
        return await self._upsert_case_trigger(
            workflow_id,
            params,
            create_missing_tags=create_missing_tags,
            commit=commit,
        )

    async def sync_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerConfig,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> CaseTrigger:
        """Apply case-trigger config from sync without checking entitlements."""
        return await self._upsert_case_trigger(
            workflow_id,
            params,
            create_missing_tags=create_missing_tags,
            commit=commit,
        )

    async def _upsert_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerConfig,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> CaseTrigger:
        await self._lock_workflow(workflow_id)
        try:
            case_trigger = await self._ensure_case_trigger_exists(
                workflow_id, commit=commit
            )
            return await self._update_case_trigger(
                workflow_id,
                CaseTriggerUpdate(
                    status=params.status,
                    event_types=params.event_types,
                    tag_filters=params.tag_filters,
                ),
                create_missing_tags=create_missing_tags,
                commit=commit,
            )
        except TracecatNotFoundError:
            resolved_tags = await self._resolve_tag_filters(
                params.tag_filters, create_missing=create_missing_tags
            )
            case_trigger = CaseTrigger(
                workspace_id=self.workspace_id,
                workflow_id=WorkflowUUID.new(workflow_id),
                status=params.status,
                event_types=[evt.value for evt in params.event_types],
                tag_filters=resolved_tags,
            )
            self.session.add(case_trigger)
            if commit:
                await self.session.commit()
                await self.session.refresh(case_trigger)
            else:
                await self.session.flush()
            return case_trigger

    @requires_entitlement(Entitlement.CASE_ADDONS)
    @audit_log(
        resource_type="case_trigger",
        action="update",
        resource_id_attr="id",
        attempt_metadata=_case_trigger_update_audit_details,
    )
    async def update_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerUpdate,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> CaseTrigger:
        await ensure_unmanaged(
            self.session,
            workspace_id=self.workspace_id,
            resource_type=SyncResourceType.WORKFLOW,
            local_id=WorkflowUUID.new(workflow_id),
        )
        return await self._update_case_trigger(
            workflow_id,
            params,
            create_missing_tags=create_missing_tags,
            commit=commit,
        )

    async def _update_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerUpdate,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
        trigger_name: str | None = None,
        as_trigger: bool = False,
    ) -> CaseTrigger:
        """Apply ``params`` and keep the case trigger's trigger row in step.

        A case trigger that is online after the update (or ``as_trigger``, a
        create from the Triggers tab) gets a trigger row if it has none. One
        that has a row gets its status, name and timestamp written with it.
        """
        title = await self._lock_workflow(workflow_id)
        # Not committed here, so the workflow lock holds until the write below.
        case_trigger = await self._ensure_case_trigger_exists(workflow_id, commit=False)
        updates = params.model_dump(exclude_unset=True)

        status = updates.get("status", case_trigger.status)
        event_types = updates.get("event_types", case_trigger.event_types)
        if isinstance(event_types, list):
            event_types = [
                evt.value if hasattr(evt, "value") else evt for evt in event_types
            ]
        if status == "online" and not event_types:
            raise TracecatValidationError(
                "event_types must be non-empty when status is online"
            )
        if status == "online":
            await self._ensure_workflow_has_runnable_definition(workflow_id)

        tag_filters = updates.get("tag_filters", case_trigger.tag_filters)
        if tag_filters is None:
            resolved_tags = []
        else:
            resolved_tags = await self._resolve_tag_filters(
                tag_filters, create_missing=create_missing_tags
            )

        case_trigger.status = status
        case_trigger.event_types = list(event_types) if event_types is not None else []
        case_trigger.tag_filters = resolved_tags

        self.session.add(case_trigger)
        await self._write_trigger_row(
            case_trigger,
            status_set="status" in updates,
            name=trigger_name,
            default_name=default_trigger_name(title),
            as_trigger=as_trigger,
        )
        if commit:
            await self.session.commit()
            await self.session.refresh(case_trigger)
        else:
            await self.session.flush()
        return case_trigger

    async def _write_trigger_row(
        self,
        case_trigger: CaseTrigger,
        *,
        status_set: bool,
        name: str | None,
        default_name: str,
        as_trigger: bool,
    ) -> None:
        """Give the case trigger its trigger row, or update the one it has.

        The caller holds the workflow lock (``_lock_workflow``).
        """
        if case_trigger.trigger_id is None:
            if as_trigger or case_trigger.status == TriggerStatus.ONLINE:
                await self._add_trigger(case_trigger, name=name or default_name)
            return
        # The header's timestamp covers its settings too. The database trigger
        # also copies the status; writing it here keeps the loaded row current.
        values: dict[str, Any] = {"updated_at": func.now()}
        if status_set:
            values["status"] = case_trigger.status
        if name is not None:
            values["name"] = name
        await self.session.execute(
            update(Trigger)
            .where(
                Trigger.workspace_id == case_trigger.workspace_id,
                Trigger.id == case_trigger.trigger_id,
            )
            .values(**values)
        )

    async def _add_trigger(self, case_trigger: CaseTrigger, *, name: str) -> None:
        """Add the ``case_trigger`` trigger row and its workflow binding.

        The binding is the case trigger's workflow, the only target a case
        trigger runs until case triggers start their targets through
        bindings. It is online even for an offline case trigger, as for
        schedules and webhooks: the trigger's status is what counts until
        bindings drive the runtime (step 2 must revisit this). The binding's
        key-share lock on the workflow is already held: the caller locked it
        ``FOR UPDATE``.
        """
        trigger = Trigger(
            id=uuid.uuid4(),
            workspace_id=case_trigger.workspace_id,
            kind=TriggerKind.CASE_TRIGGER.value,
            name=name,
            status=case_trigger.status,
        )
        self.session.add(trigger)
        # The binding's and the case trigger's foreign keys need the row first.
        await self.session.flush()
        self.session.add(
            TriggerBinding(
                workspace_id=case_trigger.workspace_id,
                trigger_id=trigger.id,
                target_type=TriggerTargetType.WORKFLOW.value,
                workflow_id=case_trigger.workflow_id,
                status=TriggerStatus.ONLINE.value,
            )
        )
        case_trigger.trigger_id = trigger.id
        self.session.add(case_trigger)

    # ----- Entry points for the Triggers tab -----
    # Each writes the case trigger and its trigger row and binding, and
    # commits, so its ``case_trigger`` audit record follows the commit as on
    # the workflow page. Each needs ``workflow:update`` and the
    # ``case_addons`` feature, as the workflow page's case trigger panel does,
    # and refuses a managed workflow as ``update_case_trigger`` does: the
    # Triggers tab is another way in, not a way around it.

    def _create_trigger_audit_details(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerUpdate,
        *,
        trigger_name: str,
    ) -> AuditEventDetails:
        return _tab_audit_details(params, trigger_name)

    def _update_trigger_audit_details(
        self,
        trigger_id: uuid.UUID,
        params: CaseTriggerUpdate,
        *,
        trigger_name: str | None = None,
    ) -> AuditEventDetails:
        return _tab_audit_details(params, trigger_name)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    @require_scope("workflow:update")
    @audit_log(
        resource_type="case_trigger",
        action="update",
        resource_id_attr="id",
        attempt_metadata=_create_trigger_audit_details,
    )
    async def create_trigger_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerUpdate,
        *,
        trigger_name: str,
    ) -> CaseTrigger:
        """Make a workflow's case trigger a trigger, with the given settings.

        The case trigger row already exists (every workflow has one); this
        applies ``params`` to it and adds its trigger row and binding. Only a
        status in ``params`` changes the status.

        Raises:
            TracecatNotFoundError: If the workflow is not in the workspace, or
                a tag filter names no tag.
            TracecatConflictError: If the case trigger already is a trigger.
            TracecatValidationError: If it would be online without event
                types, or the workflow has no published definition.
        """
        await ensure_unmanaged(
            self.session,
            workspace_id=self.workspace_id,
            resource_type=SyncResourceType.WORKFLOW,
            local_id=WorkflowUUID.new(workflow_id),
        )
        await self._lock_workflow(workflow_id)
        case_trigger = await self._ensure_case_trigger_exists(workflow_id, commit=False)
        if case_trigger.trigger_id is not None:
            raise TracecatConflictError(
                "This workflow's case trigger is already a trigger. Open it in"
                " the Triggers tab to change it."
            )
        return await self._update_case_trigger(
            workflow_id, params, trigger_name=trigger_name, as_trigger=True
        )

    @requires_entitlement(Entitlement.CASE_ADDONS)
    @require_scope("workflow:update")
    @audit_log(
        resource_type="case_trigger",
        action="update",
        resource_id_attr="id",
        attempt_metadata=_update_trigger_audit_details,
    )
    async def update_trigger_case_trigger(
        self,
        trigger_id: uuid.UUID,
        params: CaseTriggerUpdate,
        *,
        trigger_name: str | None = None,
    ) -> CaseTrigger:
        """Update the case trigger behind a ``case_trigger`` trigger, and its
        trigger row.

        Raises:
            TracecatNotFoundError: If no case trigger belongs to the trigger,
                or a tag filter names no tag.
            TracecatValidationError: As for ``update_case_trigger``.
        """
        workflow_id = await self._lock_trigger_case_trigger(trigger_id)
        return await self._update_case_trigger(
            workflow_id, params, trigger_name=trigger_name
        )

    @requires_entitlement(Entitlement.CASE_ADDONS)
    @require_scope("workflow:update")
    @audit_log(resource_type="case_trigger", action="update", resource_id_attr="id")
    async def delete_trigger_case_trigger(self, trigger_id: uuid.UUID) -> CaseTrigger:
        """Turn the case trigger behind a trigger offline and delete the
        trigger row.

        The case trigger row stays (every workflow has one), with its event
        types and tag filters; it gets a new trigger row when it is next
        turned on.

        Raises:
            TracecatNotFoundError: If no case trigger belongs to the trigger.
        """
        workflow_id = await self._lock_trigger_case_trigger(trigger_id)
        case_trigger = await self._ensure_case_trigger_exists(workflow_id, commit=False)
        case_trigger.status = TriggerStatus.OFFLINE.value
        self.session.add(case_trigger)
        await self.session.flush()
        # The key nulls case_trigger.trigger_id; the binding goes by cascade.
        await self.session.execute(
            delete(Trigger).where(
                Trigger.workspace_id == self.workspace_id, Trigger.id == trigger_id
            )
        )
        await self.session.commit()
        await self.session.refresh(case_trigger)
        return case_trigger

    async def _lock_trigger_case_trigger(self, trigger_id: uuid.UUID) -> WorkflowUUID:
        """Lock the workflow of the case trigger behind a trigger.

        Takes the managed check (and its ownership lock) first, then the
        workflow lock, and reads the case trigger again under it: the trigger
        may have been deleted meanwhile.

        Raises:
            TracecatNotFoundError: If no case trigger belongs to the trigger.
        """
        found = await self.session.scalar(
            select(CaseTrigger.workflow_id).where(
                CaseTrigger.workspace_id == self.workspace_id,
                CaseTrigger.trigger_id == trigger_id,
            )
        )
        if found is None:
            raise TracecatNotFoundError(f"No case trigger for trigger {trigger_id}")
        workflow_id = WorkflowUUID.new(found)
        await ensure_unmanaged(
            self.session,
            workspace_id=self.workspace_id,
            resource_type=SyncResourceType.WORKFLOW,
            local_id=workflow_id,
        )
        await self._lock_workflow(workflow_id)
        case_trigger = await self._ensure_case_trigger_exists(workflow_id, commit=False)
        if case_trigger.trigger_id != trigger_id:
            raise TracecatNotFoundError(f"No case trigger for trigger {trigger_id}")
        return workflow_id

    async def _resolve_tag_filters(
        self, tag_filters: Iterable[str], *, create_missing: bool = False
    ) -> list[str]:
        refs = [ref.strip() for ref in tag_filters if ref and ref.strip()]
        if not refs:
            return []
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for ref in refs:
            if ref in seen:
                continue
            seen.add(ref)
            deduped.append(ref)

        stmt = select(CaseTag).where(
            CaseTag.workspace_id == self.workspace_id,
            CaseTag.ref.in_(deduped),
        )
        result = await self.session.execute(stmt)
        existing = {tag.ref for tag in result.scalars().all()}
        missing = [ref for ref in deduped if ref not in existing]

        if missing and not create_missing:
            raise TracecatNotFoundError(f"Case tag(s) not found: {', '.join(missing)}")

        if missing and create_missing:
            for ref in missing:
                tag = CaseTag(
                    workspace_id=self.workspace_id,
                    name=ref,
                    ref=ref,
                    color=None,
                )
                self.session.add(tag)
            await self.session.flush()

        return deduped
