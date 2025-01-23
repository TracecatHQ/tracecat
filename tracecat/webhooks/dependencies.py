from typing import Annotated, cast

import orjson
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import col, select

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Webhook, WorkflowDefinition
from tracecat.dsl.models import TriggerInputs
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.types.auth import Role


async def validate_incoming_webhook(
    workflow_id: AnyWorkflowIDPath, secret: str, request: Request
) -> WorkflowDefinition:
    """Validate incoming webhook request.

    NOte: The webhook ID here is the workflow ID.
    """
    legacy_wf_id = workflow_id.to_legacy()
    async with get_async_session_context_manager() as session:
        result = await session.exec(
            select(Webhook).where(Webhook.workflow_id == legacy_wf_id)
        )
        try:
            # One webhook per workflow
            webhook = result.one()
        except NoResultFound as e:
            logger.opt(exception=e).error("Webhook does not exist", error=e)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request.",
            ) from e

        if secret != webhook.secret:
            logger.error("Secret does not match")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request",
            )

        # If we're here, the webhook has been validated
        if webhook.status == "offline":
            logger.error("Webhook is offline")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook is offline",
            )

        if webhook.method.lower() != request.method.lower():
            logger.error("Method does not match")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request method not allowed",
            )

        # Reaching here means the webhook is online and connected to an entrypoint

        # Match the webhook id with the workflow id and get the latest version
        # of the workflow defitniion.
        result = await session.exec(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == legacy_wf_id)
            .order_by(col(WorkflowDefinition.version).desc())
        )
        try:
            defn = result.first()
            if not defn:
                raise NoResultFound(
                    "No workflow definition found for workflow ID. Please commit your changes to the workflow and try again."
                )
        except NoResultFound as e:
            # No workflow associated with the webhook
            logger.error(str(e), error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
            ) from e

        # Check if the workflow is active

        if defn.workflow.status == "offline":
            logger.error("Workflow is offline")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow is offline",
            )

        # If we are here, all checks have passed
        validated_defn = WorkflowDefinition.model_validate(defn)
        ctx_role.set(
            Role(
                type="service",
                workspace_id=validated_defn.owner_id,
                service_id="tracecat-runner",
            )
        )
        return validated_defn


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

    match content_type:
        case "application/x-ndjson" | "application/jsonlines" | "application/jsonl":
            # Newline delimited json
            try:
                lines = body.splitlines()
                return cast(TriggerInputs, [orjson.loads(line) for line in lines])
            except orjson.JSONDecodeError as e:
                logger.error("Failed to parse ndjson payload", error=e)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid ndjson payload",
                ) from e
        case "application/x-www-form-urlencoded":
            try:
                form_data = await request.form()
                return cast(TriggerInputs, dict(form_data))
            except Exception as e:
                logger.error("Failed to parse form data payload", error=e)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid form data payload",
                ) from e
        case _:
            # Interpret everything else as json
            try:
                return cast(TriggerInputs, orjson.loads(body))
            except orjson.JSONDecodeError as e:
                logger.error("Failed to parse json payload", error=e)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid json payload",
                ) from e


PayloadDep = Annotated[TriggerInputs | None, Depends(parse_webhook_payload)]


WorkflowDefinitionFromWebhook = Annotated[
    WorkflowDefinition, Depends(validate_incoming_webhook)
]
