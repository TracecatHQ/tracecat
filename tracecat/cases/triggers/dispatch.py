"""Start the workflow runs that case trigger outbox rows are waiting for.

This is the outbox counterpart of the Redis consumer's message handling,
without the Redis lock and done keys. Each run has a fixed execution ID and
starts with ``REJECT_DUPLICATE``, so repeating a row after a crash or retry
starts nothing twice. A row is deleted in the same transaction that records
its outcome, after every start for it has been accepted.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from temporalio.exceptions import WorkflowAlreadyStartedError

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.enums import CaseEventType
from tracecat.cases.schemas import CaseCommentWorkflowStatus
from tracecat.cases.triggers.outbox import online_trigger_filter
from tracecat.cases.triggers.types import (
    CaseCommentWorkflowTrigger,
    CaseTriggerPayload,
    CaseTriggerTag,
    CommentWorkflowPayload,
)
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import (
    Case,
    CaseComment,
    CaseEvent,
    CaseTrigger,
    CaseTriggerOutbox,
    Workspace,
)
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import TracecatDSLError
from tracecat.identifiers.workflow import (
    ExecutionUUID,
    WorkflowExecutionID,
    WorkflowUUID,
)
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.tiers.access import is_org_entitled
from tracecat.tiers.enums import Entitlement
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService

CASE_TRIGGER_SERVICE_ID = "tracecat-case-triggers"
OUTBOX_BATCH_SIZE = 100
# Errors from bad input that fail every attempt the same way, such as a
# workflow timeout too large to convert. Retrying them would hold the row
# forever, so the start is logged and dropped as the Redis consumer did.
# Temporal, database and storage outages raise other types and are retried.
PERMANENT_START_ERRORS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    ArithmeticError,
)


def case_trigger_exec_id(
    event_id: uuid.UUID, workflow_id: uuid.UUID
) -> WorkflowExecutionID:
    """The fixed execution ID of the run a case event starts for a workflow."""
    exec_uuid = ExecutionUUID.new(uuid.uuid5(event_id, str(workflow_id)))
    return f"{WorkflowUUID.new(workflow_id).short()}/{exec_uuid.short()}"


async def outbox_has_rows() -> bool:
    """Whether any outbox row is waiting to be dispatched."""
    async with get_async_session_bypass_rls_context_manager() as session:
        return bool(
            await session.scalar(select(exists().select_from(CaseTriggerOutbox)))
        )


async def list_outbox_row_ids(limit: int = OUTBOX_BATCH_SIZE) -> list[int]:
    """The outbox rows to dispatch next, least recently attempted first.

    A failed row is touched after each attempt, so rows that keep failing move
    behind newer rows instead of filling every batch.
    """
    async with get_async_session_bypass_rls_context_manager() as session:
        result = await session.scalars(
            select(CaseTriggerOutbox.surrogate_id)
            .order_by(CaseTriggerOutbox.updated_at, CaseTriggerOutbox.surrogate_id)
            .limit(limit)
        )
        return list(result.all())


async def _touch_outbox_row(row_id: int) -> None:
    async with get_async_session_bypass_rls_context_manager() as session:
        await session.execute(
            update(CaseTriggerOutbox)
            .where(CaseTriggerOutbox.surrogate_id == row_id)
            .values(updated_at=func.clock_timestamp())
        )
        await session.commit()


def _event_type_value(event: CaseEvent) -> str:
    return event.type.value if isinstance(event.type, CaseEventType) else event.type


def _event_created_at(event: CaseEvent) -> datetime:
    created_at = event.created_at or datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at


def _tags_payload(case: Case) -> list[CaseTriggerTag]:
    return [
        {"id": str(tag.id), "ref": tag.ref, "name": tag.name, "color": tag.color}
        for tag in case.tags
    ]


def build_case_trigger_payload(*, case: Case, event: CaseEvent) -> CaseTriggerPayload:
    """Trigger inputs for a run started by a configured case trigger."""
    return {
        "case_id": str(case.id),
        "event": {
            "id": str(event.id),
            "type": _event_type_value(event),
            "data": event.data,
            "created_at": _event_created_at(event).isoformat(),
            "user_id": str(event.user_id) if event.user_id else None,
            "wf_exec_id": event.data.get("wf_exec_id") if event.data else None,
        },
        "tags": _tags_payload(case),
        "workspace_id": str(case.workspace_id),
    }


def build_comment_workflow_payload(
    *, case: Case, event: CaseEvent, trigger: CaseCommentWorkflowTrigger
) -> CommentWorkflowPayload:
    """Trigger inputs for a workflow a user picked when posting a comment."""
    comment_id = str(trigger.comment_id)
    parent_id = str(trigger.parent_id) if trigger.parent_id is not None else None
    return {
        "case_id": str(case.id),
        "comment": trigger.comment,
        "comment_id": comment_id,
        "parent_id": parent_id,
        "thread_root_id": parent_id or comment_id,
        "is_reply": parent_id is not None,
        "text": trigger.comment,
        "workspace_id": str(case.workspace_id),
        "triggered_by": {
            "type": trigger.triggered_by_type,
            "user_id": str(trigger.triggered_by_user_id)
            if trigger.triggered_by_user_id is not None
            else None,
            "service_id": trigger.triggered_by_service_id,
        },
        "event": {
            "id": str(event.id),
            "type": _event_type_value(event),
            "created_at": _event_created_at(event).isoformat(),
            "user_id": str(event.user_id) if event.user_id else None,
        },
        "tags": _tags_payload(case),
    }


def _parse_comment_workflow(
    row: CaseTriggerOutbox,
) -> CaseCommentWorkflowTrigger | None:
    if row.extras is None:
        return None
    try:
        return CaseCommentWorkflowTrigger.model_validate(row.extras)
    except ValidationError:
        logger.warning(
            "Dropping invalid comment workflow fields from case trigger outbox row",
            outbox_id=str(row.id),
            event_id=str(row.event_id),
        )
        return None


class CaseTriggerStartUnknownError(Exception):
    """Temporal could not say whether a run's start was accepted; retry later."""

    def __init__(self, wf_exec_id: WorkflowExecutionID) -> None:
        super().__init__(f"Could not settle the start of {wf_exec_id}")
        self.wf_exec_id = wf_exec_id


