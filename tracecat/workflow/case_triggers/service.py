from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from tracecat.db.models import CaseTag, CaseTrigger
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import WorkflowID, WorkflowUUID
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat.workflow.case_triggers.schemas import CaseTriggerConfig, CaseTriggerUpdate


class CaseTriggersService(BaseWorkspaceService):
    """Manage workflow case trigger configuration."""

    service_name = "case_triggers"

    @requires_entitlement(Entitlement.CASE_TRIGGERS)
    async def get_case_trigger(self, workflow_id: WorkflowID) -> CaseTrigger:
        stmt = select(CaseTrigger).where(
            CaseTrigger.workspace_id == self.workspace_id,
            CaseTrigger.workflow_id == WorkflowUUID.new(workflow_id),
        )
        result = await self.session.execute(stmt)
        try:
            return result.scalar_one()
        except NoResultFound as exc:
            raise TracecatNotFoundError(
                f"Case trigger for workflow {workflow_id} not found"
            ) from exc

    @requires_entitlement(Entitlement.CASE_TRIGGERS)
    async def upsert_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerConfig,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> CaseTrigger:
        try:
            case_trigger = await self.get_case_trigger(workflow_id)
            return await self.update_case_trigger(
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

    @requires_entitlement(Entitlement.CASE_TRIGGERS)
    async def update_case_trigger(
        self,
        workflow_id: WorkflowID,
        params: CaseTriggerUpdate,
        *,
        create_missing_tags: bool = False,
        commit: bool = True,
    ) -> CaseTrigger:
        case_trigger = await self.get_case_trigger(workflow_id)
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
