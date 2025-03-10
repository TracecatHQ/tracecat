from typing import Any

from fastapi import APIRouter, Depends

from tracecat.contexts import ctx_role
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.webhooks.dependencies import (
    PayloadDep,
    ValidWorkflowDefinitionDep,
    validate_incoming_webhook,
)
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.models import (
    ReceiveInteractionResponse,
    WorkflowExecutionCreateResponse,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(
    prefix="/webhooks",
    tags=["public"],
    dependencies=[Depends(validate_incoming_webhook)],
)


@router.post("/{workflow_id}/interactions/{secret}", include_in_schema=False)
async def receive_interaction(
    workflow_id: AnyWorkflowIDPath,
    payload: PayloadDep,
) -> ReceiveInteractionResponse:
    """Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info(
        "Received interaction",
        workflow_id=workflow_id,
        payload=payload,
        role=ctx_role.get(),
    )

    return ReceiveInteractionResponse(
        message="Interaction received",
    )


@router.post("/{workflow_id}/{secret}")
async def incoming_webhook(
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
) -> WorkflowExecutionCreateResponse:
    """Webhook endpoint to trigger a workflow.

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


@router.post("/{workflow_id}/{secret}/wait")
async def incoming_webhook_wait(
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
) -> Any:
    """Webhook endpoint to trigger a workflow.

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
