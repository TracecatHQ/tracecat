import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import Engine
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.auth import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_engine
from tracecat.db.models import WorkflowDefinition
from tracecat.experimental.dsl.dispatcher import dispatch_workflow

engine: Engine = get_engine()


router = APIRouter()


def validate_incoming_webhook(webhook_id: str, secret: str) -> WorkflowDefinition:
    with Session(engine) as session:
        # Match the webhook id with the workflow id and get the latest version
        statement = (
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == webhook_id)
            .order_by(WorkflowDefinition.version.desc())
        )

        # statement = select(Webhook).where(Webhook.id == webhook_id)
        # NOTE: Probably best to first check Webhook.
        result = session.exec(statement)
        try:
            defn = result.first()
            if not defn:
                raise NoResultFound("No workflow found")
        except NoResultFound as e:
            # No workflow associated with the webhook
            logger.opt(exception=e).error("Webhook does not exist", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid webhook ID"
            ) from e
        if secret != "test-secret":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized webhook request",
            )

        # if (workflow := webhook.workflow) is None:
        #     raise HTTPException(
        #         status_code=status.HTTP_404_NOT_FOUND, detail="There is no workflow"
        #     )
        ctx_role.set(
            Role(type="service", user_id=defn.owner_id, service_id="tracecat-runner")
        )
        return WorkflowDefinition.model_validate(defn)


async def handle_incoming_webhook(path: str, secret: str) -> WorkflowDefinition:
    """Handle incoming webhook requests.

    Steps
    -----
    1. Validate
        - Lookup the secret in the database
        - If the secret is not found, return a 404.


    Webhook path is its natural key
    """
    # TODO(perf): Replace this when we get async sessions
    defn = await asyncio.to_thread(
        validate_incoming_webhook, webhook_id=path, secret=secret
    )
    return defn


@router.post("/webhooks/{path}/{secret}", tags=["triggers"])
async def webhook(
    defn: Annotated[WorkflowDefinition, Depends(handle_incoming_webhook)],
    path: str,
    payload: dict[str, Any] | None = None,
):
    """Webhook endpoint to trigger a workflow.

    Params
    ------
    path: str
        The webhook path, equivalent to the workflow id

    Notes
    -----
    The `Workflow` object holds the RF graph

    Steps
    -----
    0. Authenticate the user
    1. Fetch the DSL from the workflow object
    2. Construct the DSLInput object
    3. Dispatch the workflow

    Todos
    -----
    We're going to use Svix to manage our webhooks.
    """
    logger.info("Webhook hit", path=path, payload=payload)

    # Fetch the DSL from the workflow object
    logger.info(defn)
    dsl_input = defn.content
    logger.info(dsl_input.dump_yaml())

    asyncio.create_task(dispatch_workflow(dsl_input, workflow_id=path))
    return {"status": "ok"}
