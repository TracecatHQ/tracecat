from typing import Any

from fastapi import APIRouter

from tracecat.contexts import ctx_role
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.webhooks.dependencies import PayloadDep, WorkflowDefinitionFromWebhook
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.models import WorkflowExecutionCreateResponse
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/webhooks")


@router.post("/{workflow_id}/{secret}", tags=["public"])
async def incoming_webhook(
    defn: WorkflowDefinitionFromWebhook,
    workflow_id: AnyWorkflowIDPath,
    payload: PayloadDep,
) -> WorkflowExecutionCreateResponse:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info("Webhook hit", path=workflow_id, payload=payload, role=ctx_role.get())

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    response = service.create_workflow_execution_nowait(
        dsl=dsl_input,
        wf_id=workflow_id,
        payload=payload,
        trigger_type=TriggerType.WEBHOOK,
    )
    return response


@router.post("/{workflow_id}/{secret}/wait", tags=["public"])
async def incoming_webhook_wait(
    defn: WorkflowDefinitionFromWebhook,
    workflow_id: AnyWorkflowIDPath,
    payload: PayloadDep,
) -> Any:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info("Webhook hit", path=workflow_id, payload=payload, role=ctx_role.get())

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    response = await service.create_workflow_execution(
        dsl=dsl_input,
        wf_id=workflow_id,
        payload=payload,
        trigger_type=TriggerType.WEBHOOK,
    )

    return response["result"]
