from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from pydantic import ValidationError
from redis.exceptions import ResponseError
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from temporalio.exceptions import WorkflowAlreadyStartedError
from tenacity import RetryError

from tracecat import config
from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.schemas import CaseCommentWorkflowStatus
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Case, CaseComment, CaseEvent, CaseTrigger, Workspace
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client
from tracecat.registry.lock.types import RegistryLock
from tracecat.workflow.case_triggers.schemas import (
    normalize_case_trigger_event_filters,
)
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService


class CaseTriggerConsumer:
    """Consume case events and dispatch workflows based on configured triggers."""

    def __init__(
        self, client: RedisClient, *, consumer_name: str | None = None
    ) -> None:
        self.client = client
        self.stream_key = config.TRACECAT__CASE_TRIGGERS_STREAM_KEY
        self.group = config.TRACECAT__CASE_TRIGGERS_GROUP
        self.block_ms = config.TRACECAT__CASE_TRIGGERS_BLOCK_MS
        self.batch = config.TRACECAT__CASE_TRIGGERS_BATCH
        self.claim_idle_ms = config.TRACECAT__CASE_TRIGGERS_CLAIM_IDLE_MS
        self.dedup_ttl = config.TRACECAT__CASE_TRIGGERS_DEDUP_TTL_SECONDS
        self.lock_ttl = config.TRACECAT__CASE_TRIGGERS_LOCK_TTL_SECONDS
        self.consumer_name = consumer_name or f"{socket.gethostname()}:{os.getpid()}"
        self._workspace_role_cache: dict[uuid.UUID, Role] = {}
        self._pending_check_interval = max(self.claim_idle_ms / 1000.0, 30.0)

    async def run(self) -> None:
        if not config.TRACECAT__CASE_TRIGGERS_ENABLED:
            logger.info("Case triggers disabled; skipping consumer")
            return

        await self._ensure_group()
        logger.info(
            "Case trigger consumer started",
            stream_key=self.stream_key,
            group=self.group,
            consumer=self.consumer_name,
        )
        last_pending_check = monotonic()
        try:
            while True:
                try:
                    messages = await self.client.xreadgroup(
                        group_name=self.group,
                        consumer_name=self.consumer_name,
                        streams={self.stream_key: ">"},
                        count=self.batch,
                        block=self.block_ms,
                    )
                except (ResponseError, RetryError) as e:
                    if self._is_nogroup_error(e):
                        logger.warning(
                            "Redis case trigger stream/group missing; recreating",
                            stream_key=self.stream_key,
                            group=self.group,
                            error=str(e),
                        )
                        await self._ensure_group()
                        continue
                    raise
                if messages:
                    for _stream, entries in messages:
                        for message_id, fields in entries:
                            await self._handle_message(message_id, fields)
                else:
                    now = monotonic()
                    if now - last_pending_check >= self._pending_check_interval:
                        await self._claim_idle_messages()
                        last_pending_check = now
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("Case trigger consumer cancelled")
            raise
        except Exception as e:
            logger.error("Case trigger consumer stopped due to error", error=str(e))
            raise

    def _is_nogroup_error(self, error: Exception) -> bool:
        if isinstance(error, ResponseError):
            return "NOGROUP" in str(error)
        if isinstance(error, RetryError):
            last_exc = error.last_attempt.exception()
            return isinstance(last_exc, ResponseError) and "NOGROUP" in str(last_exc)
        return False

    async def _ensure_group(self) -> None:
        try:
            await self.client.xgroup_create(
                self.stream_key,
                self.group,
                id="$",
                ignore_busygroup=True,
            )
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                return
            raise

    async def _handle_message(self, message_id: str, fields: dict[str, str]) -> None:
        should_ack = False
        try:
            should_ack = await self._process_message(fields)
        except Exception as e:
            logger.error(
                "Failed to process case trigger message",
                message_id=message_id,
                error=str(e),
            )
        if should_ack:
            await self.client.xack(self.stream_key, self.group, [message_id])

    async def _process_message(self, fields: dict[str, str]) -> bool:
        event_id = fields.get("event_id")
        case_id = fields.get("case_id")
        workspace_id = fields.get("workspace_id")
        event_type = fields.get("event_type")
        if not (event_id and case_id and workspace_id and event_type):
            logger.warning("Malformed case trigger message", fields=fields)
            return True

        try:
            event_uuid = uuid.UUID(event_id)
            case_uuid = uuid.UUID(case_id)
            workspace_uuid = uuid.UUID(workspace_id)
        except ValueError:
            logger.warning("Invalid IDs in case trigger message", fields=fields)
            return True

        async with get_async_session_bypass_rls_context_manager() as session:
            event = await self._load_event(
                session, event_uuid, case_uuid, workspace_uuid
            )
            if event is None:
                return True

            wf_exec_id = None
            if isinstance(event.data, dict):
                wf_exec_id = event.data.get("wf_exec_id")
            if wf_exec_id:
                logger.debug(
                    "Skipping workflow-originated case event",
                    event_id=event_id,
                    wf_exec_id=wf_exec_id,
                )
                return True

            case = await self._load_case(session, case_uuid, workspace_uuid)
            if case is None:
                return True

            triggers = await self._load_triggers(session, workspace_uuid, event_type)
            case_tag_refs = {tag.ref for tag in case.tags}
            role = await self._get_service_role(session, workspace_uuid)
            explicit_workflow_id = self._parse_optional_uuid(fields.get("workflow_id"))
            explicit_comment_id = self._parse_optional_uuid(fields.get("comment_id"))

            should_ack = True
            if explicit_workflow_id is not None:
                explicit_processed = await self._process_explicit_workflow(
                    session=session,
                    role=role,
                    case=case,
                    event=event,
                    fields=fields,
                    event_id=event_id,
                    workflow_id=explicit_workflow_id,
                    comment_id=explicit_comment_id,
                )
                should_ack = should_ack and explicit_processed

            for trigger in triggers:
                if (
                    explicit_workflow_id is not None
                    and trigger.workflow_id == explicit_workflow_id
                ):
                    continue
                if not self._matches_event_filters(trigger, event):
                    continue
                if trigger.tag_filters:
                    if not case_tag_refs.intersection(trigger.tag_filters):
                        continue
                done_key = f"case-trigger:done:{event_id}:{trigger.workflow_id}"
                lock_key = f"case-trigger:lock:{event_id}:{trigger.workflow_id}"

                if await self.client.exists(done_key):
                    continue

                lock_acquired = await self.client.set_if_not_exists(
                    lock_key,
                    value="1",
                    expire_seconds=self.lock_ttl,
                )
                if not lock_acquired:
                    should_ack = False
                    continue

                try:
                    dispatched = await self._dispatch_workflow(
                        session=session,
                        role=role,
                        trigger=trigger,
                        case=case,
                        event=event,
                    )
                    if not dispatched:
                        should_ack = False
                        continue

                    await self.client.set(
                        done_key,
                        value="1",
                        expire_seconds=self.dedup_ttl,
                    )
                except Exception as e:
                    should_ack = False
                    logger.error(
                        "Failed to dispatch workflow for case trigger",
                        error=str(e),
                        workflow_id=str(trigger.workflow_id),
                        event_id=event_id,
                    )
                finally:
                    await self.client.delete(lock_key)

            await session.commit()
            return should_ack

    def _parse_optional_uuid(self, value: str | None) -> uuid.UUID | None:
        if not value:
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            return None

    def _matches_event_filters(self, trigger: CaseTrigger, event: CaseEvent) -> bool:
        try:
            event_filters = normalize_case_trigger_event_filters(
                trigger.event_filters or {},
                event_types=getattr(trigger, "event_types", None),
            )
        except (ValidationError, ValueError) as e:
            logger.warning(
                "Skipping case trigger with invalid event filters",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
                event_type=event.type,
                error=str(e),
            )
            return False

        allowed_values = event_filters.values_for(event.type)
        if not allowed_values:
            return True
        if not isinstance(event.data, dict):
            return False

        match event.data:
            case {"new": str(new_value)}:
                return new_value in allowed_values
            case _:
                return False

    async def _process_explicit_workflow(
        self,
        *,
        session,
        role: Role,
        case: Case,
        event: CaseEvent,
        fields: dict[str, str],
        event_id: str,
        workflow_id: uuid.UUID,
        comment_id: uuid.UUID | None,
    ) -> bool:
        done_key = f"case-trigger:done:{event_id}:{workflow_id}"
        lock_key = f"case-trigger:lock:{event_id}:{workflow_id}"

        if await self.client.exists(done_key):
            return True

        lock_acquired = await self.client.set_if_not_exists(
            lock_key,
            value="1",
            expire_seconds=self.lock_ttl,
        )
        if not lock_acquired:
            return False

        try:
            dispatched = await self._dispatch_selected_workflow(
                session=session,
                role=role,
                workflow_id=workflow_id,
                case=case,
                event=event,
                fields=fields,
                comment_id=comment_id,
            )
            await session.commit()
            await self.client.set(
                done_key,
                value="1",
                expire_seconds=self.dedup_ttl,
            )
            return dispatched
        finally:
            await self.client.delete(lock_key)

    async def _load_event(
        self,
        session,
        event_id: uuid.UUID,
        case_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> CaseEvent | None:
        result = await session.execute(
            select(CaseEvent).where(
                CaseEvent.id == event_id,
                CaseEvent.case_id == case_id,
                CaseEvent.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_case(
        self, session, case_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> Case | None:
        result = await session.execute(
            select(Case)
            .options(selectinload(Case.tags))
            .where(
                Case.id == case_id,
                Case.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_triggers(
        self, session, workspace_id: uuid.UUID, event_type: str
    ) -> list[CaseTrigger]:
        stmt = select(CaseTrigger).where(
            CaseTrigger.workspace_id == workspace_id,
            CaseTrigger.status == "online",
            CaseTrigger.event_types.contains([event_type]),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _get_service_role(self, session, workspace_id: uuid.UUID) -> Role:
        if workspace_id in self._workspace_role_cache:
            return self._workspace_role_cache[workspace_id]

        result = await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalar_one()
        role = Role(
            type="service",
            workspace_id=workspace_id,
            organization_id=workspace.organization_id,
            user_id=None,
            service_id="tracecat-case-triggers",
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-case-triggers"],
        )
        self._workspace_role_cache[workspace_id] = role
        return role

    def _get_audit_role(
        self,
        role: Role,
        *,
        triggered_by_user_id: uuid.UUID | None,
        triggered_by_service_id: str | None,
    ) -> Role | None:
        if triggered_by_user_id is None:
            return None
        return role.model_copy(
            update={
                "user_id": triggered_by_user_id,
                "service_id": triggered_by_service_id or role.service_id,
            }
        )

    async def _audit_workflow_execution_event(
        self,
        *,
        session,
        role: Role | None,
        status: AuditEventStatus,
        workflow_id: uuid.UUID,
        case_id: uuid.UUID,
        comment_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
        wf_exec_id: str | None,
    ) -> None:
        if role is None:
            return
        async with AuditService.with_session(role=role, session=session) as svc:
            await svc.create_event(
                resource_type="workflow_execution",
                action="create",
                resource_id=workflow_id,
                status=status,
                data={
                    "case_id": str(case_id),
                    "comment_id": str(comment_id) if comment_id is not None else None,
                    "parent_id": str(parent_id) if parent_id is not None else None,
                    "workflow_id": str(workflow_id),
                    "wf_exec_id": wf_exec_id,
                    "trigger_type": "case",
                },
            )

    async def _set_comment_workflow_status(
        self,
        session,
        *,
        workspace_id: uuid.UUID,
        comment_id: uuid.UUID | None,
        status: CaseCommentWorkflowStatus,
    ) -> None:
        if comment_id is None:
            return
        await session.execute(
            update(CaseComment)
            .where(
                CaseComment.workspace_id == workspace_id,
                CaseComment.id == comment_id,
            )
            .values(workflow_status=status.value)
        )

    def _build_case_trigger_payload(
        self,
        *,
        case: Case,
        event: CaseEvent,
    ) -> dict[str, Any]:
        created_at = event.created_at or datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return {
            "case_id": str(case.id),
            "event": {
                "id": str(event.id),
                "type": event.type.value
                if hasattr(event.type, "value")
                else event.type,
                "data": event.data,
                "created_at": created_at.isoformat(),
                "user_id": str(event.user_id) if event.user_id else None,
                "wf_exec_id": event.data.get("wf_exec_id") if event.data else None,
            },
            "tags": [
                {
                    "id": str(tag.id),
                    "ref": tag.ref,
                    "name": tag.name,
                    "color": tag.color,
                }
                for tag in case.tags
            ],
            "workspace_id": str(case.workspace_id),
        }

    def _build_explicit_comment_payload(
        self,
        *,
        case: Case,
        event: CaseEvent,
        fields: dict[str, str],
        comment_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        created_at = event.created_at or datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        triggered_by_user_id = fields.get("triggered_by_user_id")
        triggered_by_service_id = fields.get("triggered_by_service_id")
        parent_id = fields.get("parent_id")
        comment = fields.get("comment") or fields.get("text", "")
        thread_root_id = parent_id or (
            str(comment_id) if comment_id is not None else None
        )

        return {
            "case_id": str(case.id),
            "comment": comment,
            "comment_id": str(comment_id) if comment_id is not None else None,
            "parent_id": parent_id,
            "thread_root_id": thread_root_id,
            "is_reply": parent_id is not None,
            "text": comment,
            "workspace_id": str(case.workspace_id),
            "triggered_by": {
                "type": fields.get("triggered_by_type") or "service",
                "user_id": triggered_by_user_id,
                "service_id": triggered_by_service_id,
            },
            "event": {
                "id": str(event.id),
                "type": event.type.value
                if hasattr(event.type, "value")
                else event.type,
                "created_at": created_at.isoformat(),
                "user_id": str(event.user_id) if event.user_id else None,
            },
            "tags": [
                {
                    "id": str(tag.id),
                    "ref": tag.ref,
                    "name": tag.name,
                    "color": tag.color,
                }
                for tag in case.tags
            ],
        }

    async def _dispatch_workflow(
        self,
        *,
        session,
        role: Role,
        trigger: CaseTrigger,
        case: Case,
        event: CaseEvent,
    ) -> bool:
        defn_service = WorkflowDefinitionsService(session, role=role)
        wf_id = WorkflowUUID.new(trigger.workflow_id)
        defn = await defn_service.get_definition_by_workflow_id(wf_id)
        if not defn:
            logger.warning(
                "No workflow definition found for workflow",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
            )
            return False
        if not defn.content:
            logger.warning(
                "Workflow definition content missing",
                workflow_id=str(trigger.workflow_id),
                event_id=str(event.id),
            )
            return False

        dsl = DSLInput.model_validate(defn.content)
        workflow_service = await WorkflowExecutionsService.connect(role=role)

        workflow_service.create_workflow_execution_nowait(
            dsl=dsl,
            wf_id=wf_id,
            payload=self._build_case_trigger_payload(case=case, event=event),
            trigger_type=TriggerType.CASE,
            registry_lock=RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None,
        )
        return True

    async def _dispatch_selected_workflow(
        self,
        *,
        session,
        role: Role,
        workflow_id: uuid.UUID,
        case: Case,
        event: CaseEvent,
        fields: dict[str, str],
        comment_id: uuid.UUID | None,
    ) -> bool:
        defn_service = WorkflowDefinitionsService(session, role=role)
        wf_id = WorkflowUUID.new(workflow_id)
        defn = await defn_service.get_definition_by_workflow_id(wf_id)
        wf_exec_id = fields.get("wf_exec_id")
        parent_id = self._parse_optional_uuid(fields.get("parent_id"))
        audit_role = self._get_audit_role(
            role,
            triggered_by_user_id=self._parse_optional_uuid(
                fields.get("triggered_by_user_id")
            ),
            triggered_by_service_id=fields.get("triggered_by_service_id"),
        )

        if not wf_exec_id:
            await self._set_comment_workflow_status(
                session,
                workspace_id=case.workspace_id,
                comment_id=comment_id,
                status=CaseCommentWorkflowStatus.FAILED,
            )
            await self._audit_workflow_execution_event(
                session=session,
                role=audit_role,
                status=AuditEventStatus.FAILURE,
                workflow_id=workflow_id,
                case_id=case.id,
                comment_id=comment_id,
                parent_id=parent_id,
                wf_exec_id=None,
            )
            return True
        if not defn or not defn.content:
            logger.warning(
                "Explicit case comment workflow definition missing",
                workflow_id=str(workflow_id),
                event_id=str(event.id),
            )
            await self._set_comment_workflow_status(
                session,
                workspace_id=case.workspace_id,
                comment_id=comment_id,
                status=CaseCommentWorkflowStatus.FAILED,
            )
            await self._audit_workflow_execution_event(
                session=session,
                role=audit_role,
                status=AuditEventStatus.FAILURE,
                workflow_id=workflow_id,
                case_id=case.id,
                comment_id=comment_id,
                parent_id=parent_id,
                wf_exec_id=wf_exec_id,
            )
            return True

        dsl = DSLInput.model_validate(defn.content)
        workflow_service = await WorkflowExecutionsService.connect(role=role)

        try:
            await workflow_service.create_workflow_execution_wait_for_start(
                dsl=dsl,
                wf_id=wf_id,
                wf_exec_id=wf_exec_id,
                payload=self._build_explicit_comment_payload(
                    case=case,
                    event=event,
                    fields=fields,
                    comment_id=comment_id,
                ),
                trigger_type=TriggerType.CASE,
                registry_lock=RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None,
            )
        except WorkflowAlreadyStartedError:
            logger.info(
                "Explicit case comment workflow already started; treating replay as success",
                workflow_id=str(workflow_id),
                event_id=str(event.id),
                wf_exec_id=wf_exec_id,
            )
        except Exception as e:
            logger.error(
                "Failed to dispatch explicit case comment workflow",
                error=str(e),
                workflow_id=str(workflow_id),
                event_id=str(event.id),
            )
            await self._set_comment_workflow_status(
                session,
                workspace_id=case.workspace_id,
                comment_id=comment_id,
                status=CaseCommentWorkflowStatus.FAILED,
            )
            await self._audit_workflow_execution_event(
                session=session,
                role=audit_role,
                status=AuditEventStatus.FAILURE,
                workflow_id=workflow_id,
                case_id=case.id,
                comment_id=comment_id,
                parent_id=parent_id,
                wf_exec_id=wf_exec_id,
            )
            return True

        await self._audit_workflow_execution_event(
            session=session,
            role=audit_role,
            status=AuditEventStatus.SUCCESS,
            workflow_id=workflow_id,
            case_id=case.id,
            comment_id=comment_id,
            parent_id=parent_id,
            wf_exec_id=wf_exec_id,
        )
        return True

    async def _claim_idle_messages(self) -> None:
        pending = await self.client.xpending_range(
            self.stream_key,
            self.group,
            min_id="-",
            max_id="+",
            count=self.batch,
            idle=self.claim_idle_ms,
        )
        if not pending:
            return

        message_ids: list[str] = []
        for entry in pending:
            msg_id = None
            if isinstance(entry, dict):
                msg_id = entry.get("message_id") or entry.get("id")
            else:
                msg_id = getattr(entry, "message_id", None)
            if msg_id:
                message_ids.append(msg_id)

        if not message_ids:
            return

        claimed = await self.client.xclaim(
            self.stream_key,
            self.group,
            self.consumer_name,
            self.claim_idle_ms,
            message_ids,
        )
        for message_id, fields in claimed:
            await self._handle_message(message_id, fields)


async def start_case_trigger_consumer() -> None:
    client = await get_redis_client()
    consumer = CaseTriggerConsumer(client)
    await consumer.run()
