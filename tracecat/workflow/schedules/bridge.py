from datetime import datetime, timedelta
from typing import Any

import temporalio.client
from temporalio.api.common.v1 import Payloads
from temporalio.common import TypedSearchAttributes
from temporalio.exceptions import TemporalError

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLRunArgs
from tracecat.identifiers import ScheduleUUID, WorkflowID
from tracecat.identifiers.schedules import AnyScheduleID
from tracecat.logger import logger
from tracecat.workflow.executions.enums import TemporalSearchAttr, TriggerType
from tracecat.workflow.schedules.schemas import ScheduleUpdate


def build_schedule_search_attributes(role: Role) -> TypedSearchAttributes:
    """Build search attributes for scheduled workflows."""
    pairs = [TriggerType.SCHEDULED.to_temporal_search_attr_pair()]
    if role.workspace_id is not None:
        pairs.append(
            TemporalSearchAttr.WORKSPACE_ID.create_pair(str(role.workspace_id))
        )
    return TypedSearchAttributes(search_attributes=pairs)


async def _get_handle(schedule_id: AnyScheduleID) -> temporalio.client.ScheduleHandle:
    schedule_uuid = ScheduleUUID.new(schedule_id)
    client = await get_temporal_client()
    temporal_id = schedule_uuid.to_legacy()
    return client.get_schedule_handle(temporal_id)


async def create_schedule(
    workflow_id: WorkflowID,
    schedule_id: AnyScheduleID,
    role: Role,
    *,
    cron: str | None = None,
    every: timedelta | None = None,
    offset: timedelta | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    timeout: float | None = None,
    paused: bool = False,
) -> temporalio.client.ScheduleHandle:
    # Importing here to avoid circular imports...
    from tracecat.dsl.workflow import DSLWorkflow

    client = await get_temporal_client()

    schedule_kwargs = {}
    if timeout:
        schedule_kwargs["execution_timeout"] = timedelta(seconds=timeout)
    if task_timeout := config.TEMPORAL__TASK_TIMEOUT:
        schedule_kwargs["task_timeout"] = timedelta(seconds=float(task_timeout))

    schedule_uuid = ScheduleUUID.new(schedule_id)
    temporal_schedule_id = schedule_uuid.to_legacy()
    workflow_schedule_id = f"{workflow_id.short()}/{temporal_schedule_id}"

    if (cron is None or not cron.strip()) and every is None:
        raise ValueError("Either cron or every must be provided for a schedule")

    spec_kwargs: dict[str, Any] = {
        "start_at": start_at,
        "end_at": end_at,
    }
    if cron and cron.strip():
        spec_kwargs["cron_expressions"] = [cron]
    elif every is not None:
        spec_kwargs["intervals"] = [
            temporalio.client.ScheduleIntervalSpec(every=every, offset=offset)
        ]

    return await client.create_schedule(
        id=temporal_schedule_id,
        schedule=temporalio.client.Schedule(
            action=temporalio.client.ScheduleActionStartWorkflow(
                DSLWorkflow.run,
                # Scheduled workflow only needs to know the workflow ID
                # and the role of the user who scheduled it. Everything else
                # is pulled inside the workflow itself.
                # Pass the native ScheduleUUID (UUID) - it will be serialized as UUID string
                DSLRunArgs(role=role, wf_id=workflow_id, schedule_id=schedule_uuid),
                id=workflow_schedule_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                typed_search_attributes=build_schedule_search_attributes(role),
                **schedule_kwargs,
            ),
            spec=temporalio.client.ScheduleSpec(**spec_kwargs),
            policy=temporalio.client.SchedulePolicy(
                # Allow overlapping workflows to run in parallel
                overlap=temporalio.client.ScheduleOverlapPolicy.ALLOW_ALL,
            ),
            state=temporalio.client.ScheduleState(paused=paused),
        ),
    )


async def delete_schedule(schedule_id: AnyScheduleID) -> None:
    schedule_uuid = ScheduleUUID.new(schedule_id)
    handle = await _get_handle(schedule_uuid)
    try:
        await handle.delete()
    except TemporalError as e:
        msg = str(e).lower()
        # Check for schedule-specific not found conditions
        if any(
            phrase in msg
            for phrase in ["schedule not found", "not found", "does not exist"]
        ):
            logger.warning(
                f"Temporal schedule {schedule_uuid.to_legacy()} not found, skipping deletion"
            )
            return None
        if "workflow execution already completed" not in msg:
            raise RuntimeError(f"Error deleting schedule: {e}") from e
    return None


async def update_schedule(schedule_id: AnyScheduleID, params: ScheduleUpdate) -> None:
    async def _update_schedule(
        input: temporalio.client.ScheduleUpdateInput,
    ) -> temporalio.client.ScheduleUpdate:
        set_fields = params.model_dump(exclude_unset=True)
        action = input.description.schedule.action
        spec = input.description.schedule.spec
        state = input.description.schedule.state

        if "status" in set_fields:
            state.paused = set_fields["status"] != "online"
        if isinstance(action, temporalio.client.ScheduleActionStartWorkflow):
            # Extract role from existing schedule to rebuild search attributes
            from tracecat.workflow.executions.common import extract_first

            try:
                raw_args = await extract_first(Payloads(payloads=[action.args[0]]))
                run_args = DSLRunArgs.model_validate(raw_args)
                role = run_args.role
            except Exception as e:
                logger.warning(
                    "Error extracting role from schedule action",
                    error=e,
                )
                role = Role(type="service", service_id="tracecat-schedule-runner")
            action.typed_search_attributes = build_schedule_search_attributes(role)
            if "inputs" in set_fields:
                action.args[0].dsl.trigger_inputs = set_fields["inputs"]  # type: ignore
        else:
            raise NotImplementedError(
                "Only ScheduleActionStartWorkflow is supported for now."
            )
        # We only support one interval per schedule for now
        cron_enabled = False
        if "cron" in set_fields:
            cron = set_fields["cron"]
            if cron:
                spec.cron_expressions = [cron]
                # Prefer cron schedules over intervals when explicitly provided
                spec.intervals = []
                cron_enabled = True
            else:
                spec.cron_expressions = []

        # Determine interval configuration if requested
        if not cron_enabled and ("every" in set_fields or "offset" in set_fields):
            current_interval = spec.intervals[0] if spec.intervals else None
            every = set_fields.get("every", getattr(current_interval, "every", None))
            offset = set_fields.get("offset", getattr(current_interval, "offset", None))

            if every is None:
                spec.intervals = []
            else:
                spec.intervals = [
                    temporalio.client.ScheduleIntervalSpec(every=every, offset=offset)
                ]
                # Clear cron expressions when switching to interval-based scheduling
                # to prevent double-triggering
                spec.cron_expressions = []
        if "start_at" in set_fields:
            spec.start_at = set_fields["start_at"]
        if "end_at" in set_fields:
            spec.end_at = set_fields["end_at"]

        return temporalio.client.ScheduleUpdate(schedule=input.description.schedule)

    handle = await _get_handle(schedule_id)
    return await handle.update(_update_schedule)
