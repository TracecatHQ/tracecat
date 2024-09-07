from datetime import datetime, timedelta
from typing import Any

import temporalio.client

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.identifiers import ScheduleID, WorkflowID
from tracecat.workflow.schedules.models import ScheduleUpdate


async def _get_handle(schedule_id: ScheduleID) -> temporalio.client.ScheduleHandle:
    client = await get_temporal_client()
    return client.get_schedule_handle(schedule_id)


async def create_schedule(
    workflow_id: WorkflowID,
    schedule_id: ScheduleID,
    dsl: DSLInput,
    # Schedule config
    every: timedelta,
    offset: timedelta | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    trigger_inputs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> temporalio.client.ScheduleHandle:
    client = await get_temporal_client()

    workflow_schedule_id = f"{workflow_id}:{schedule_id}"
    return await client.create_schedule(
        schedule_id,
        temporalio.client.Schedule(
            action=temporalio.client.ScheduleActionStartWorkflow(
                DSLWorkflow.run,
                # The args that should run in the scheduled workflow
                DSLRunArgs(
                    dsl=dsl,
                    role=ctx_role.get(),
                    wf_id=workflow_id,
                    trigger_inputs=trigger_inputs,
                ),
                id=workflow_schedule_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            ),
            spec=temporalio.client.ScheduleSpec(
                intervals=[
                    temporalio.client.ScheduleIntervalSpec(every=every, offset=offset)
                ],
                start_at=start_at,
                end_at=end_at,
            ),
        ),
        **kwargs,
    )


async def delete_schedule(schedule_id: ScheduleID) -> None:
    handle = await _get_handle(schedule_id)
    try:
        return await handle.delete()
    except Exception as e:
        if "workflow execution already completed" not in str(e):
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