async def _lock_outbox_row(
    session: AsyncSession, row_id: int
) -> CaseTriggerOutbox | None:
    """Lock a row, or return None if it is gone or another dispatcher holds it."""
    return await session.scalar(
        select(CaseTriggerOutbox)
        .where(CaseTriggerOutbox.surrogate_id == row_id)
        .with_for_update(skip_locked=True)
    )


async def _load_event_and_case(
    session: AsyncSession, row: CaseTriggerOutbox
) -> tuple[CaseEvent, Case] | None:
    event = await session.scalar(
        select(CaseEvent).where(
            CaseEvent.id == row.event_id,
            CaseEvent.case_id == row.case_id,
            CaseEvent.workspace_id == row.workspace_id,
        )
    )
    if event is None:
        return None
    case = await session.scalar(
        select(Case)
        .options(selectinload(Case.tags))
        .where(Case.id == row.case_id, Case.workspace_id == row.workspace_id)
    )
    if case is None:
        return None
    return event, case


class CaseTriggerOutboxDispatcher:
    """Dispatch outbox rows one at a time, each in its own transaction."""

    def __init__(self, heartbeat: Callable[[], None] | None = None) -> None:
        self._heartbeat = heartbeat or (lambda: None)
        self._workspace_roles: dict[uuid.UUID, Role] = {}

    async def dispatch_row(self, row_id: int) -> None:
        """Start every run for one outbox row, then delete the row.

        A picked comment workflow is settled in its own transaction first, and
        the row records that, so a retry for the sake of another run never
        handles it again. Configured triggers follow in a second transaction
        that deletes the row once every start was accepted.

        Another dispatcher holding the row's lock means it is already being
        handled, so the row is skipped. Transient failures raise and leave the
        row in place, moved to the back of the queue, for the next attempt.
        """
        try:
            comment_error = await self._dispatch_comment_workflow(row_id)
            await self._dispatch_configured_triggers(
                row_id, delete_row=comment_error is None
            )
            if comment_error is not None:
                raise comment_error
        except Exception:
            await _touch_outbox_row(row_id)
            raise

    async def _dispatch_comment_workflow(self, row_id: int) -> Exception | None:
        """Settle a row's picked comment workflow; return the error if it must retry."""
        async with get_async_session_bypass_rls_context_manager() as session:
            row = await _lock_outbox_row(session, row_id)
            if row is None:
                return None
            comment_workflow = _parse_comment_workflow(row)
            if comment_workflow is None or comment_workflow.settled:
                return None
            if loaded := await _load_event_and_case(session, row):
                event, case = loaded
                role = await self._get_service_role(session, row.workspace_id)
                try:
                    await self._start_comment_workflow(
                        session,
                        role=role,
                        case=case,
                        event=event,
                        trigger=comment_workflow,
                    )
                except Exception as e:
                    await session.rollback()
                    return e
            row.extras = comment_workflow.model_copy(
                update={"settled": True}
            ).model_dump(mode="json")
            await session.commit()
            return None

    async def _dispatch_configured_triggers(
        self, row_id: int, *, delete_row: bool
    ) -> None:
        async with get_async_session_bypass_rls_context_manager() as session:
            row = await _lock_outbox_row(session, row_id)
            if row is None:
                return
            if loaded := await _load_event_and_case(session, row):
                event, case = loaded
                role = await self._get_service_role(session, row.workspace_id)
                # Try every start before failing the row, so one run that
                # cannot start does not hold back the event's other runs.
                failures: list[Exception] = []
                await self._start_configured_triggers(
                    session,
                    role=role,
                    case=case,
                    event=event,
                    comment_workflow=_parse_comment_workflow(row),
                    failures=failures,
                )
                if failures:
                    await session.rollback()
                    raise failures[0]
            # An overlapping attempt may still hold or have rolled back the
            # picked workflow, so the row goes only once that is settled.
            comment_workflow = _parse_comment_workflow(row)
            if delete_row and (comment_workflow is None or comment_workflow.settled):
                await session.delete(row)
            await session.commit()

    async def _start_configured_triggers(
        self,
        session: AsyncSession,
        *,
        role: Role,
        case: Case,
        event: CaseEvent,
        comment_workflow: CaseCommentWorkflowTrigger | None,
        failures: list[Exception],
    ) -> None:
        if not await self._has_case_addons_entitlement(session, role):
            logger.info(
                "Skipping configured case trigger dispatch; entitlement missing",
                event_id=str(event.id),
                workspace_id=str(case.workspace_id),
                organization_id=str(role.organization_id),
                entitlement=Entitlement.CASE_ADDONS.value,
            )
            return

        try:
            event_type = CaseEventType(_event_type_value(event))
        except ValueError:
            logger.warning(
                "Unknown case event type, skipping", event_type=_event_type_value(event)
            )
            return
        triggers = (
            await session.scalars(
                select(CaseTrigger).where(
                    online_trigger_filter(case.workspace_id, event_type)
                )
            )
        ).all()
        case_tag_refs = {tag.ref for tag in case.tags}
        for trigger in triggers:
            if (
                comment_workflow is not None
                and trigger.workflow_id == comment_workflow.workflow_id
            ):
                continue
            if trigger.tag_filters and not case_tag_refs.intersection(
                trigger.tag_filters
            ):
                continue
            try:
                await self._start_trigger_workflow(
                    session, role=role, trigger=trigger, case=case, event=event
                )
            except Exception as e:
                failures.append(e)

    async def _get_service_role(
        self, session: AsyncSession, workspace_id: uuid.UUID
    ) -> Role:
        if role := self._workspace_roles.get(workspace_id):
            return role
        organization_id = await session.scalar(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        role = Role(
            type="service",
            workspace_id=workspace_id,
            organization_id=organization_id,
            user_id=None,
            service_id=CASE_TRIGGER_SERVICE_ID,
            scopes=SERVICE_PRINCIPAL_SCOPES[CASE_TRIGGER_SERVICE_ID],
        )
        self._workspace_roles[workspace_id] = role
        return role

    async def _has_case_addons_entitlement(
        self, session: AsyncSession, role: Role
    ) -> bool:
        if role.organization_id is None:
            logger.warning(
                "Skipping configured case trigger dispatch; service role has no organization",
                workspace_id=str(role.workspace_id),
            )
            return False
        return await is_org_entitled(
            session, role.organization_id, Entitlement.CASE_ADDONS
        )

    async def _start_trigger_workflow(
        self,
        session: AsyncSession,
        *,
        role: Role,
        trigger: CaseTrigger,
        case: Case,
        event: CaseEvent,
    ) -> None:
        self._heartbeat()
        wf_id = WorkflowUUID.new(trigger.workflow_id)
        defn = await WorkflowDefinitionsService(
            session, role=role
        ).get_definition_by_workflow_id(
            wf_id, load_relationships=False, load_workflow=True
        )
        if not defn:
            await self._disable_invalid_trigger(
                session, trigger, event, reason="missing workflow definition"
            )
            return
        if defn.workflow is None or defn.workflow.version != defn.version:
            await self._disable_invalid_trigger(
                session, trigger, event, reason="current workflow definition missing"
            )
            return
        if not defn.content:
            await self._disable_invalid_trigger(
                session, trigger, event, reason="missing workflow definition content"
            )
            return
        try:
            dsl = DSLInput.model_validate(defn.content)
        except (ValidationError, TracecatDSLError):
            await self._disable_invalid_trigger(
                session, trigger, event, reason="invalid workflow definition content"
            )
            return

        wf_exec_id = case_trigger_exec_id(event.id, trigger.workflow_id)
        service = await WorkflowExecutionsService.connect(role=role)
        try:
            await service.create_workflow_execution_wait_for_start(
                dsl=dsl,
                wf_id=wf_id,
                wf_exec_id=wf_exec_id,
                payload=build_case_trigger_payload(case=case, event=event),
                trigger_type=TriggerType.CASE,
                registry_lock=RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None,
            )
        except WorkflowAlreadyStartedError:
            logger.info(
                "Case-triggered run already started",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
                wf_exec_id=wf_exec_id,
            )
        except PERMANENT_START_ERRORS as e:
            logger.error(
                "Dropping case-triggered run that cannot start",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
                wf_exec_id=wf_exec_id,
                error_type=type(e).__name__,
                error=str(e),
            )

    async def _disable_invalid_trigger(
        self,
        session: AsyncSession,
        trigger: CaseTrigger,
        event: CaseEvent,
        *,
        reason: str,
    ) -> None:
        logger.warning(
            "Disabling invalid case trigger",
            workflow_id=str(trigger.workflow_id),
            event_id=str(event.id),
            reason=reason,
        )
        trigger.status = "offline"
        session.add(trigger)
        await session.flush()

    async def _start_comment_workflow(
        self,
        session: AsyncSession,
        *,
        role: Role,
        case: Case,
        event: CaseEvent,
        trigger: CaseCommentWorkflowTrigger,
    ) -> None:
        self._heartbeat()
        wf_id = WorkflowUUID.new(trigger.workflow_id)
        audit_role = (
            role.model_copy(
                update={
                    "user_id": trigger.triggered_by_user_id,
                    "service_id": trigger.triggered_by_service_id or role.service_id,
                }
            )
            if trigger.triggered_by_user_id is not None
            else None
        )
        defn = await WorkflowDefinitionsService(
            session, role=role
        ).get_definition_by_workflow_id(wf_id, load_relationships=False)
        dsl: DSLInput | None = None
        if defn and defn.content:
            try:
                dsl = DSLInput.model_validate(defn.content)
            except (ValidationError, TracecatDSLError):
                dsl = None
        service = await WorkflowExecutionsService.connect(role=role)
        if defn is None or dsl is None:
            logger.warning(
                "Comment workflow definition missing or invalid",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
            )
            await self._fail_comment_workflow_unless_started(
                session, service, audit_role=audit_role, case=case, trigger=trigger
            )
            return

        try:
            await service.create_workflow_execution_wait_for_start(
                dsl=dsl,
                wf_id=wf_id,
                wf_exec_id=trigger.wf_exec_id,
                payload=build_comment_workflow_payload(
                    case=case, event=event, trigger=trigger
                ),
                trigger_type=TriggerType.CASE,
                registry_lock=RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None,
            )
        except WorkflowAlreadyStartedError:
            logger.info(
                "Comment workflow already started",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
                wf_exec_id=trigger.wf_exec_id,
            )
            # The ID may be held by a tombstone from an earlier attempt that
            # lost its commit, rather than by the run itself.
            if not await self._fail_comment_workflow_unless_started(
                session, service, audit_role=audit_role, case=case, trigger=trigger
            ):
                return
        except PERMANENT_START_ERRORS as e:
            logger.error(
                "Comment workflow cannot start",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
                wf_exec_id=trigger.wf_exec_id,
                error_type=type(e).__name__,
                error=str(e),
            )
            await self._fail_comment_workflow_unless_started(
                session, service, audit_role=audit_role, case=case, trigger=trigger
            )
            return
        await self._audit_comment_workflow(
            session,
            audit_role=audit_role,
            status=AuditEventStatus.SUCCESS,
            case=case,
            trigger=trigger,
        )

    async def _fail_comment_workflow_unless_started(
        self,
        session: AsyncSession,
        service: WorkflowExecutionsService,
        *,
        audit_role: Role | None,
        case: Case,
        trigger: CaseCommentWorkflowTrigger,
    ) -> bool:
        """Mark a picked workflow failed, unless an earlier attempt started it.

        An earlier attempt of this row may have started the run and then lost
        its commit. Settling the ID tells the two apart, and makes sure a run
        that never started cannot start later. Returns whether the run started.
        """
        match await service.settle_lost_start(trigger.wf_exec_id):
            case "started":
                return True
            case "unknown":
                raise CaseTriggerStartUnknownError(trigger.wf_exec_id)
            case "not_started":
                await self._fail_comment_workflow(
                    session, audit_role=audit_role, case=case, trigger=trigger
                )
                return False

    async def _fail_comment_workflow(
        self,
        session: AsyncSession,
        *,
        audit_role: Role | None,
        case: Case,
        trigger: CaseCommentWorkflowTrigger,
    ) -> None:
        await session.execute(
            update(CaseComment)
            .where(
                CaseComment.workspace_id == case.workspace_id,
                CaseComment.id == trigger.comment_id,
            )
            .values(workflow_status=CaseCommentWorkflowStatus.FAILED.value)
        )
        await self._audit_comment_workflow(
            session,
            audit_role=audit_role,
            status=AuditEventStatus.FAILURE,
            case=case,
            trigger=trigger,
        )

    async def _audit_comment_workflow(
        self,
        session: AsyncSession,
        *,
        audit_role: Role | None,
        status: AuditEventStatus,
        case: Case,
        trigger: CaseCommentWorkflowTrigger,
    ) -> None:
        if audit_role is None:
            return
        async with AuditService.with_session(role=audit_role, session=session) as svc:
            await svc.create_event(
                resource_type="workflow_execution",
                action="create",
                resource_id=trigger.workflow_id,
                status=status,
                data={
                    "case_id": str(case.id),
                    "comment_id": str(trigger.comment_id),
                    "parent_id": str(trigger.parent_id)
                    if trigger.parent_id is not None
                    else None,
                    "workflow_id": str(trigger.workflow_id),
                    "wf_exec_id": trigger.wf_exec_id,
                    "trigger_type": "case",
                },
            )
