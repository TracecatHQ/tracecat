import re
from datetime import datetime, timedelta
from typing import Any, TypeVar

from pydantic import ValidationInfo, ValidatorFunctionWrapHandler, WrapValidator
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleHandle,
    ScheduleIntervalSpec,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from tracecat import config, identifiers
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput
from tracecat.dsl.workflow import DSLRunArgs, DSLWorkflow
from tracecat.types.api import UpdateScheduleParams

T = TypeVar("T")

EASY_TD_PATTERN = (
    r"^"  # Start of string
    r"(?:(?P<weeks>\d+)w)?"  # Match weeks
    r"(?:(?P<days>\d+)d)?"  # Match days
    r"(?:(?P<hours>\d+)h)?"  # Match hours
    r"(?:(?P<minutes>\d+)m)?"  # Match minutes
    r"(?:(?P<seconds>\d+)s)?"  # Match seconds
    r"$"  # End of string
)


class EasyTimedelta:
    def __new__(cls):
        return WrapValidator(cls.maybe_str2timedelta)

    @classmethod
    def maybe_str2timedelta(
        cls, v: T, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> T:
        if isinstance(v, str):
            # If it's a string, try to parse it as a timedelta
            try:
                return string_to_timedelta(v)
            except ValueError:
                pass
        # Otherwise, handle as normal
        return handler(v, info)


def string_to_timedelta(time_str: str) -> timedelta:
    # Regular expressions to match different time units with named capture groups
    pattern = re.compile(
        r"^"  # Start of string
        r"(?:(?P<weeks>\d+)w)?"  # Match weeks
        r"(?:(?P<days>\d+)d)?"  # Match days
        r"(?:(?P<hours>\d+)h)?"  # Match hours
        r"(?:(?P<minutes>\d+)m)?"  # Match minutes
        r"(?:(?P<seconds>\d+)s)?"  # Match seconds
        r"$"  # End of string
    )
    match = pattern.match(time_str)

    if not match:
        raise ValueError("Invalid time format")

    # Extracting the values, defaulting to 0 if not present
    weeks = int(match.group("weeks") or 0)
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)

    # Check if all values are zero
    if all(v == 0 for v in (weeks, days, hours, minutes, seconds)):
        raise ValueError("Invalid time format. All values are zero.")

    # Creating a timedelta object
    return timedelta(
        days=days, weeks=weeks, hours=hours, minutes=minutes, seconds=seconds
    )


async def _get_handle(sch_id: identifiers.ScheduleID) -> ScheduleHandle:
    client = await get_temporal_client()
    return client.get_schedule_handle(sch_id)


async def create_schedule(
    workflow_id: identifiers.WorkflowID,
    schedule_id: identifiers.ScheduleID,
    dsl: DSLInput,
    # Schedule config
    every: timedelta,
    offset: timedelta | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    **kwargs: Any,
) -> ScheduleHandle:
    client = await get_temporal_client()

    workflow_schedule_id = f"{workflow_id}:{schedule_id}"
    return await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                DSLWorkflow.run,
                # The args that should run in the scheduled workflow
                DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=workflow_id),
                id=workflow_schedule_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=every, offset=offset)],
                start_at=start_at,
                end_at=end_at,
            ),
        ),
        **kwargs,
    )


async def delete_schedule(schedule_id: identifiers.ScheduleID) -> ScheduleHandle:
    handle = await _get_handle(schedule_id)
    try:
        return await handle.delete()
    except Exception as e:
        if "workflow execution already completed" in str(e):
            # This is fine, we can ignore this error
            return
        raise e


async def update_schedule(
    schedule_id: identifiers.ScheduleID, params: UpdateScheduleParams
) -> ScheduleUpdate:
    async def _update_schedule(input: ScheduleUpdateInput) -> ScheduleUpdate:
        set_fields = params.model_dump(exclude_unset=True)
        action = input.description.schedule.action
        spec = input.description.schedule.spec
        state = input.description.schedule.state

        if "status" in set_fields:
            state.paused = set_fields["status"] != "online"
        if isinstance(action, ScheduleActionStartWorkflow):
            if "inputs" in set_fields:
                action.args[0].dsl.trigger_inputs = set_fields["inputs"]
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

        return ScheduleUpdate(schedule=input.description.schedule)

    handle = await _get_handle(schedule_id)
    return await handle.update(_update_schedule)
