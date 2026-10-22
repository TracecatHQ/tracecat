from typing import Annotated

from fastapi import Depends
from fastapi.exceptions import RequestValidationError

from tracecat.identifiers import ScheduleUUID


def schedule_id_path_dependency(schedule_id: str) -> ScheduleUUID:
    try:
        return ScheduleUUID.new(schedule_id)
    except ValueError as exc:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("path", "schedule_id"),
                    "msg": "Invalid schedule ID",
                    "input": schedule_id,
                }
            ]
        ) from exc


AnyScheduleIDPath = Annotated[ScheduleUUID, Depends(schedule_id_path_dependency)]
"""A schedule ID that can be either a UUID or a short ID in the format sch_XXXXX."""
