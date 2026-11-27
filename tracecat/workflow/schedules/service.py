from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import NoResultFound
from temporalio import activity

from tracecat.audit.logger import AuditEventDetails, audit_log
from tracecat.authz.controls import require_scope
from tracecat.db.models import Schedule, Trigger, TriggerBinding, Workflow
from tracecat.db.session_events import AfterCommitQueue
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.identifiers import ScheduleUUID, WorkflowID
from tracecat.identifiers.schedules import AnyScheduleID
from tracecat.identifiers.workflow import AnyWorkflowID, WorkflowUUID
from tracecat.logger import logger
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorClassification,
    RuntimeErrorKind,
)
from tracecat.service import BaseWorkspaceService
from tracecat.storage.object import InlineObject
from tracecat.temporal.errors import raise_application_error_from_classification
from tracecat.triggers.enums import TriggerKind, TriggerStatus, TriggerTargetType
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.reconciliation import ScheduleChanges
from tracecat.workflow.schedules.schemas import (
    GetScheduleActivityInputs,
    ScheduleCreate,
    ScheduleUpdate,
)

# The trigger name column is 255 characters.
_NAME_MAX = 255


def _describe_interval(every: timedelta) -> str:
    """A compact interval such as "1d 2h", "30m" or "0.5s"."""
    whole = int(every.total_seconds())
    fraction = every.total_seconds() - whole
    parts: list[str] = []
    for unit, size in (("d", 86_400), ("h", 3_600), ("m", 60)):
        count, whole = divmod(whole, size)
        if count:
            parts.append(f"{count}{unit}")
    # Seconds keep any fraction: a 500 ms interval is "0.5s", not "0s".
    seconds = round(whole + fraction, 3)
    if seconds or not parts:
        parts.append(f"{seconds:g}s")
    return " ".join(parts)


def default_trigger_name(
    workflow_title: str | None,
    *,
    cron: str | None = None,
    every: timedelta | None = None,
) -> str:
    """The name a schedule's trigger gets when none is given.

    The workflow title plus " schedule" and when it runs, such as
    "Nightly report schedule (0 9 * * *)" or "Sync schedule (every 1h)", so
    several schedules of one workflow get different names. "Schedule" for an
    untitled workflow. The title is cut so the name fits the column.
    Backfilled rows (migration ``61ebd010e726``) have the name without the
    spec.
    """
    spec = ""
    if cron and cron.strip():
        spec = f" ({cron.strip()})"
    elif every is not None:
        spec = f" (every {_describe_interval(every)})"
    title = (workflow_title or "").strip()
    if not title:
        return f"Schedule{spec}"[:_NAME_MAX]
    suffix = f" schedule{spec}"
    return f"{title[: max(_NAME_MAX - len(suffix), 0)]}{suffix}"[:_NAME_MAX]


@dataclass(frozen=True)
class DeletedSchedule:
    """The ID of a schedule that was just deleted."""

    id: uuid.UUID


