from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from temporalio import activity

from tracecat.authz.controls import require_scope
from tracecat.db.engine import get_async_session_bypass_rls_context_manager
from tracecat.db.models import Schedule, Workspace
from tracecat.db.session_events import add_after_commit_callback
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import OrganizationID, ScheduleUUID, WorkflowID, WorkspaceID
from tracecat.identifiers.schedules import AnyScheduleID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.storage.object import InlineObject
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import (
    GetScheduleActivityInputs,
    ScheduleCreate,
    ScheduleUpdate,
)


class WorkflowSchedulesService(BaseWorkspaceService):
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
        statement = select(Schedule).where(Schedule.workspace_id == self.workspace_id)
        if workflow_id is not None:
            statement = statement.where(Schedule.workflow_id == workflow_id)
        result = await self.session.execute(statement)
        schedules = result.scalars().all()
        return list(schedules)

    @require_scope("schedule:create")
    async def create_schedule(
        self, params: ScheduleCreate, commit: bool = True
    ) -> Schedule:
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
        schedule = Schedule(
            workspace_id=self.workspace_id,
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
        await self.session.flush()

        role_copy = self.role.model_copy(
            update={
                "type": "service",
                "service_id": "tracecat-schedule-runner",
                "user_id": None,
            }
        )

        # Register after-commit callback to create Temporal schedule
        schedule_id = schedule.id

        async def _create_schedule():
            try:
                handle = await bridge.create_schedule(
                    workflow_id=WorkflowUUID.new(params.workflow_id),
                    schedule_id=schedule_id,
                    cron=params.cron,
                    every=params.every,
                    offset=params.offset,
                    start_at=params.start_at,
                    end_at=params.end_at,
                    timeout=params.timeout,
                    role=role_copy,
                )
                logger.info(
                    "Created schedule",
                    handle_id=handle.id,
                    workflow_id=params.workflow_id,
                    schedule_id=schedule_id,
                    schedule_role=role_copy,
                )
            except Exception as e:
                # Log; optionally wire to a retry/outbox
                logger.error(
                    "The schedules service couldn't create a Temporal schedule after commit",
                    error=str(e),
                    workflow_id=params.workflow_id,
                    schedule_id=schedule_id,
                    schedule_role=role_copy,
                )

        add_after_commit_callback(self.session, _create_schedule)

        # Ensure the SQLAlchemy instance is persistent before refresh.
        # Commit will implicitly flush; when commit=False we must flush explicitly
        # or SQLAlchemy will raise "Instance is not persistent within this Session".
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        await self.session.refresh(schedule)
        return schedule

    async def get_schedule(self, schedule_id: AnyScheduleID) -> Schedule:
        """
        Retrieve a schedule by its ID.

        Parameters
        ----------
        schedule_id : AnyScheduleID
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
        schedule_uuid = ScheduleUUID.new(schedule_id)
        result = await self.session.execute(
            select(Schedule).where(
                Schedule.workspace_id == self.workspace_id,
                Schedule.id == schedule_uuid,
            )
        )
        try:
            return result.scalar_one()
        except NoResultFound as e:
            raise TracecatNotFoundError(f"Schedule {schedule_uuid} not found") from e

    @require_scope("schedule:update")
    async def update_schedule(
        self, schedule_id: AnyScheduleID, params: ScheduleUpdate
    ) -> Schedule:
        """
        Update a schedule with the given schedule ID and parameters.

        Parameters
        ----------
        schedule_id : AnyScheduleID
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

        # Update the schedule in DB first
        for key, value in params.model_dump(exclude_unset=True).items():
            # Safety: params have been validated
            setattr(schedule, key, value)

        self.session.add(schedule)

        # After-commit callback to update Temporal schedule
        async def _update_schedule():
            try:
                await bridge.update_schedule(schedule_id, params)
                logger.info(
                    "Updated schedule",
                    schedule_id=schedule_id,
                )
            except Exception as e:
                logger.error(
                    "The schedules service couldn't update the Temporal schedule after commit",
                    error=str(e),
                    schedule_id=schedule_id,
                )

        add_after_commit_callback(self.session, _update_schedule)

        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    @require_scope("schedule:delete")
    async def delete_schedule(
        self, schedule_id: AnyScheduleID, commit: bool = True
    ) -> None:
        """
        Delete a schedule.

        Parameters
        ----------
        schedule_id : AnyScheduleID
            The ID of the schedule to be deleted.

        Raises
        ------
        TracecatServiceError
            If an error occurs while deleting the schedule from Temporal.

        """
        # Stage DB delete (if exists)
        try:
            schedule = await self.get_schedule(schedule_id)
            await self.session.delete(schedule)
            logger.info("Deleted schedule", schedule_id=schedule_id)
        except NoResultFound:
            logger.warning(
                "Schedule not found in DB; will still attempt Temporal delete after commit",
                schedule_id=schedule_id,
            )

        # After-commit callback to delete Temporal schedule
        async def _delete_schedule():
            try:
                await bridge.delete_schedule(schedule_id)
                logger.info(
                    "Deleted Temporal schedule",
                    schedule_id=schedule_id,
                )
            except RuntimeError as e:
                logger.error(
                    "The schedules service couldn't delete the Temporal schedule after commit",
                    error=str(e),
                    schedule_id=schedule_id,
                )

        add_after_commit_callback(self.session, _delete_schedule)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

    @staticmethod
    @activity.defn
    async def get_workspace_organization_id_activity(
        workspace_id: WorkspaceID,
    ) -> OrganizationID | None:
        """Resolve organization_id for a workspace.

        This activity is used to heal legacy scheduled workflow roles that are
        missing organization_id in their serialized schedule arguments.
        """
        async with get_async_session_bypass_rls_context_manager() as session:
            stmt = select(Workspace.organization_id).where(Workspace.id == workspace_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    @staticmethod
    @activity.defn
    async def get_schedule_trigger_inputs_activity(
        input: GetScheduleActivityInputs,
    ) -> InlineObject | None:
        """Temporal activity to get schedule trigger inputs.

        Parameters
        ----------
        input : GetScheduleActivityInputs
            The input parameters for retrieving the schedule.

        Returns
        -------
        InlineObject | None
            The schedule trigger inputs wrapped in InlineObject, or None if no inputs.

        Raises
        ------
        TracecatNotFoundError
            If the schedule is not found.
        """
        async with WorkflowSchedulesService.with_session(role=input.role) as service:
            try:
                schedule = await service.get_schedule(input.schedule_id)
                if schedule.inputs is None:
                    return None
                return InlineObject(data=schedule.inputs)
            except TracecatNotFoundError:
                raise
