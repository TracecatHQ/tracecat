from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING, Annotated, Any, cast

import orjson
from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from tracecat.auth.api_keys import verify_api_key
from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Webhook, WorkflowDefinition
from tracecat.dsl.schemas import TriggerInputs
from tracecat.ee.interactions.connectors import parse_slack_interaction_input
from tracecat.ee.interactions.enums import InteractionCategory
from tracecat.ee.interactions.schemas import InteractionInput
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.webhooks.schemas import NDJSON_CONTENT_TYPES
from tracecat.workflow.management.management import WorkflowsManagementService

if TYPE_CHECKING:
    from tracecat.dsl.common import DSLInput

API_KEY_HEADER = "x-tracecat-api-key"


def _extract_client_ip(request: Request) -> str | None:
    # Assume proxy middleware already validated/sanitized headers; treat
    # X-Forwarded-For as untrusted and rely on the resolved client host.
    if request.client:
        return request.client.host
    return None


def _ip_allowed(client_ip: str, cidr_list: Sequence[str]) -> bool:
    try:
        ip_obj = ip_address(client_ip)
    except ValueError:
        return False

    for cidr in cidr_list:
        try:
            network = ip_network(cidr, strict=False)
        except ValueError:
            logger.warning(
                "Invalid IP allowlist entry",
                entry=cidr,
            )
            continue
        if ip_obj in network:
            return True
    return False


async def validate_incoming_webhook(
    workflow_id: AnyWorkflowIDPath, secret: str, request: Request
) -> None:
    """Validate incoming webhook request.

    NOte: The webhook ID here is the workflow ID.
    """
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(Webhook).where(Webhook.workflow_id == workflow_id)
        )
        try:
            # One webhook per workflow
            webhook = result.scalar_one()
        except NoResultFound as e:
            logger.info("Webhook does not exist")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request",
            ) from e

        if not secrets.compare_digest(secret, webhook.secret):
            logger.warning("Secret does not match")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request",
            )

        # If we're here, the webhook has been validated
        if webhook.status == "offline":
            logger.info("Webhook is offline")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook is offline",
            )

        if request.method.lower() not in webhook.normalized_methods:
            logger.info("Method does not match")
            raise HTTPException(
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                detail="Request method not allowed",
            ) from None

        updated = False

        client_ip = _extract_client_ip(request)
        if webhook.allowlisted_cidrs:
            if client_ip is None:
                logger.warning(
                    "Request missing client IP while allowlist configured",
                    webhook_id=webhook.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Unauthorized webhook request",
                )
            if not _ip_allowed(client_ip, webhook.allowlisted_cidrs):
                logger.warning(
                    "Request IP not in allowlist",
                    webhook_id=webhook.id,
                    client_ip=client_ip,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Unauthorized webhook request",
                )

        api_key_header = request.headers.get(API_KEY_HEADER)
        if api_key_record := webhook.api_key:
            if api_key_record.revoked_at is not None:
                logger.warning(
                    "Rejected request using revoked webhook API key",
                    webhook_id=webhook.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized webhook request",
                )
            if not api_key_header:
                logger.warning(
                    "Missing API key for webhook with active key",
                    webhook_id=webhook.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized webhook request",
                )
            if not verify_api_key(
                api_key_header, api_key_record.salt, api_key_record.hashed
            ):
                logger.warning(
                    "Invalid API key presented",
                    webhook_id=webhook.id,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized webhook request",
                )
            api_key_record.last_used_at = datetime.now(UTC)
            updated = True
        elif api_key_header:
            logger.info(
                "API key provided for webhook without active key configuration",
                webhook_id=webhook.id,
            )

        if updated:
            session.add(webhook.api_key)
            await session.commit()

        ctx_role.set(
            Role(
                type="service",
                workspace_id=webhook.workspace_id,
                service_id="tracecat-runner",
                workspace_role=WorkspaceRole.EDITOR,
            )
        )


async def validate_workflow_definition(
    workflow_id: AnyWorkflowIDPath,
) -> WorkflowDefinition:
    # Reaching here means the webhook is online and connected to an entrypoint

    # Match the webhook id with the workflow id and get the latest version
    # of the workflow defitniion.
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == workflow_id)
            .order_by(WorkflowDefinition.version.desc())
            .limit(1)
        )
        defn = result.scalars().first()
        if not defn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No workflow definition found for workflow ID."
                " Please commit your changes to the workflow and try again.",
            )

        # If we are here, all checks have passed
        # XXX: This doesn't load the `workflow` relationship
        return defn


