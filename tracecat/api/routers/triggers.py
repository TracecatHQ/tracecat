import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import Engine
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.auth import Role
from tracecat.contexts import ctx_role
from tracecat.db.connectors import workflow_to_dsl
from tracecat.db.engine import get_engine
from tracecat.db.models import Workflow
from tracecat.experimental.dsl.workflow import DSLInput

engine: Engine = get_engine()


router = APIRouter()


def validate_incoming_webhook(webhook_id: str, secret: str) -> Workflow:
    with Session(engine) as session:
        statement = select(Workflow).where(Workflow.id == webhook_id)
        # statement = select(Webhook).where(Webhook.id == webhook_id)
        result = session.exec(statement)
        try:
            workflow = result.one()
        except NoResultFound as e:
            logger.opt(exception=e).error("Webhook does not exist", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid webhook ID"
            ) from e
        # if secret != webhook.secret:
        #     raise HTTPException(
        #         status_code=status.HTTP_401_UNAUTHORIZED,
        #         detail="Unauthorized webhook request",
        #     )

        # if (workflow := webhook.workflow) is None:
        #     raise HTTPException(
        #         status_code=status.HTTP_404_NOT_FOUND, detail="There is no workflow"
        #     )
        _ = workflow.actions  # Hydrate the workflow
        if workflow.actions is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workflow has no actions"
            )
        ctx_role.set(
            Role(
                type="service", user_id=workflow.owner_id, service_id="tracecat-runner"
            )
        )
        return workflow


async def handle_incoming_webhook(path: str, secret: str) -> DSLInput:
    """Handle incoming webhook requests.

    Steps
    -----
    1. Validate
        - Lookup the secret in the database
        - If the secret is not found, return a 404.


    Webhook path is its natural key
    """
    # TODO(perf): Replace this when we get async sessions
    workflow = await asyncio.to_thread(
        validate_incoming_webhook, webhook_id=path, secret=secret
    )
    logger.info(workflow)

    return workflow_to_dsl(workflow)


@router.post("/webhooks/{path}/{secret}")
async def webhook(
    dsl: Annotated[DSLInput, Depends(handle_incoming_webhook)],
    path: str,
    payload: dict[str, Any],
):
    """Webhook endpoint to trigger a workflow.

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
    logger.info(dsl)
    logger.info(dsl.dump_yaml())

    # await dispatch_wofklow(dsl)
    return {"status": "ok"}
