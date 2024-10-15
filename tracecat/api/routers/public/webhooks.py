from typing import Annotated, Any

from fastapi import APIRouter, Depends

from tracecat.api.routers.public.dependencies import validate_incoming_webhook
from tracecat.contexts import ctx_role
from tracecat.db.schemas import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.logger import logger
from tracecat.workflow.executions.models import CreateWorkflowExecutionResponse
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/webhooks")


@router.post("/{path}/{secret}", tags=["public"])
async def incoming_webhook(
    defn: Annotated[WorkflowDefinition, Depends(validate_incoming_webhook)],
    path: str,
    payload: dict[str, Any] | None = None,
) -> CreateWorkflowExecutionResponse:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info("Webhook hit", path=path, payload=payload, role=ctx_role.get())

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    response = service.create_workflow_execution_nowait(
        dsl=dsl_input, wf_id=path, payload=payload
    )
    return response


@router.post("/{path}/{secret}/wait", tags=["public"])
async def incoming_webhook_wait(
    defn: Annotated[WorkflowDefinition, Depends(validate_incoming_webhook)],
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info("Webhook hit", path=path, payload=payload, role=ctx_role.get())

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    response = await service.create_workflow_execution(
        dsl=dsl_input, wf_id=path, payload=payload
    )

    return response["final_context"]
