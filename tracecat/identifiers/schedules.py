"""Schedule identifiers."""

from typing import Annotated

from pydantic import StringConstraints

ScheduleID = Annotated[str, StringConstraints(pattern=r"sch-[0-9a-f]{32}")]
"""A unique ID for a schedule.

This is the equivalent of a Schedule ID in Temporal.

Exapmles
--------
- `sch-77932a0b140a4465a1a25a5c95edcfb8`
"""
