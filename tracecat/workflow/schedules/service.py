from __future__ import annotations

from typing import Literal, cast

from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from temporalio import activity

from tracecat.contexts import ctx_role
from tracecat.db.schemas import Schedule
from tracecat.identifiers import ScheduleID, WorkflowID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseService
from tracecat.types.auth import AccessLevel
from tracecat.types.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatServiceError,
)
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.models import (
    GetScheduleActivityInputs,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)


class WorkflowSchedulesService(BaseService):
    """Manages schedules for Workflows."""

    service_name = "workflow_schedules"

    async def list_schedules(
        self, workflow_id: WorkflowID | None = None
    ) -> list[Schedule]:
        """
        List all schedules for a workflow.

        Parameters
        ----------
        workflow_id : WorkflowID | None, optional
            The ID of the workflow. If provided, only schedules for the specified workflow will be listed.

        Returns
        -------
        list[Schedule]
            A list of Schedule objects representing the schedules for the specified workflow, or all schedules if no workflow ID is provided.
        """
        statement = select(Schedule).where(Schedule.owner_id == self.role.workspace_id)
        if workflow_id is not None:
            statement = statement.where(Schedule.workflow_id == workflow_id)
        result = await self.session.exec(statement)
        schedules = result.all()
        return list(schedules)

    async def create_schedule(self, params: ScheduleCreate) -> Schedule:
        """
        Create a schedule for a workflow.

        Parameters
        ----------
        params : ScheduleCreate
            The parameters for creating the schedule.

        Returns
        -------
        Schedule
            The created schedule.

        Raises
        ------
        TracecatServiceError
            If there is an error creating the schedule.

        """
        owner_id = self.role.workspace_id
        if owner_id is None:
            raise TracecatAuthorizationError("Workspace ID is required")
        schedule = Schedule(
            owner_id=owner_id,
            workflow_id=WorkflowUUID.new(params.workflow_id),
            inputs=params.inputs or {},
            every=params.every,
            offset=params.offset,
            start_at=params.start_at,
            end_at=params.end_at,
            timeout=params.timeout,
            cron=params.cron,
            status="online",
        )
        self.session.add(schedule)

        role = ctx_role.get().model_copy(
            update={
                "type": "service",
                "service_id": "tracecat-schedule-runner",
                "access_level": AccessLevel.ADMIN,
                "user_id": None,
            }
        )

        try:
            handle = await bridge.create_schedule(
                workflow_id=WorkflowUUID.new(params.workflow_id),
                schedule_id=schedule.id,
                every=params.every,
                offset=params.offset,
                start_at=params.start_at,
                end_at=params.end_at,
                timeout=params.timeout,
                role=role,
            )
        except Exception as e:
            # If we fail to create the schedule in temporal
            # we should rollback the transaction
            await self.session.rollback()
            msg = "The schedules service couldn't create a Temporal schedule"
            self.logger.error(
                msg,
                error=e,
                workflow_id=params.workflow_id,
                schedule_id=schedule.id,
                schedule_role=role,
            )
            raise TracecatServiceError(msg) from e
        logger.info(
            "Created schedule",
            handle_id=handle.id,
            workflow_id=params.workflow_id,
            schedule_id=schedule.id,
            schedule_role=role,
        )

        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def get_schedule(self, schedule_id: ScheduleID) -> Schedule:
        """
        Retrieve a schedule by its ID.

        Parameters
        ----------
        schedule_id : ScheduleID
            The ID of the schedule to retrieve.

        Returns
        -------
        Schedule
            The retrieved schedule.

        Raises
        ------
        TracecatNotFoundError
            If the schedule is not found

        """
        result = await self.session.exec(
            select(Schedule).where(
                Schedule.owner_id == self.role.workspace_id,
                Schedule.id == schedule_id,
            )
        )
        try:
            return result.one()
        except NoResultFound as e:
            raise TracecatNotFoundError(f"Schedule {schedule_id} not found") from e

    async def update_schedule(
        self, schedule_id: ScheduleID, params: ScheduleUpdate
    ) -> Schedule:
        """
        Update a schedule with the given schedule ID and parameters.

        Parameters
        ----------
        schedule_id : ScheduleID
            The ID of the schedule to be updated.
        params : ScheduleUpdate
            The updated parameters for the schedule.

        Returns
        -------
        Schedule
            The updated schedule.

        Raises
        ------
        TracecatNotFoundError
            If there is an error updating the schedule.
        """
        schedule = await self.get_schedule(schedule_id)

        try:
            # Synchronize with Temporal
            await bridge.update_schedule(schedule_id, params)
        except Exception as e:
            await self.session.rollback()
            msg = f"The schedules service couldn't update the Temporal schedule with ID {schedule_id}"
            logger.error(msg, error=e)
            raise TracecatNotFoundError(msg) from e

        # Update the schedule
        for key, value in params.model_dump(exclude_unset=True).items():
            # Safety: params have been validated
            setattr(schedule, key, value)

        self.session.add(schedule)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def delete_schedule(self, schedule_id: ScheduleID) -> None:
        """
        Delete a schedule.

        Parameters
        ----------
        schedule_id : ScheduleID
            The ID of the schedule to be deleted.

        Raises
        ------
        TracecatServiceError
            If an error occurs while deleting the schedule from Temporal.

        """
        try:
            schedule = await self.get_schedule(schedule_id)
        except NoResultFound:
            schedule = None
            logger.warning(
                "Schedule not found, attempt to delete underlying Temporal schedule...",
                schedule_id=schedule_id,
            )

        try:
            # Delete the schedule from Temporal first
            await bridge.delete_schedule(schedule_id)
        except RuntimeError as e:
            raise TracecatServiceError(
                f"The schedules service couldn't delete the Temporal schedule with ID {schedule_id}"
            ) from e

        # If successful, delete the schedule from the database
        if schedule:
            await self.session.delete(schedule)
            await self.session.commit()
        else:
            logger.warning(
                "Schedule was already deleted from the database",
                schedule_id=schedule_id,
            )

    @staticmethod
    @activity.defn
    async def get_schedule_activity(input: GetScheduleActivityInputs) -> ScheduleRead:
        """Temporal activity to get a schedule.

        Parameters
        ----------
        input : GetScheduleActivityInputs
            The input parameters for retrieving the schedule.

        Returns
        -------
        ScheduleRead
            The schedule information.

        Raises
        ------
        TracecatNotFoundError
            If the schedule is not found.
        """
        async with WorkflowSchedulesService.with_session(role=input.role) as service:
            try:
                schedule = await service.get_schedule(input.schedule_id)
                return ScheduleRead(
                    id=schedule.id,
                    owner_id=schedule.owner_id,
                    created_at=schedule.created_at,
                    updated_at=schedule.updated_at,
                    workflow_id=WorkflowUUID.new(schedule.workflow_id),
                    inputs=schedule.inputs,
                    every=schedule.every,
                    offset=schedule.offset,
                    start_at=schedule.start_at,
                    end_at=schedule.end_at,
                    timeout=schedule.timeout,
                    cron=schedule.cron,
                    status=cast(Literal["online", "offline"], schedule.status),
                )
            except TracecatNotFoundError:
                raise
