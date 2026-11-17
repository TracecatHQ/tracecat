from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError
from slugify import slugify
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import NoResultFound

from tracecat.audit.logger import AuditEventDetails, audit_log
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseTag, CaseTrigger, Workflow, WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowID, WorkflowUUID
from tracecat.managed_resources.guard import ensure_unmanaged, hold_for_customer_write
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig, CaseTriggerUpdate
from tracecat.workspace_sync.enums import SyncResourceType


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

    async def _lock_workflow(self, workflow_id: WorkflowID) -> None:
        workflow_uuid = WorkflowUUID.new(workflow_id)
        stmt = (
            select(Workflow.id)
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_uuid,
            )
            .with_for_update()
        )
        if await self.session.scalar(stmt) is None:
            raise TracecatNotFoundError(f"Workflow {workflow_id} not found")

    async def _ensure_case_trigger_exists(
        self, workflow_id: WorkflowID, *, commit: bool = True
    ) -> CaseTrigger:
        workflow_uuid = WorkflowUUID.new(workflow_id)
        stmt = select(CaseTrigger).where(
            CaseTrigger.workspace_id == self.workspace_id,
            CaseTrigger.workflow_id == workflow_uuid,
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
    ) -> CaseTrigger:
        await self._lock_workflow(workflow_id)
        case_trigger = await self._ensure_case_trigger_exists(
            workflow_id, commit=commit
        )
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
        if commit:
            await self.session.commit()
            await self.session.refresh(case_trigger)
        else:
            await self.session.flush()
        return case_trigger

    async def _resolve_tag_filters(
        self, tag_filters: Iterable[str], *, create_missing: bool = False
    ) -> list[str]:
        refs = list(dict.fromkeys(f.strip() for f in tag_filters if f and f.strip()))
        if not refs:
            return []

        # Stored refs pass through; only unmatched entries resolve by name or ID.
        existing = set(
            await self.session.scalars(
                select(CaseTag.ref)
                .where(
                    CaseTag.workspace_id == self.workspace_id,
                    CaseTag.ref.in_(refs),
                )
                .order_by(CaseTag.id)
                # Block a concurrent rename until this trigger's refs are saved.
                .with_for_update(read=True, key_share=True)
            )
        )
        misses = [ref for ref in refs if ref not in existing]
        if not misses:
            return refs
        resolved: dict[str, str] = {}
        if create_missing:
            if blank := [ref for ref in misses if not slugify(ref)]:
                raise TracecatValidationError(
                    f"Case tag filters need letters or digits: {', '.join(blank)}"
                )
            # Exported refs can outgrow the tag name limit; recreate them verbatim.
            long_refs = [r for r in misses if len(r) > 50 and slugify(r) == r]
            resolved |= await self._create_verbatim_tags(long_refs)
            misses = [r for r in misses if r not in resolved]
        try:
            tags = await CaseTagsService(self.session, role=self.role).resolve_tags(
                misses, create_if_missing=create_missing
            )
        except NoResultFound as exc:
            raise TracecatNotFoundError(str(exc)) from exc
        except ValidationError as exc:
            value = exc.errors()[0]["input"]
            raise TracecatValidationError(
                f"Case tag names must be 1-50 characters: {value!r}"
            ) from exc
        resolved |= {miss: tag.ref for miss, tag in zip(misses, tags, strict=True)}
        return list(dict.fromkeys(resolved.get(ref, ref) for ref in refs))

    async def _create_verbatim_tags(self, refs: list[str]) -> dict[str, str]:
        if not refs:
            return {}
        await hold_for_customer_write(self.session, self.workspace_id)
        await self.session.execute(
            insert(CaseTag)
            .values(
                [
                    {"workspace_id": self.workspace_id, "name": ref, "ref": ref}
                    for ref in refs
                ]
            )
            .on_conflict_do_nothing()
        )
        # A synced tag can hold this name under a different ref.
        rows = await self.session.execute(
            select(CaseTag.ref, CaseTag.name).where(
                CaseTag.workspace_id == self.workspace_id,
                or_(CaseTag.ref.in_(refs), CaseTag.name.in_(refs)),
            )
        )
        by_ref: dict[str, str] = {}
        by_name: dict[str, str] = {}
        for ref, name in rows.tuples():
            by_ref[ref] = ref
            by_name[name] = ref
        return {ref: by_ref.get(ref) or by_name[ref] for ref in refs}
