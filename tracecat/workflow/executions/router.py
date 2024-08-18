from typing import Annotated

import temporalio.service
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import identifiers
from tracecat.auth.credentials import authenticate_user_for_workspace
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.logging import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.executions.models import (
    CreateWorkflowExecutionParams,
    CreateWorkflowExecutionResponse,
    EventHistoryResponse,
    TerminateWorkflowExecutionParams,
    WorkflowExecutionResponse,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/workflow-executions")


@router.get("", tags=["workflow-executions"])
async def list_workflow_executions(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    # Filters
    workflow_id: identifiers.WorkflowID | None = Query(None),
) -> list[WorkflowExecutionResponse]:
    """List all workflow executions."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        if workflow_id:
            executions = await service.list_executions_by_workflow_id(workflow_id)
        else:
            executions = await service.list_executions()
        return [
            WorkflowExecutionResponse.from_dataclass(execution)
            for execution in executions
        ]


@router.get("/{execution_id}", tags=["workflow-executions"])
async def get_workflow_execution(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    execution_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
) -> WorkflowExecutionResponse:
    """Get a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        execution = await service.get_execution(execution_id)
        return WorkflowExecutionResponse.from_dataclass(execution)


@router.get("/{execution_id}/history", tags=["workflow-executions"])
async def list_workflow_execution_event_history(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    execution_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
) -> list[EventHistoryResponse]:
    """Get a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        events = await service.list_workflow_execution_event_history(execution_id)
        return events


@router.post("", tags=["workflow-executions"])
async def create_workflow_execution(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: CreateWorkflowExecutionParams,
    session: AsyncSession = Depends(get_async_session),
) -> CreateWorkflowExecutionResponse:
    """Create and schedule a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        # Get the dslinput from the workflow definition
        try:
            result = await session.exec(
                select(WorkflowDefinition)
                .where(WorkflowDefinition.workflow_id == params.workflow_id)
                .order_by(WorkflowDefinition.version.desc())
            )
            defn = result.first()
            if not defn:
                raise NoResultFound("No workflow definition found for workflow ID")
        except NoResultFound as e:
            # No workflow associated with the webhook
            logger.opt(exception=e).error("Invalid workflow ID", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
            ) from e
        dsl_input = DSLInput(**defn.content)
        try:
            response = service.create_workflow_execution_nowait(
                dsl=dsl_input,
                wf_id=params.workflow_id,
                payload=params.inputs,
                enable_runtime_tests=params.enable_runtime_tests,
            )
            return response
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "type": "TracecatValidationError",
                    "message": str(e),
                    "detail": e.detail,
                },
            ) from e


@router.post(
    "/{execution_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflow-executions"],
)
async def cancel_workflow_execution(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    execution_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
) -> None:
    """Get a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        try:
            await service.cancel_workflow_execution(execution_id)
        except temporalio.service.RPCError as e:
            if "workflow execution already completed" in e.message:
                logger.info(
                    "Workflow execution already completed, ignoring cancellation request",
                )
            else:
                logger.error(e.message, error=e, execution_id=execution_id)
                raise e


@router.post(
    "/{execution_id}/terminate",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflow-executions"],
)
async def terminate_workflow_execution(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    execution_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
    params: TerminateWorkflowExecutionParams,
) -> None:
    """Get a workflow execution."""
    with logger.contextualize(role=role):
        service = await WorkflowExecutionsService.connect()
        try:
            await service.terminate_workflow_execution(
                execution_id, reason=params.reason
            )
        except temporalio.service.RPCError as e:
            if "workflow execution already completed" in e.message:
                logger.info(
                    "Workflow execution already completed, ignoring termination request",
                )
            else:
                logger.error(e.message, error=e, execution_id=execution_id)
                raise e
