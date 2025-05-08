from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from temporalio.service import RPCError

from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.enums import InteractionCategory
from tracecat.ee.interactions.models import InteractionInput
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.webhooks.dependencies import (
    PayloadDep,
    ValidWorkflowDefinitionDep,
    parse_interaction_payload,
    validate_incoming_webhook,
)
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.models import (
    ReceiveInteractionResponse,
    WorkflowExecutionCreateResponse,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(
    prefix="/webhooks/{workflow_id}/{secret}",
    tags=["public"],
    dependencies=[Depends(validate_incoming_webhook)],
)


# NOTE: Need to set response_model to None to avoid FastAPI trying to parse the response as JSON
# We need empty status 200 responses for slash command APIs (e.g. Slack)
@router.post("", response_model=None)
async def incoming_webhook(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool = Query(default=False, description="Echo back to the caller"),
    empty_echo: bool = Query(
        default=False,
        description="Return an empty response. Assumes `echo` to be `True`.",
    ),
    request: Request,
) -> WorkflowExecutionCreateResponse | Response:
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
    if echo:
        if empty_echo:
            return Response(status_code=200)
        try:
            response["payload"] = await request.json()
        except Exception as e:
            logger.warning("Failed to decode request payload body", error=str(e))
            response["payload"] = None
    return response


@router.post("/wait")
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


@router.post("/interactions/{category}")
async def receive_interaction(
    workflow_id: AnyWorkflowIDPath,
    input: Annotated[InteractionInput, Depends(parse_interaction_payload)],
    category: InteractionCategory,
) -> ReceiveInteractionResponse:
    """Process incoming workflow interactions from external services.

    Args:
        workflow_id: ID of the workflow to interact with
        payload: Raw interaction payload from the external service
        category: Category of interaction (e.g., slack)

    Returns:
        Response confirming the interaction was processed

    Raises:
        HTTPException: If payload is invalid or workflow interaction fails
    """
    logger.info(
        "Received interaction",
        workflow_id=workflow_id,
        category=category,
        input=input,
        role=ctx_role.get(),
    )

    try:
        # Get temporal client and workflow handle
        client = await get_temporal_client()
        handle = client.get_workflow_handle_for(DSLWorkflow.run, input.execution_id)

        # Convert to internal interaction format
        # Execute workflow interaction handler
        result = await handle.execute_update(DSLWorkflow.interaction_handler, input)
        logger.info("Interaction processed", result=result)

        return ReceiveInteractionResponse(
            message="Interaction processed successfully",
        )

    except RPCError as e:
        if "workflow not found" in str(e).lower():
            logger.error("Workflow not found", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
            ) from e
        raise
    except Exception as e:
        logger.error("Failed to process interaction", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process interaction: {str(e)}",
        ) from e
