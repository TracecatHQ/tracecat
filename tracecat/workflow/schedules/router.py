from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import Schedule
from tracecat.identifiers import ScheduleID
from tracecat.identifiers.workflow import OptionalAnyWorkflowIDQuery, WorkflowUUID
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatNotFoundError, TracecatServiceError
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.schedules.models import (
    ScheduleCreate,
    ScheduleSearch,
    ScheduleUpdate,
)
from tracecat.workflow.schedules.service import WorkflowSchedulesService

router = APIRouter(prefix="/schedules")


@router.get("", tags=["schedules"])
async def list_schedules(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: OptionalAnyWorkflowIDQuery,
) -> list[Schedule]:
    service = WorkflowSchedulesService(session, role=role)
    return await service.list_schedules(workflow_id=workflow_id)


@router.post("", tags=["schedules"])
async def create_schedule(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: ScheduleCreate,
) -> Schedule:
    """Create a schedule for a workflow."""
    service = WorkflowSchedulesService(session, role=role)
    workflow_svc = WorkflowsManagementService(session, role=role)
    workflow = await workflow_svc.get_workflow(WorkflowUUID.new(params.workflow_id))
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found. Please check the workflow ID and try again.k",
        )
    if not workflow.version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow must be saved before creating a schedule.",
        )
    try:
        return await service.create_schedule(params)
    except TracecatServiceError as e:
        logger.error("Error creating schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating schedule: {e}. Please try again or contact support.",
        ) from e


@router.get("/{schedule_id}", tags=["schedules"])
async def get_schedule(
    role: WorkspaceUserRole, session: AsyncDBSession, schedule_id: ScheduleID
) -> Schedule:
    """Get a schedule from a workflow."""
    service = WorkflowSchedulesService(session, role=role)
    try:
        return await service.get_schedule(schedule_id)
    except TracecatNotFoundError as e:
        logger.error("Error getting schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found. Please check whether this schedule exists and try again.",
        ) from e


@router.post("/{schedule_id}", tags=["schedules"])
async def update_schedule(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    schedule_id: ScheduleID,
    params: ScheduleUpdate,
) -> Schedule:
    """Update a schedule from a workflow. You cannot update the Workflow Definition, but you can update other fields."""
    service = WorkflowSchedulesService(session, role=role)
    try:
        return await service.update_schedule(schedule_id, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found. Please check whether this schedule exists and try again.",
        ) from e
    except TracecatServiceError as e:
        logger.error("Error updating schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update schedule: {e}. Please try again or contact support.",
        ) from e


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["schedules"],
)
async def delete_schedule(
    role: WorkspaceUserRole, session: AsyncDBSession, schedule_id: ScheduleID
) -> None:
    """Delete a schedule from a workflow."""
    service = WorkflowSchedulesService(session, role=role)
    try:
        await service.delete_schedule(schedule_id)
    except TracecatServiceError as e:
        logger.error("Error deleting schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete schedule: {e}. Please try again or contact support.",
        ) from e


@router.get("/search", tags=["schedules"])
async def search_schedules(
    role: WorkspaceUserRole, session: AsyncDBSession, params: ScheduleSearch
) -> list[Schedule]:
    """**[WORK IN PROGRESS]** Search for schedules."""
    statement = select(Schedule).where(Schedule.owner_id == role.workspace_id)
    results = await session.exec(statement)
    schedules = results.all()
    return list(schedules)
