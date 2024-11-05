from fastapi import APIRouter

from tracecat.contexts import ctx_role
from tracecat.dsl.common import DSLInput
from tracecat.dsl.models import DSLContext
from tracecat.logger import logger
from tracecat.webhooks.dependencies import PayloadDep, WorkflowDefinitionFromWebhook
from tracecat.workflow.executions.models import CreateWorkflowExecutionResponse
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/webhooks")


@router.post("/{path}/{secret}", tags=["public"])
async def incoming_webhook(
    defn: WorkflowDefinitionFromWebhook, path: str, payload: PayloadDep
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
    defn: WorkflowDefinitionFromWebhook, path: str, payload: PayloadDep
) -> DSLContext:
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