class WorkflowSchedulesService(BaseWorkspaceService):
    """Manages schedules for Workflows.

    Each schedule also has a ``schedule`` trigger row with one workflow binding,
    so it shows in the Triggers tab. This service writes them with the schedule
    in one transaction. Deleting a schedule deletes its trigger row through a
    database trigger, which also covers a cascade from the workflow.
    """

    service_name = "workflow_schedules"

    def _suppress_audit_when_deferred_commit(
        self, params: ScheduleCreate, commit: bool = True
    ) -> AuditEventDetails:
        if commit is False:
            return AuditEventDetails(emit=False)
        return AuditEventDetails()

    def _schedule_update_audit_details(
        self, schedule_id: AnyScheduleID, params: ScheduleUpdate
    ) -> AuditEventDetails:
        return AuditEventDetails(
            data={"changed_fields": sorted(params.model_fields_set)}
        )

    def _suppress_delete_audit_when_deferred_commit(
        self, schedule_id: AnyScheduleID, commit: bool = True
    ) -> AuditEventDetails:
        if commit is False:
            return AuditEventDetails(emit=False)
        return AuditEventDetails()

    async def _lock_workflow(self, workflow_id: AnyWorkflowID | uuid.UUID) -> str:
        """Lock the workflow row until commit and return its title."""
        workflow_uuid = WorkflowUUID.new(workflow_id)
        stmt = (
            select(Workflow.id, Workflow.title)
            .where(
                Workflow.workspace_id == self.workspace_id,
                Workflow.id == workflow_uuid,
            )
            .with_for_update()
        )
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            raise TracecatNotFoundError(f"Workflow {workflow_id} not found")
        return row.title

    async def _add_trigger(
        self,
        *,
        workflow_id: uuid.UUID,
        status: str,
        name: str,
        created_at: datetime | None = None,
    ) -> uuid.UUID:
        """Add a ``schedule`` trigger row and its workflow binding.

        The binding is the schedule's workflow, the only target a schedule
        runs until schedules start their targets through bindings.
        """
        trigger = Trigger(
            id=uuid.uuid4(),
            workspace_id=self.workspace_id,
            kind=TriggerKind.SCHEDULE.value,
            name=name,
            status=status,
        )
        if created_at is not None:
            trigger.created_at = created_at
        self.session.add(trigger)
        # The binding's and the schedule's foreign keys need the row first.
        await self.session.flush()
        self.session.add(
            TriggerBinding(
                workspace_id=self.workspace_id,
                trigger_id=trigger.id,
                target_type=TriggerTargetType.WORKFLOW.value,
                workflow_id=workflow_id,
                status=TriggerStatus.ONLINE.value,
            )
        )
        return trigger.id

    async def _require_published_workflow(
        self, workflow_id: AnyWorkflowID | uuid.UUID
    ) -> None:
        """Validate the external create precondition inside the audit boundary."""
        workflow_uuid = WorkflowUUID.new(workflow_id)
        stmt = select(Workflow).where(
            Workflow.workspace_id == self.workspace_id,
            Workflow.id == workflow_uuid,
        )
        workflow = await self.session.scalar(stmt)
        if workflow is None:
            raise TracecatNotFoundError(
                "Workflow not found. Please check the workflow ID and try again."
            )
        if workflow.version is None:
            raise TracecatConflictError(
                "Workflow must be saved before creating a schedule."
            )

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
    @audit_log(
        resource_type="schedule",
        action="create",
        resource_id_attr="id",
        attempt_metadata=_suppress_audit_when_deferred_commit,
    )
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
        if commit:
            await self._require_published_workflow(params.workflow_id)
        return await self._create_schedule_impl(params, commit=commit)

    async def _create_schedule_impl(
        self,
        params: ScheduleCreate,
        commit: bool = True,
        *,
        trigger_name: str | None = None,
    ) -> Schedule:
        """Implement schedule creation for an authorized caller.

        This internal method lets other methods on this service compose schedule
        changes under their own authorization and audit lifecycle. External
        callers must use :meth:`create_schedule`.

        Args:
            params: Configuration for the new schedule.
            commit: Whether to commit immediately. When ``False``, the method
                flushes the row and leaves the transaction and registered
                Temporal callback to the caller.
            trigger_name: Name of the schedule's trigger row. Defaults to
                ``default_trigger_name`` of the workflow title and spec.

        Returns:
            The newly created schedule.
        """
        title = await self._lock_workflow(params.workflow_id)
        workflow_uuid = WorkflowUUID.new(params.workflow_id)
        trigger_id = await self._add_trigger(
            workflow_id=workflow_uuid,
            status=params.status,
            name=trigger_name
            or default_trigger_name(title, cron=params.cron, every=params.every),
        )
        schedule = Schedule(
            workspace_id=self.workspace_id,
            workflow_id=workflow_uuid,
            trigger_id=trigger_id,
            inputs=params.inputs or {},
            every=params.every,
            offset=params.offset,
            start_at=params.start_at,
            end_at=params.end_at,
            timeout=params.timeout,
            cron=params.cron,
            status=params.status,
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
                    status=params.status,
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

        if changes := ScheduleChanges.of(self.session):
            changes.created.append(schedule_id)
        else:
            AfterCommitQueue.of(self.session).add(_create_schedule)

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

    async def _get_schedule_with_workflow_lock(
        self, schedule_id: AnyScheduleID
    ) -> Schedule:
        schedule_uuid = ScheduleUUID.new(schedule_id)
        result = await self.session.execute(
            select(Schedule)
            .join(Workflow, Workflow.id == Schedule.workflow_id)
            .where(
                Schedule.workspace_id == self.workspace_id,
                Schedule.id == schedule_uuid,
                Workflow.workspace_id == self.workspace_id,
            )
            # The schedule row too, read fresh: an update that adopts a
            # schedule without a trigger row must see a concurrent adoption.
            .with_for_update(of=(Workflow, Schedule))
            .execution_options(populate_existing=True)
        )
        try:
            return result.scalar_one()
        except NoResultFound as e:
            raise TracecatNotFoundError(f"Schedule {schedule_uuid} not found") from e

    @require_scope("schedule:update")
    @audit_log(
        resource_type="schedule",
        action="update",
        attempt_metadata=_schedule_update_audit_details,
    )
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
        schedule = await self._get_schedule_with_workflow_lock(schedule_id)
        await self._update_schedule_impl(schedule, params)
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def _update_schedule_impl(
        self,
        schedule: Schedule,
        params: ScheduleUpdate,
        *,
        trigger_name: str | None = None,
        temporal_inputs: bool = True,
    ) -> None:
        """Apply ``params`` to a schedule whose workflow the caller locked.

        ``temporal_inputs=False`` keeps ``inputs`` out of the Temporal update:
        scheduled runs read their inputs from this row when they start.

        Writes the schedule's trigger row in the same transaction: its status
        and timestamp, or a new row for a schedule the previous release made
        without one. Leaves the commit, and the Temporal update it triggers,
        to the caller.
        """
        schedule_id = schedule.id
        for key, value in params.model_dump(exclude_unset=True).items():
            # Safety: params have been validated
            setattr(schedule, key, value)
        self.session.add(schedule)

        if schedule.trigger_id is None:
            title = await self.session.scalar(
                select(Workflow.title).where(
                    Workflow.workspace_id == self.workspace_id,
                    Workflow.id == schedule.workflow_id,
                )
            )
            schedule.trigger_id = await self._add_trigger(
                workflow_id=schedule.workflow_id,
                status=schedule.status,
                name=default_trigger_name(
                    title, cron=schedule.cron, every=schedule.every
                ),
                created_at=schedule.created_at,
            )
        else:
            # The header's timestamp covers its settings too. The status is
            # written only when this update sets it: the loaded row may predate
            # a concurrent status change, and the database trigger copies
            # every status change anyway.
            values: dict[str, object] = {"updated_at": func.now()}
            if "status" in params.model_fields_set:
                values["status"] = schedule.status
            if trigger_name is not None:
                values["name"] = trigger_name
            await self.session.execute(
                update(Trigger)
                .where(
                    Trigger.workspace_id == self.workspace_id,
                    Trigger.id == schedule.trigger_id,
                )
                .values(**values)
            )

        # After-commit callback to update Temporal schedule
        async def _update_schedule():
            try:
                await bridge.update_schedule(schedule_id, temporal_params)
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

        temporal_params = params
        if not temporal_inputs and "inputs" in params.model_fields_set:
            temporal_params = ScheduleUpdate.model_validate(
                params.model_dump(exclude_unset=True, exclude={"inputs"})
            )
        if temporal_params.model_fields_set:
            AfterCommitQueue.of(self.session).add(_update_schedule)

    @require_scope("schedule:delete")
    @audit_log(
        resource_type="schedule",
        action="delete",
        attempt_metadata=_suppress_delete_audit_when_deferred_commit,
    )
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
        await self._delete_schedule_impl(schedule_id, commit=commit)

    async def _delete_schedule_impl(
        self, schedule_id: AnyScheduleID, commit: bool = True
    ) -> None:
        """Implement schedule deletion for an authorized caller.

        This internal method lets other methods on this service compose schedule
        changes under their own authorization and audit lifecycle. External
        callers must use :meth:`delete_schedule`.

        Args:
            schedule_id: ID of the schedule to delete.
            commit: Whether to commit immediately. When ``False``, the method
                flushes the deletion and leaves the transaction and registered
                Temporal callback to the caller.
        """
        schedule = await self._get_schedule_with_workflow_lock(schedule_id)
        # The database trigger on schedule deletes its trigger row and binding.
        await self.session.delete(schedule)
        logger.info("Deleted schedule", schedule_id=schedule_id)

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

        if changes := ScheduleChanges.of(self.session):
            changes.deleted.append(schedule.id)
        else:
            AfterCommitQueue.of(self.session).add(_delete_schedule)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

    # ----- Entry points for the Triggers tab -----
    # Each writes the schedule, its trigger row and binding, and commits, so
    # its ``schedule`` audit record follows the commit as on ``/schedules``.
    # Each needs its schedule scope: the Triggers tab is another way in, not
    # a way around it.

    @require_scope("schedule:create")
    @audit_log(resource_type="schedule", action="create", resource_id_attr="id")
    async def create_trigger_schedule(
        self, params: ScheduleCreate, *, trigger_name: str
    ) -> Schedule:
        """Create a schedule and its trigger row, as ``create_schedule`` does.

        Raises:
            TracecatNotFoundError: If the workflow is not in the workspace.
            TracecatConflictError: If the workflow was never saved.
        """
        await self._require_published_workflow(params.workflow_id)
        return await self._create_schedule_impl(
            params, commit=True, trigger_name=trigger_name
        )

    def _trigger_update_audit_details(
        self,
        trigger_id: uuid.UUID,
        params: ScheduleUpdate,
        *,
        trigger_name: str | None = None,
    ) -> AuditEventDetails:
        changed = set(params.model_fields_set)
        if trigger_name is not None:
            changed.add("name")
        return AuditEventDetails(data={"changed_fields": sorted(changed)})

    @require_scope("schedule:update")
    @audit_log(
        resource_type="schedule",
        action="update",
        resource_id_attr="id",
        attempt_metadata=_trigger_update_audit_details,
    )
    async def update_trigger_schedule(
        self,
        trigger_id: uuid.UUID,
        params: ScheduleUpdate,
        *,
        trigger_name: str | None = None,
    ) -> Schedule:
        """Update the schedule behind a ``schedule`` trigger.

        Raises:
            TracecatNotFoundError: If no schedule belongs to the trigger.
        """
        schedule = await self._get_trigger_schedule(trigger_id)
        # Inputs are saved on the row and kept out of the Temporal update:
        # runs read them from the row, and the bridge's inputs update fails,
        # which would drop the spec change with it. The spec fields always go
        # together, so the Temporal schedule never ends up with neither cron
        # nor interval.
        await self._update_schedule_impl(
            schedule, params, trigger_name=trigger_name, temporal_inputs=False
        )
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    @require_scope("schedule:delete")
    @audit_log(resource_type="schedule", action="delete", resource_id_attr="id")
    async def delete_trigger_schedule(self, trigger_id: uuid.UUID) -> DeletedSchedule:
        """Delete the schedule behind a ``schedule`` trigger, and so the trigger.

        Returns:
            The deleted schedule's ID, for its audit record.

        Raises:
            TracecatNotFoundError: If no schedule belongs to the trigger.
        """
        schedule = await self._get_trigger_schedule(trigger_id)
        schedule_id = schedule.id
        await self._delete_schedule_impl(schedule_id, commit=True)
        return DeletedSchedule(id=schedule_id)

    async def _get_trigger_schedule(self, trigger_id: uuid.UUID) -> Schedule:
        """The schedule of a trigger, with its workflow locked until commit.

        The schedule row is locked too and read fresh, so a change compared
        against it sees what a concurrent writer committed.
        """
        schedule = await self.session.scalar(
            select(Schedule)
            .join(Workflow, Workflow.id == Schedule.workflow_id)
            .where(
                Schedule.workspace_id == self.workspace_id,
                Schedule.trigger_id == trigger_id,
                Workflow.workspace_id == self.workspace_id,
            )
            .with_for_update(of=(Workflow, Schedule))
            .execution_options(populate_existing=True)
        )
        if schedule is None:
            raise TracecatNotFoundError(f"No schedule for trigger {trigger_id}")
        return schedule

    @require_scope("workflow:update")
    async def replace_schedules(
        self,
        workflow_id: WorkflowID,
        schedules: Sequence[ScheduleCreate],
        commit: bool = True,
    ) -> None:
        """Replace all schedules for a workflow as part of a workflow edit.

        This is the internal surface used by the edit-workflow document path. It
        is gated by ``workflow:update`` (the workflow-edit permission) rather
        than the standalone ``schedule:create``/``schedule:delete`` scopes, so a
        user who is allowed to edit workflows can change their schedules without
        also holding the admin-only ``schedule:delete`` scope. The delete and
        create work is committed as a single transaction with the rest of the
        edit.
        """
        existing = await self.list_schedules(workflow_id=workflow_id)
        for schedule in existing:
            await self._delete_schedule_impl(schedule.id, commit=False)
        for params in schedules:
            await self._create_schedule_impl(params, commit=False)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

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
        ApplicationError
            If the schedule cannot be loaded, with a classified Temporal failure.
        """
        try:
            async with WorkflowSchedulesService.with_session(
                role=input.role
            ) as service:
                schedule = await service.get_schedule(input.schedule_id)
                if schedule.inputs is None:
                    return None
                return InlineObject(data=schedule.inputs)
        except TracecatNotFoundError as e:
            classification = RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.WORKFLOW_BOOTSTRAP_INVALID_DATA,
                message="Tracecat could not load the scheduled workflow trigger",
                retry_disposition=RetryDisposition.NON_RETRYABLE,
                cause=e,
            )
            raise_application_error_from_classification(classification)
        except Exception as e:
            classification = RuntimeErrorClassification.platform(
                kind=RuntimeErrorKind.WORKFLOW_BOOTSTRAP_UNAVAILABLE,
                message="Tracecat could not load the scheduled workflow trigger",
                retry_disposition=RetryDisposition.RETRYABLE,
                cause=e,
            )
            raise_application_error_from_classification(classification)
