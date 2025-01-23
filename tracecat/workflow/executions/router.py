import temporalio.service
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.exc import NoResultFound
from sqlmodel import col, select

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.auth.enums import SpecialUserID
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import WorkflowDefinition
from tracecat.dsl.common import DSLInput
from tracecat.identifiers import UserID
from tracecat.identifiers.workflow import OptionalAnyWorkflowIDQuery, WorkflowUUID
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.executions.dependencies import UnquotedExecutionID
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.models import (
    WorkflowExecutionCreate,
    WorkflowExecutionCreateResponse,
    WorkflowExecutionRead,
    WorkflowExecutionReadCompact,
    WorkflowExecutionReadMinimal,
    WorkflowExecutionTerminate,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

router = APIRouter(prefix="/workflow-executions", tags=["workflow-executions"])


@router.get("")
async def list_workflow_executions(
    role: WorkspaceUserRole,
    # Filters
    workflow_id: OptionalAnyWorkflowIDQuery,
    trigger_types: set[TriggerType] | None = Query(None, alias="trigger"),
    triggered_by_user_id: UserID | SpecialUserID | None = Query(None, alias="user_id"),
    limit: int | None = Query(None, alias="limit"),
) -> list[WorkflowExecutionReadMinimal]:
    """List all workflow executions."""
    service = await WorkflowExecutionsService.connect(role=role)
    if triggered_by_user_id == SpecialUserID.CURRENT:
        if role.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User ID is required to filter by user ID",
            )
        triggered_by_user_id = role.user_id
    executions = await service.list_executions(
        workflow_id=workflow_id,
        trigger_types=trigger_types,
        triggered_by_user_id=triggered_by_user_id,
        limit=limit,
    )
    return [
        WorkflowExecutionReadMinimal.from_dataclass(execution)
        for execution in executions
    ]


@router.get("/{execution_id}")
async def get_workflow_execution(
    role: WorkspaceUserRole, execution_id: UnquotedExecutionID
) -> WorkflowExecutionRead:
    """Get a workflow execution."""
    logger.info("Getting workflow execution", execution_id=execution_id)
    service = await WorkflowExecutionsService.connect(role=role)
    execution = await service.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow execution not found",
        )
    logger.info("Getting workflow execution events", execution_id=execution.id)
    events = await service.list_workflow_execution_events(execution.id)
    return WorkflowExecutionRead(
        id=execution.id,
        run_id=execution.run_id,
        start_time=execution.start_time,
        execution_time=execution.execution_time,
        close_time=execution.close_time,
        status=execution.status,
        workflow_type=execution.workflow_type,
        task_queue=execution.task_queue,
        history_length=execution.history_length,
        events=events,
    )


@router.get("/{execution_id}/compact")
async def get_workflow_execution_compact(
    role: WorkspaceUserRole,
    execution_id: UnquotedExecutionID,
) -> WorkflowExecutionReadCompact:
    """Get a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    execution = await service.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow execution not found",
        )

    compact_events = await service.list_workflow_execution_events_compact(execution_id)
    return WorkflowExecutionReadCompact(
        id=execution.id,
        parent_wf_exec_id=execution.parent_id,
        run_id=execution.run_id,
        start_time=execution.start_time,
        execution_time=execution.execution_time,
        close_time=execution.close_time,
        status=execution.status,
        workflow_type=execution.workflow_type,
        task_queue=execution.task_queue,
        history_length=execution.history_length,
        events=compact_events,
    )


@router.post("")
async def create_workflow_execution(
    role: WorkspaceUserRole,
    params: WorkflowExecutionCreate,
    session: AsyncDBSession,
) -> WorkflowExecutionCreateResponse:
    """Create and schedule a workflow execution."""
    service = await WorkflowExecutionsService.connect(role=role)
    # Get the dslinput from the workflow definition
    wf_id = WorkflowUUID.new(params.workflow_id)
    try:
        result = await session.exec(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == wf_id.to_legacy())
            .order_by(col(WorkflowDefinition.version).desc())
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
            dsl=dsl_input, wf_id=wf_id, payload=params.inputs
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
