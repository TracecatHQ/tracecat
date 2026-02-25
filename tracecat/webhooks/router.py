from itertools import batched
from typing import Annotated, Any, TypedDict

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from temporalio.service import RPCError

from tracecat.concurrency import cooperative
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.enums import InteractionCategory
from tracecat.ee.interactions.schemas import InteractionInput
from tracecat.identifiers.workflow import AnyWorkflowIDPath, generate_exec_id
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.webhooks.dependencies import (
    DraftWorkflowDep,
    PayloadDep,
    ValidWorkflowDefinitionDep,
    parse_content_type,
    parse_interaction_payload,
    validate_incoming_webhook,
)
from tracecat.webhooks.schemas import NDJSON_CONTENT_TYPES
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.schemas import (
    ReceiveInteractionResponse,
    WorkflowExecutionCreateResponse,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(
    prefix="/webhooks/{workflow_id}/{secret}",
    tags=["public"],
    dependencies=[Depends(validate_incoming_webhook)],
)


class OktaVerificationResponse(TypedDict):
    verification: str


type WebhookResponse = (
    WorkflowExecutionCreateResponse | OktaVerificationResponse | Response
)


# NOTE: Need to set response_model to None to avoid FastAPI trying to parse the response as JSON
# We need empty status 200 responses for slash command APIs (e.g. Slack)
# POST is the primary method for webhook triggering
@router.post("", response_model=None)
async def incoming_webhook_post(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool = Query(default=False, description="Echo back to the caller"),
    empty_echo: bool = Query(
        default=False,
        description="Return an empty response. Assumes `echo` to be `True`.",
    ),
    vendor: str | None = Query(
        default=None,
        description="Vendor specific webhook verification. Supported vendors: `okta`.",
    ),
    request: Request,
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
) -> WebhookResponse:
    """Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    return await _incoming_webhook(
        workflow_id=workflow_id,
        defn=defn,
        payload=payload,
        echo=echo,
        empty_echo=empty_echo,
        vendor=vendor,
        request=request,
        content_type=content_type,
    )


# GET is secondary, mainly for webhook verification challenges (e.g., Okta)
@router.get("", response_model=None)
async def incoming_webhook_get(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool = Query(default=False, description="Echo back to the caller"),
    empty_echo: bool = Query(
        default=False,
        description="Return an empty response. Assumes `echo` to be `True`.",
    ),
    vendor: str | None = Query(
        default=None,
        description="Vendor specific webhook verification. Supported vendors: `okta`.",
    ),
    request: Request,
    content_type: Annotated[str | None, Header(alias="content-type")] = None,
) -> WebhookResponse:
    """Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    return await _incoming_webhook(
        workflow_id=workflow_id,
        defn=defn,
        payload=payload,
        echo=echo,
        empty_echo=empty_echo,
        vendor=vendor,
        request=request,
        content_type=content_type,
    )


async def _incoming_webhook(
    *,
    workflow_id: AnyWorkflowIDPath,
    defn: ValidWorkflowDefinitionDep,
    payload: PayloadDep,
    echo: bool,
    empty_echo: bool,
    vendor: str | None,
    request: Request,
    content_type: str | None,
) -> WebhookResponse:
    logger.info("Webhook hit", path=workflow_id, role=ctx_role.get())
    logger.trace("Webhook payload", payload=payload)

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    # If this was a ndjson, automatically batch the requests
    # This is a workaround for the fact that Temporal doesn't support batching
    # of webhook requests
    mime_type = parse_content_type(content_type)[0] if content_type else ""
    if mime_type in NDJSON_CONTENT_TYPES and isinstance(payload, list):
        one_response = None
        # Slow release to avoid overwhelming the system
        async for p in cooperative(batched(payload, 8), delay=2):
            one_response = await service.create_workflow_execution_wait_for_start(
                dsl=dsl_input,
                wf_id=workflow_id,
                payload=p,
                trigger_type=TriggerType.WEBHOOK,
                registry_lock=RegistryLock.model_validate(defn.registry_lock)
                if defn.registry_lock
                else None,
                definition_version=defn.version,
            )
        # Currently just return the last response's wf_exec_id
        response = WorkflowExecutionCreateResponse(
            message="Workflow execution created",
            wf_id=workflow_id,
            wf_exec_id=one_response["wf_exec_id"]
            if one_response
            else generate_exec_id(workflow_id),  # This should never happen
        )

    else:
        response = await service.create_workflow_execution_wait_for_start(
            dsl=dsl_input,
            wf_id=workflow_id,
            payload=payload,
            trigger_type=TriggerType.WEBHOOK,
            registry_lock=RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None,
            definition_version=defn.version,
        )

    # Response handling
    if echo:
        if empty_echo:
            return Response(status_code=200)
        try:
            response["payload"] = await request.json()
        except Exception as e:
            logger.warning(
                "Failed to decode request payload body during echo", error=str(e)
            )
            response["payload"] = None

    if vendor is not None:
        if vendor == "okta":
            # https://developer.okta.com/docs/concepts/event-hooks/#one-time-verification-request
            challenge = request.headers.get("x-okta-verification-challenge")
            if challenge:
                return OktaVerificationResponse(verification=challenge)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported vendor: {vendor}. Expected: `okta`.",
            )

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
    logger.info("Webhook hit", path=workflow_id, role=ctx_role.get())
    logger.trace("Webhook payload", payload=payload)

    dsl_input = DSLInput(**defn.content)

    service = await WorkflowExecutionsService.connect()
    response = await service.create_workflow_execution(
        dsl=dsl_input,
        wf_id=workflow_id,
        payload=payload,
        trigger_type=TriggerType.WEBHOOK,
        registry_lock=RegistryLock.model_validate(defn.registry_lock)
        if defn.registry_lock
        else None,
        definition_version=defn.version,
    )

    return response["result"]


@router.post("/draft", response_model=None)
async def incoming_webhook_draft(
    workflow_id: AnyWorkflowIDPath,
    draft_ctx: DraftWorkflowDep,
    payload: PayloadDep,
) -> WorkflowExecutionCreateResponse:
    """Draft webhook endpoint to trigger a workflow execution using the draft workflow graph.

    This endpoint runs the current (uncommitted) workflow graph rather than the committed definition.
    Child workflows using aliases will resolve to the latest draft aliases, not committed aliases.
    """
    logger.info("Draft webhook hit", path=workflow_id, role=ctx_role.get())
    logger.trace("Draft webhook payload", payload=payload)

    service = await WorkflowExecutionsService.connect()
    response = await service.create_draft_workflow_execution_wait_for_start(
        dsl=draft_ctx.dsl,
        wf_id=workflow_id,
        payload=payload,
        trigger_type=TriggerType.WEBHOOK,
        registry_lock=RegistryLock.model_validate(draft_ctx.registry_lock)
        if draft_ctx.registry_lock
        else None,
    )
    return response


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
        role=ctx_role.get(),
    )
    logger.trace("Interaction input", input=input)

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
