from typing import Annotated

from fastapi import Depends

from tracecat.identifiers import ScheduleUUID


def schedule_id_path_dependency(schedule_id: str) -> ScheduleUUID:
    return ScheduleUUID.new(schedule_id)


AnyScheduleIDPath = Annotated[ScheduleUUID, Depends(schedule_id_path_dependency)]
"""A schedule ID that can be either a UUID or a short ID in the format sch_XXXXX."""
