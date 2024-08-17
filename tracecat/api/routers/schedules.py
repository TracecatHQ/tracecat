from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import identifiers
from tracecat.auth.credentials import TemporaryRole, authenticate_user_for_workspace
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import Schedule, WorkflowDefinition
from tracecat.dsl import schedules
from tracecat.dsl.common import DSLInput
from tracecat.logging import logger
from tracecat.types.api import (
    CreateScheduleParams,
    SearchScheduleParams,
    UpdateScheduleParams,
)
from tracecat.types.auth import Role

router = APIRouter(prefix="/schedules")


@router.get("", tags=["schedules"])
async def list_schedules(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    workflow_id: identifiers.WorkflowID | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> list[Schedule]:
    """List all schedules for a workflow."""
    statement = select(Schedule).where(Schedule.owner_id == role.workspace_id)
    if workflow_id:
        statement = statement.where(Schedule.workflow_id == workflow_id)
    result = await session.exec(statement)
    schedules = result.all()
    return schedules


@router.post("", tags=["schedules"])
async def create_schedule(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: CreateScheduleParams,
    session: AsyncSession = Depends(get_async_session),
) -> Schedule:
    """Create a schedule for a workflow."""

    with logger.contextualize(role=role):
        result = await session.exec(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.workflow_id == params.workflow_id)
            .order_by(WorkflowDefinition.version.desc())
        )
        try:
            if not (defn_data := result.first()):
                raise NoResultFound("No workflow definition found for workflow ID")
        except NoResultFound as e:
            logger.opt(exception=e).error("Invalid workflow ID", error=e)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid workflow ID"
            ) from e

        schedule = Schedule(
            owner_id=role.workspace_id, **params.model_dump(exclude_unset=True)
        )
        await session.refresh(defn_data)
        defn = WorkflowDefinition.model_validate(defn_data)
        dsl = DSLInput(**defn.content)
        if params.inputs:
            dsl.trigger_inputs = params.inputs

        try:
            # Set the role for the schedule as the tracecat-runner
            with TemporaryRole(
                type="service",
                user_id=defn.owner_id,
                service_id="tracecat-schedule-runner",
            ) as sch_role:
                handle = await schedules.create_schedule(
                    workflow_id=params.workflow_id,
                    schedule_id=schedule.id,
                    dsl=dsl,
                    every=params.every,
                    offset=params.offset,
                    start_at=params.start_at,
                    end_at=params.end_at,
                )
                logger.info(
                    "Created schedule",
                    handle_id=handle.id,
                    workflow_id=params.workflow_id,
                    schedule_id=schedule.id,
                    sch_role=sch_role,
                )

            session.add(schedule)
            await session.commit()
            await session.refresh(schedule)
            return schedule
        except Exception as e:
            session.rollback()
            logger.opt(exception=e).error("Error creating schedule", error=e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error creating schedule",
            ) from e


@router.get("/{schedule_id}", tags=["schedules"])
async def get_schedule(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    schedule_id: identifiers.ScheduleID,
    session: AsyncSession = Depends(get_async_session),
) -> Schedule:
    """Get a schedule from a workflow."""
    statement = select(Schedule).where(
        Schedule.owner_id == role.workspace_id, Schedule.id == schedule_id
    )
    result = await session.exec(statement)
    try:
        schedule = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    return schedule


@router.post("/{schedule_id}", tags=["schedules"])
async def update_schedule(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    schedule_id: identifiers.ScheduleID,
    params: UpdateScheduleParams,
    session: AsyncSession = Depends(get_async_session),
) -> Schedule:
    """Update a schedule from a workflow. You cannot update the Workflow Definition, but you can update other fields."""
    statement = select(Schedule).where(
        Schedule.owner_id == role.workspace_id, Schedule.id == schedule_id
    )
    result = await session.exec(statement)
    try:
        schedule = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    try:
        # (1) Synchronize with Temporal
        await schedules.update_schedule(schedule_id, params)

        # (2) Update the schedule
        for key, value in params.model_dump(exclude_unset=True).items():
            # Safety: params have been validated
            setattr(schedule, key, value)

        session.add(schedule)
        await session.commit()
        await session.refresh(schedule)
        return schedule
    except Exception as e:
        session.rollback()
        logger.opt(exception=e).error("Error creating schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating schedule",
        ) from e


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["schedules"],
)
async def delete_schedule(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    schedule_id: identifiers.ScheduleID,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete a schedule from a workflow."""
    statement = select(Schedule).where(
        Schedule.owner_id == role.workspace_id, Schedule.id == schedule_id
    )
    result = await session.exec(statement)
    schedule = result.one_or_none()
    if not schedule:
        logger.warning(
            "Schedule not found, attempt to delete underlying Temporal schedule...",
            schedule_id=schedule_id,
        )

    try:
        # Delete the schedule from Temporal first
        await schedules.delete_schedule(schedule_id)

        # If successful, delete the schedule from the database
        if schedule:
            await session.delete(schedule)
            await session.commit()
        else:
            logger.warning(
                "Schedule was already deleted from the database",
                schedule_id=schedule_id,
            )
    except Exception as e:
        logger.error("Error deleting schedule", error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error deleting schedule",
        ) from e


@router.get("/search", tags=["schedules"])
async def search_schedules(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: SearchScheduleParams,
    session: AsyncSession = Depends(get_async_session),
) -> list[Schedule]:
    """**[WORK IN PROGRESS]** Search for schedules."""
    statement = select(Schedule).where(Schedule.owner_id == role.workspace_id)
    results = await session.exec(statement)
    schedules = results.all()
    return schedules
