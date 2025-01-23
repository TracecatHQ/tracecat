from datetime import datetime, timedelta

import temporalio.client
from temporalio.common import TypedSearchAttributes

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLRunArgs
from tracecat.identifiers import ScheduleID, WorkflowID
from tracecat.logger import logger
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.schedules.models import ScheduleUpdate

SEARCH_ATTRS = TypedSearchAttributes(
    search_attributes=[TriggerType.SCHEDULED.to_temporal_search_attr_pair()]
)


async def _get_handle(schedule_id: ScheduleID) -> temporalio.client.ScheduleHandle:
    client = await get_temporal_client()
    return client.get_schedule_handle(schedule_id)


async def create_schedule(
    workflow_id: WorkflowID,
    schedule_id: ScheduleID,
    *,
    every: timedelta,
    offset: timedelta | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    timeout: float | None = None,
) -> temporalio.client.ScheduleHandle:
    # Importing here to avoid circular imports...
    from tracecat.dsl.workflow import DSLWorkflow

    client = await get_temporal_client()

    schedule_kwargs = {}
    if timeout:
        schedule_kwargs["execution_timeout"] = timedelta(seconds=timeout)
    if task_timeout := config.TEMPORAL__TASK_TIMEOUT:
        schedule_kwargs["task_timeout"] = timedelta(seconds=float(task_timeout))

    workflow_schedule_id = f"{workflow_id.short()}/{schedule_id}"

    if config.TEMPORAL__API_KEY__ARN or config.TEMPORAL__API_KEY:
        logger.warning(
            "Using Temporal cloud, skipping search attributes (add to schedule)"
        )
        search_attrs = TypedSearchAttributes.empty
    else:
        search_attrs = SEARCH_ATTRS
    return await client.create_schedule(
        id=schedule_id,
        schedule=temporalio.client.Schedule(
            action=temporalio.client.ScheduleActionStartWorkflow(
                DSLWorkflow.run,
                # Scheduled workflow only needs to know the workflow ID
                # and the role of the user who scheduled it. Everything else
                # is pulled inside the workflow itself.
                DSLRunArgs(
                    role=ctx_role.get(), wf_id=workflow_id, schedule_id=schedule_id
                ),
                id=workflow_schedule_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                typed_search_attributes=search_attrs,
                **schedule_kwargs,
            ),
            spec=temporalio.client.ScheduleSpec(
                intervals=[
                    temporalio.client.ScheduleIntervalSpec(every=every, offset=offset)
                ],
                start_at=start_at,
                end_at=end_at,
            ),
            policy=temporalio.client.SchedulePolicy(
                # Allow overlapping workflows to run in parallel
                overlap=temporalio.client.ScheduleOverlapPolicy.ALLOW_ALL,
            ),
        ),
    )


async def delete_schedule(schedule_id: ScheduleID) -> None:
    handle = await _get_handle(schedule_id)
    try:
        await handle.delete()
    except Exception as e:
        if "workflow execution already completed" not in str(e).lower():
            raise RuntimeError(f"Error deleting schedule: {e}") from e
    return None


async def update_schedule(schedule_id: ScheduleID, params: ScheduleUpdate) -> None:
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
            if config.TEMPORAL__API_KEY__ARN or config.TEMPORAL__API_KEY:
                logger.warning(
                    "Using Temporal cloud, skipping search attributes (update schedule)"
                )
            else:
                action.typed_search_attributes = SEARCH_ATTRS
            if "inputs" in set_fields:
                action.args[0].dsl.trigger_inputs = set_fields["inputs"]  # type: ignore
        else:
            raise NotImplementedError(
                "Only ScheduleActionStartWorkflow is supported for now."
            )
        # We only support one interval per schedule for now
        if "every" in set_fields:
            spec.intervals[0].every = set_fields["every"]
        if "offset" in set_fields:
            spec.intervals[0].offset = set_fields["offset"]
        if "start_at" in set_fields:
            spec.start_at = set_fields["start_at"]
        if "end_at" in set_fields:
            spec.end_at = set_fields["end_at"]

        return temporalio.client.ScheduleUpdate(schedule=input.description.schedule)

    handle = await _get_handle(schedule_id)
    return await handle.update(_update_schedule)
