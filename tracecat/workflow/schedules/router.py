import asyncio

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Schedule
from tracecat.exceptions import TracecatNotFoundError, TracecatServiceError
from tracecat.identifiers.workflow import OptionalAnyWorkflowIDQuery
from tracecat.logger import logger
from tracecat.workflow.schedules.dependencies import AnyScheduleIDPath
from tracecat.workflow.schedules.schemas import (
    ScheduleCreate,
    ScheduleRead,
    ScheduleSearch,
    ScheduleUpdate,
)
from tracecat.workflow.schedules.service import WorkflowSchedulesService

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleRead])
@require_scope("schedule:read")
async def list_schedules(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    workflow_id: OptionalAnyWorkflowIDQuery,
) -> list[ScheduleRead]:
    service = WorkflowSchedulesService(session, role=role)
    schedules = await service.list_schedules(workflow_id=workflow_id)

    run_times = await asyncio.gather(
        *(service.get_schedule_run_times(s.id) for s in schedules)
    )

    result = []
    for s, (last_run, next_run) in zip(schedules, run_times, strict=True):
        read_obj = ScheduleRead.model_validate(s)
        read_obj.last_run_at = last_run
        read_obj.next_run_at = next_run
        result.append(read_obj)

    return result


@router.post("", response_model=ScheduleRead)
@require_scope("schedule:create")
async def create_schedule(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: ScheduleCreate,
) -> ScheduleRead:
    """Create a schedule for a workflow."""
    service = WorkflowSchedulesService(session, role=role)
    try:
        schedule = await service.create_schedule(params)
        return ScheduleRead.model_validate(schedule)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatServiceError as e:
        logger.error("Error creating schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating schedule: {e}. Please try again or contact support.",
        ) from e


@router.get("/{schedule_id}", response_model=ScheduleRead)
@require_scope("schedule:read")
async def get_schedule(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    schedule_id: AnyScheduleIDPath,
) -> ScheduleRead:
    """Get a schedule from a workflow."""
    service = WorkflowSchedulesService(session, role=role)
    try:
        schedule = await service.get_schedule(schedule_id)
        last_run, next_run = await service.get_schedule_run_times(schedule.id)
        read_obj = ScheduleRead.model_validate(schedule)
        read_obj.last_run_at = last_run
        read_obj.next_run_at = next_run
        return read_obj
    except TracecatNotFoundError as e:
        logger.error("Error getting schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found. Please check whether this schedule exists and try again.",
        ) from e


@router.post("/{schedule_id}", response_model=ScheduleRead)
@require_scope("schedule:update")
async def update_schedule(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    schedule_id: AnyScheduleIDPath,
    params: ScheduleUpdate,
) -> ScheduleRead:
    """Update a schedule from a workflow. You cannot update the Workflow Definition, but you can update other fields."""
    service = WorkflowSchedulesService(session, role=role)
    try:
        schedule = await service.update_schedule(schedule_id, params)
        last_run, next_run = await service.get_schedule_run_times(schedule.id)
        read_obj = ScheduleRead.model_validate(schedule)
        read_obj.last_run_at = last_run
        read_obj.next_run_at = next_run
        return read_obj
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


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("schedule:delete")
async def delete_schedule(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    schedule_id: AnyScheduleIDPath,
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


@router.get("/search", response_model=list[ScheduleRead])
@require_scope("schedule:read")
async def search_schedules(
    role: WorkspaceActorRouteRole, session: AsyncDBSession, params: ScheduleSearch
) -> list[ScheduleRead]:
    """**[WORK IN PROGRESS]** Search for schedules."""
    statement = select(Schedule).where(Schedule.workspace_id == role.workspace_id)
    results = await session.execute(statement)
    schedules = results.scalars().all()

    service = WorkflowSchedulesService(session, role=role)
    run_times = await asyncio.gather(
        *(service.get_schedule_run_times(s.id) for s in schedules)
    )

    result = []
    for s, (last_run, next_run) in zip(schedules, run_times, strict=True):
        read_obj = ScheduleRead.model_validate(s)
        read_obj.last_run_at = last_run
        read_obj.next_run_at = next_run
        result.append(read_obj)

    return result
