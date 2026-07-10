from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.audit.types import AuditAction
from tracecat.auth.types import Role
from tracecat.db.models import Webhook, WebhookApiKey
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import WorkflowID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.webhooks.schemas import WebhookCreate, WebhookUpdate

type WebhookAuditData = dict[str, object]
type WebhookAuditDataFactory = Callable[[], WebhookAuditData]


async def get_webhook(
    session: AsyncSession,
    workspace_id,
    workflow_id: WorkflowID,
) -> Webhook | None:
    statement = select(Webhook).where(
        Webhook.workspace_id == workspace_id,
        Webhook.workflow_id == workflow_id,
    )
    result = await session.execute(statement)
    return result.scalars().first()


def webhook_config_audit_data(
    params: WebhookCreate | WebhookUpdate, *, include_defaults: bool = False
) -> WebhookAuditData:
    fields = {
        key: value
        for key, value in params.model_dump(exclude_unset=not include_defaults).items()
        if value is not None
    }
    data: WebhookAuditData = {"fields": sorted(fields)}
    if status_value := fields.get("status"):
        data["status"] = status_value
    if "methods" in fields:
        data["method_count"] = len(cast(list[str], fields["methods"]))
    if "allowlisted_cidrs" in fields:
        data["allowlisted_cidr_count"] = len(
            cast(list[str], fields["allowlisted_cidrs"])
        )
    if "include_headers" in fields:
        data["include_headers"] = bool(fields["include_headers"])
    return data


def webhook_api_key_audit_data(
    operation: str,
    api_key: WebhookApiKey | None = None,
    *,
    include_presence: bool = True,
) -> WebhookAuditData:
    data: WebhookAuditData = {"key_operation": operation}
    if include_presence:
        data["key_present"] = api_key is not None
    if api_key is not None and api_key.id is not None:
        data["api_key_id"] = str(api_key.id)
    return data


def _db_error_code(exc: BaseException) -> str | None:
    for candidate in (getattr(exc, "orig", None), getattr(exc, "__cause__", None), exc):
        code = getattr(candidate, "sqlstate", None) or getattr(
            candidate, "pgcode", None
        )
        if code is not None:
            return str(code)
    return None


async def emit_webhook_audit_event(
    role: Role,
    session: AsyncSession,
    *,
    workflow_id: WorkflowID,
    action: AuditAction,
    status: AuditEventStatus,
    webhook_id: object | None = None,
    data: WebhookAuditData | None = None,
    fresh_session: bool = False,
) -> None:
    try:
        async with AuditService.with_session(
            role,
            session=None if fresh_session else session,
        ) as svc:
            await svc.create_event(
                resource_type="webhook",
                resource_id=str(webhook_id) if webhook_id is not None else None,
                parent_resource_type="workflow",
                parent_resource_id=str(workflow_id),
                action=action,
                status=status,
                data=data,
            )
    except Exception as exc:
        logger.warning(
            "Webhook audit log failed",
            error_type=type(exc).__name__,
            db_error_code=_db_error_code(exc),
        )


def _resolve_audit_data(
    data: WebhookAuditData | WebhookAuditDataFactory | None,
) -> WebhookAuditData | None:
    return data() if callable(data) else data


@asynccontextmanager
async def webhook_key_audit_span(
    role: Role,
    session: AsyncSession,
    *,
    workflow_id: WorkflowID,
    webhook_id: object | None,
    action: AuditAction,
    attempt_data: WebhookAuditData | WebhookAuditDataFactory,
    terminal_data: WebhookAuditData | WebhookAuditDataFactory,
) -> AsyncGenerator[None, None]:
    await emit_webhook_audit_event(
        role,
        session,
        workflow_id=workflow_id,
        action=action,
        status=AuditEventStatus.ATTEMPT,
        webhook_id=webhook_id,
        data=_resolve_audit_data(attempt_data),
    )
    try:
        yield
    except Exception:
        await emit_webhook_audit_event(
            role,
            session,
            workflow_id=workflow_id,
            action=action,
            status=AuditEventStatus.FAILURE,
            webhook_id=webhook_id,
            data=_resolve_audit_data(terminal_data),
            fresh_session=True,
        )
        raise
    await emit_webhook_audit_event(
        role,
        session,
        workflow_id=workflow_id,
        action=action,
        status=AuditEventStatus.SUCCESS,
        webhook_id=webhook_id,
        data=_resolve_audit_data(terminal_data),
    )


class WebhookConfigService(BaseWorkspaceService):
    service_name = "webhook_config"

    async def create_webhook(
        self,
        workflow_id: WorkflowID,
        params: WebhookCreate,
    ) -> Webhook:
        audit_data = webhook_config_audit_data(params, include_defaults=True)
        webhook = Webhook(
            workspace_id=self.workspace_id,
            methods=cast(list[str], params.methods),
            workflow_id=workflow_id,
            status=params.status,
            allowlisted_cidrs=params.allowlisted_cidrs,
            include_headers=params.include_headers,
        )
        try:
            self.session.add(webhook)
            await self.session.commit()
            await self.session.refresh(webhook)
        except Exception:
            await emit_webhook_audit_event(
                self.role,
                self.session,
                workflow_id=workflow_id,
                action="create",
                status=AuditEventStatus.FAILURE,
                webhook_id=webhook.id,
                data=audit_data,
                fresh_session=True,
            )
            raise
        await emit_webhook_audit_event(
            self.role,
            self.session,
            workflow_id=workflow_id,
            action="create",
            status=AuditEventStatus.SUCCESS,
            webhook_id=webhook.id,
            data=audit_data,
        )
        return webhook

    async def update_webhook(
        self,
        workflow_id: WorkflowID,
        params: WebhookUpdate,
    ) -> Webhook:
        audit_data = webhook_config_audit_data(params)
        webhook: Webhook | None = None
        try:
            webhook = await get_webhook(
                self.session,
                workspace_id=self.workspace_id,
                workflow_id=workflow_id,
            )
            if webhook is None:
                raise TracecatNotFoundError("Webhook not found")
            for key, value in params.model_dump(exclude_unset=True).items():
                # Safety: params have been validated by WebhookUpdate.
                setattr(webhook, key, value)
            self.session.add(webhook)
            await self.session.commit()
            await self.session.refresh(webhook)
        except Exception:
            await emit_webhook_audit_event(
                self.role,
                self.session,
                workflow_id=workflow_id,
                action="update",
                status=AuditEventStatus.FAILURE,
                webhook_id=webhook.id if webhook is not None else None,
                data=audit_data,
                fresh_session=True,
            )
            raise
        await emit_webhook_audit_event(
            self.role,
            self.session,
            workflow_id=workflow_id,
            action="update",
            status=AuditEventStatus.SUCCESS,
            webhook_id=webhook.id,
            data=audit_data,
        )
        return webhook
