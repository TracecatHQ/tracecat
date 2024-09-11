from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from tracecat.api.routers.public.dependencies import (
    handle_service_callback,
    validate_incoming_webhook,
)
from tracecat.contexts import ctx_role
from tracecat.dsl.common import DSLInput
from tracecat.logger import logger
from tracecat.types.api import ServiceCallbackAction
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/callback")


@router.post("/{service}", tags=["public"])
async def webhook_callback(
    request: Request,
    service: str,
    next_action: Annotated[
        ServiceCallbackAction | None, Depends(handle_service_callback)
    ],
) -> dict[str, str]:
    """Receive a callback from an external service.

    This can be used to trigger a workflow from an external service, or perform some other actions.
    """

    match next_action:
        case ServiceCallbackAction(
            action="webhook",
            payload=payload,
            metadata={"path": path, "secret": secret},
        ):
            # Don't validate method because callback is always POST
            defn = await validate_incoming_webhook(
                path=path, secret=secret, request=request, validate_method=False
            )
            logger.info(
                "Received Webhook in callback",
                service=service,
                path=path,
                payload=payload,
                role=ctx_role.get(),
            )

            # Fetch the DSL from the workflow object
            dsl_input = DSLInput(**defn.content)

            wf_exec_service = await WorkflowExecutionsService.connect()
            response = wf_exec_service.create_workflow_execution_nowait(
                dsl=dsl_input,
                wf_id=path,
                payload=payload,
            )
            return {
                "status": "ok",
                "message": "Webhook callback processed",
                "service": service,
                "details": response,
            }

        case None:
            logger.info("No next action", service=service)
            return {"status": "ok", "message": "No action taken", "service": service}
        case _:
            logger.error("Unsupported next action", next_action=next_action)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported next action in webhook callback for {service!r} service",
            )
