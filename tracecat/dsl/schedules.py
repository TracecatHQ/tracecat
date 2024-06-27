import re
from datetime import datetime, timedelta
from typing import Any, TypeVar

from pydantic import ValidationInfo, ValidatorFunctionWrapHandler, WrapValidator
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleDescription,
    ScheduleHandle,
    ScheduleIntervalSpec,
    ScheduleListDescription,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from tracecat import config, identifiers
from tracecat.contexts import ctx_role
from tracecat.dsl.common import DSLInput, get_temporal_client
from tracecat.dsl.workflow import DSLRunArgs, DSLWorkflow

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


async def create_schedule(
    workflow_id: identifiers.WorkflowID,
    schedule_id: str,
    dsl: DSLInput,
    # Schedule config
    every: timedelta,
    offset: timedelta | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    **kwargs: Any,
) -> ScheduleHandle:
    client = await get_temporal_client()

    exec_id = identifiers.workflow.exec_id(workflow_id)
    return await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                DSLWorkflow.run,
                # The args that should run in the scheduled workflow
                DSLRunArgs(dsl=dsl, role=ctx_role.get(), wf_id=workflow_id),
                id=exec_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=every, offset=offset)],
                start_at=start_at,
                end_at=end_at,
            ),
            state=ScheduleState(note="Here's a note on my Schedule."),
        ),
        **kwargs,
    )


async def delete_schedule(sch_id: str) -> ScheduleHandle:
    client = await get_temporal_client()
    handle = client.get_schedule_handle(sch_id)
    return await handle.delete()


async def update_schedule(input: ScheduleUpdateInput) -> ScheduleUpdate:
    schedule_action = input.description.schedule.action

    if isinstance(schedule_action, ScheduleActionStartWorkflow):
        schedule_action.args = [
            DSLRunArgs(dsl=input.description.schedule.action.args.dsl)
        ]
    ScheduleUpdate(schedule=input.description.schedule)


async def get_schedule() -> ScheduleDescription:
    client = await get_temporal_client()
    handle = client.get_schedule_handle(
        "workflow-schedule-id",
    )

    desc = await handle.describe()

    print(f"Returns the note: {desc.schedule.state.note}")


async def list_schedules() -> list[ScheduleListDescription]:
    client = await get_temporal_client()
    res = []
    async for schedule in await client.list_schedules():
        res.append(schedule)
    return res
