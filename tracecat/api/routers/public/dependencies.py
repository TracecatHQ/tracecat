from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import select

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Webhook, WorkflowDefinition
from tracecat.logger import logger
from tracecat.types.auth import Role


async def validate_incoming_webhook(
    path: str, secret: str, request: Request
) -> WorkflowDefinition:
    """Validate incoming webhook request.

    NOte: The webhook ID here is the workflow ID.
    """
    logger.info("Validating incoming webhook", path=path, secret=secret)
    async with get_async_session_context_manager() as session:
        result = await session.exec(select(Webhook).where(Webhook.workflow_id == path))
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
            .where(WorkflowDefinition.workflow_id == path)
            .order_by(WorkflowDefinition.version.desc())
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


WorkflowDefinitionFromWebhook = Annotated[
    WorkflowDefinition, Depends(validate_incoming_webhook)
]
