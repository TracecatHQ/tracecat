from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from tenacity import RetryError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Case, CaseEvent, CaseTrigger, Workspace
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.redis.client import RedisClient, get_redis_client
from tracecat.registry.lock.types import RegistryLock
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

        async with get_async_session_context_manager() as session:
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
            if not triggers:
                return True

            case_tag_refs = {tag.ref for tag in case.tags}
            role = await self._get_service_role(session, workspace_uuid)

            should_ack = True
            for trigger in triggers:
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

            return should_ack

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

        created_at = event.created_at or datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        payload: dict[str, Any] = {
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

        workflow_service.create_workflow_execution_nowait(
            dsl=dsl,
            wf_id=wf_id,
            payload=payload,
            trigger_type=TriggerType.CASE,
            registry_lock=RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None,
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
