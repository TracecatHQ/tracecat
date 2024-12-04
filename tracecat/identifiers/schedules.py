"""Schedule identifiers."""

from typing import Annotated

from pydantic import StringConstraints

# Patterns
SCHEDULE_ID_PATTERN = r"sch-[0-9a-f]{32}"
SCHEDULE_EXEC_ID_PATTERN = rf"{SCHEDULE_ID_PATTERN}-.*"

# Annotations
ScheduleID = Annotated[str, StringConstraints(pattern=SCHEDULE_ID_PATTERN)]
"""A unique ID for a schedule.

This is the equivalent of a Schedule ID in Temporal.

Exapmles
--------
- `sch-77932a0b140a4465a1a25a5c95edcfb8`
"""

ScheduleExecutionID = Annotated[
    str, StringConstraints(pattern=SCHEDULE_EXEC_ID_PATTERN)
]
"""The full unique ID for a scheduled workflow execution."""