def parse_content_type(content_type: str) -> tuple[str, dict[str, str]]:
    """Parse Content-Type header into media type and parameters."""
    mime_type, *params = content_type.strip().split(";")
    metadata = {}
    for param in params:
        if "=" in param:
            key, value = param.strip().split("=", 1)
            metadata[key] = value.strip('"')
    return mime_type.strip(), metadata


async def parse_webhook_payload(
    request: Request,
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
) -> TriggerInputs | None:
    """
    Dependency to parse webhook payload based on Content-Type header.

    Args:
        request: FastAPI request object
        content_type: Content-Type header value

    Returns:
        Parsed payload as TriggerInputs or None if no payload
    """
    body = await request.body()
    if not body:
        return None

    # Parse the media type from Content-Type header
    mime_type = ""
    if content_type:
        mime_type, _ = parse_content_type(content_type)

    if mime_type in NDJSON_CONTENT_TYPES:
        # Newline delimited json
        try:
            lines = body.splitlines()
            result = [orjson.loads(line) for line in lines]
        except orjson.JSONDecodeError as e:
            logger.error("Failed to parse ndjson payload", error=e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ndjson payload",
            ) from e
    elif mime_type == "application/x-www-form-urlencoded":
        try:
            form_data = await request.form()
            result = dict(form_data)
        except Exception as e:
            logger.error("Failed to parse form data payload", error=e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid form data payload",
            ) from e
    else:
        # Interpret everything else as json
        try:
            result = orjson.loads(body)
        except orjson.JSONDecodeError as e:
            logger.error("Failed to parse json payload", error=e)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid json payload",
            ) from e

    return cast(TriggerInputs, result)


def parse_interaction_payload(
    category: InteractionCategory,
    payload: TriggerInputs | None = Depends(parse_webhook_payload),
) -> InteractionInput:
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing interaction payload",
        )
    logger.info("Parsed interaction payload", payload=payload)
    match category:
        case InteractionCategory.SLACK:
            # Specific steps to handle interactive Slack payloads
            # according to https://api.slack.com/interactivity/handling#payloads
            if not isinstance(payload, dict):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Malformed Slack interaction payload",
                )
            if "payload" not in payload:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing payload field in Slack interaction payload",
                )
            payload_obj = cast(dict[str, Any], orjson.loads(payload["payload"]))
            return parse_slack_interaction_input(payload_obj)
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid interaction category",
            )


PayloadDep = Annotated[TriggerInputs | None, Depends(parse_webhook_payload)]
ValidWorkflowDefinitionDep = Annotated[
    WorkflowDefinition, Depends(validate_workflow_definition)
]
"""Returns WorkflowDefinition that is not loaded with the `workflow` relationship"""


@dataclass
class DraftWorkflowContext:
    """Context for draft workflow execution containing DSL and registry lock."""

    dsl: DSLInput
    registry_lock: dict[str, str] | None


async def validate_draft_workflow(
    workflow_id: AnyWorkflowIDPath,
) -> DraftWorkflowContext:
    """Build DSL from the draft workflow (i.e. definition in the workflow table)."""

    role = ctx_role.get()
    async with WorkflowsManagementService.with_session(role=role) as mgmt_service:
        workflow = await mgmt_service.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )
        try:
            dsl: DSLInput = await mgmt_service.build_dsl_from_workflow(workflow)
            # Draft executions use None for registry_lock to resolve at runtime (latest registry)
            # This avoids stale locks when actions are edited in the UI
            return DraftWorkflowContext(dsl=dsl, registry_lock=None)
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "TracecatValidationError",
                    "message": str(e),
                    "detail": e.detail,
                },
            ) from e
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "ValidationError",
                    "message": str(e),
                    "detail": e.errors(),
                },
            ) from e


DraftWorkflowDep = Annotated[DraftWorkflowContext, Depends(validate_draft_workflow)]
"""Returns DraftWorkflowContext with DSL and registry_lock from the draft workflow"""
