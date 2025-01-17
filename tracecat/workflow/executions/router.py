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

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.identifiers import WorkflowID
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.executions.dependencies import UnquotedExecutionID
from tracecat.workflow.executions.models import (
    EventHistoryRead,
    WorkflowExecutionCreate,
    WorkflowExecutionCreateResponse,
    WorkflowExecutionRead,
    WorkflowExecutionTerminate,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/workflow-executions")


@router.get("", tags=["workflow-executions"])
async def list_workflow_executions(
    role: WorkspaceUserRole,
    # Filters
    workflow_id: WorkflowID | None = Query(None),
) -> list[WorkflowExecutionRead]:
    """List all workflow executions."""
    service = await WorkflowExecutionsService.connect(role=role)
    if workflow_id:
        executions = await service.list_executions_by_workflow_id(workflow_id)
    else:
        executions = await service.list_executions()
    return [WorkflowExecutionRead.from_dataclass(execution) for execution in executions]


@router.get("/{execution_id}", tags=["workflow-executions"])
async def get_workflow_execution(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
) -> WorkflowExecutionRead:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    execution = await service.get_execution(execution_id)
    return WorkflowExecutionRead.from_dataclass(execution)


@router.get("/{execution_id}/history", tags=["workflow-executions"])
async def list_workflow_execution_event_history(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
) -> list[EventHistoryRead]:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    events = await service.list_workflow_execution_event_history(execution_id)
    return events


@router.post("", tags=["workflow-executions"])
async def create_workflow_execution(
    role: WorkspaceUserRole,
    params: WorkflowExecutionCreate,
    session: AsyncSession = Depends(get_async_session),
) -> WorkflowExecutionCreateResponse:
    """Create and schedule a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    # Get the dslinput from the workflow definition
    try:
        result = await session.exec(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == params.workflow_id)
            .order_by(WorkflowDefinition.version.desc())  # type: ignore
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
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
) -> None:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
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
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
    params: WorkflowExecutionTerminate,
) -> None:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    try:
        await service.terminate_workflow_execution(execution_id, reason=params.reason)
    except temporalio.service.RPCError as e:
        if "workflow execution already completed" in e.message:
            logger.info(
                "Workflow execution already completed, ignoring termination request",
            )
        else:
            logger.error(e.message, error=e, execution_id=execution_id)
            raise e
