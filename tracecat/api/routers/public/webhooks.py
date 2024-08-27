from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from urllib import parse as urlparse

from tracecat.api.routers.public.dependencies import validate_incoming_webhook
from tracecat.contexts import ctx_role
from tracecat.db.schemas import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.logging import logger
from tracecat.workflow.executions.models import CreateWorkflowExecutionResponse
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/webhooks")


@router.post("/{path}/{secret}", tags=["public"])
async def incoming_webhook(
    defn: Annotated[WorkflowDefinition, Depends(validate_incoming_webhook)],
    path: str,
    payload: dict[str, Any] | str | None = None,
    x_tracecat_enable_runtime_tests: Annotated[str | None, Header()] = None,
) -> CreateWorkflowExecutionResponse:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    if isinstance(payload,dict):
      logger.info("Webhook hit", path=path, payload=payload, role=ctx_role.get())
    elif isinstance(payload,str):
      logger.info("Webhook hit - query - Converting to JSON", path=path, payload=payload, role=ctx_role.get())
      payload = dict(urlparse.parse_qsl(payload))
    else:
      logger.info("Webhook hit", path=path, payload=payload, role=ctx_role.get())
      raise ValueError('Payload is not a str or dict - cannot obtain json for validation')

    dsl_input = DSLInput(**defn.content)

    enable_runtime_tests = (x_tracecat_enable_runtime_tests or "false").lower() in (
        "1",
        "true",
    )

    service = await WorkflowExecutionsService.connect()
    response = service.create_workflow_execution_nowait(
        dsl=dsl_input,
        wf_id=path,
        payload=payload,
        enable_runtime_tests=enable_runtime_tests,
    )
    return response


@router.post("/{path}/{secret}/wait", tags=["public"])
async def incoming_webhook_wait(
    defn: Annotated[WorkflowDefinition, Depends(validate_incoming_webhook)],
    path: str,
    payload: dict[str, Any] | None = None,
    x_tracecat_enable_runtime_tests: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """
    Webhook endpoint to trigger a workflow.

    This is an external facing endpoint is used to trigger a workflow by sending a webhook request.
    The workflow is identified by the `path` parameter, which is equivalent to the workflow id.
    """
    logger.info("Webhook hit", path=path, payload=payload, role=ctx_role.get())

    dsl_input = DSLInput(**defn.content)

    enable_runtime_tests = (x_tracecat_enable_runtime_tests or "false").lower() in (
        "1",
        "true",
    )

    service = await WorkflowExecutionsService.connect()
    response = await service.create_workflow_execution(
        dsl=dsl_input,
        wf_id=path,
        payload=payload,
        enable_runtime_tests=enable_runtime_tests,
    )

    return response["final_context"]
