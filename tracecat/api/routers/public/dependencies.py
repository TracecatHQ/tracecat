import orjson
from fastapi import (
    HTTPException,
    Request,
    status,
)
from sqlalchemy.exc import NoResultFound
from sqlmodel import select

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Webhook, WorkflowDefinition
from tracecat.logging import logger
from tracecat.parse import parse_child_webhook
from tracecat.types.api import ServiceCallbackAction
from tracecat.types.auth import Role


async def handle_service_callback(
    request: Request, service: str
) -> ServiceCallbackAction | None:
    if service == "slack":
        # if (
        #     request.headers["user-agent"]
        #     != "Slackbot 1.0 (+https://api.slack.com/robots)"
        # ):
        #     raise HTTPException(
        #         status_code=status.HTTP_400_BAD_REQUEST,
        #         detail="Invalid User-Agent",
        #     )
        # NOTE: This coroutine can only be consumed once!
        form_data = await request.form()
        json_payload = form_data.get("payload")
        payload = orjson.loads(json_payload)
        # Extract out the webhook
        if (actions := payload.get("actions")) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack callback: Invalid payload",
            )
        logger.info("Received slack action", actions=actions)
        if len(actions) < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack callback: No actions",
            )
        action = actions[0]
        match action:
            case {
                "type": "static_select",
                "action_id": url,
                "selected_option": {"value": kv_params},
            }:
                # e.g.
                # {
                #     "type": "static_select",
                #     "action_id": "...",
                #     "block_id": "nMMIK",
                #     "selected_option": {
                #         "text": {
                #             "type": "plain_text",
                #             "text": "True Positive",
                #             "emoji": True,
                #         },
                #         "value": '["closed", "true_positive"]',
                #     },
                #     "action_ts": "1719281272.742854",
                # }

                logger.info("Matched static select action", action=action)
                child_wh = parse_child_webhook(url, [kv_params])

                if not child_wh:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid child webhook URL",
                    )
                return ServiceCallbackAction(
                    action="webhook",
                    payload=child_wh["payload"],
                    metadata={"path": child_wh["path"], "secret": child_wh["secret"]},
                )

            case {"type": action_type}:
                logger.error(
                    "Invalid action type", action_type=action_type, action=action
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid action type {action_type}",
                )
            case _:
                logger.error("Invalid action", action=action)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid action",
                )
    return None


async def validate_incoming_webhook(
    path: str, secret: str, request: Request, *, validate_method: bool = True
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

        if validate_method and webhook.method.lower() != request.method.lower():
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
