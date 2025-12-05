"""Schedule identifiers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints

from tracecat.identifiers.common import TracecatUUID

# Prefixes
SCH_ID_PREFIX = "sch_"
LEGACY_SCHEDULE_ID_PREFIX = "sch-"

# Patterns for validation
_SCH_ID_SHORT_PATTERN = rf"{SCH_ID_PREFIX}[0-9a-zA-Z]+"
_LEGACY_SCHEDULE_ID_PATTERN = r"sch-[0-9a-f]{32}"
_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# Used by workflow.py for execution ID matching
TEMPORAL_SCHEDULE_ID_PATTERN = rf"(?:{_LEGACY_SCHEDULE_ID_PATTERN}|{_UUID_PATTERN})"
SCHEDULE_EXEC_ID_PATTERN = rf"{TEMPORAL_SCHEDULE_ID_PATTERN}-.*"

# Short ID type (used as TracecatUUID type parameter)
ScheduleIDShort = Annotated[str, StringConstraints(pattern=_SCH_ID_SHORT_PATTERN)]
LegacyScheduleID = Annotated[
    str, StringConstraints(pattern=_LEGACY_SCHEDULE_ID_PATTERN)
]


class ScheduleUUID(TracecatUUID[ScheduleIDShort]):
    """UUID for schedule resources.

    Supports:
    - Native UUID format (database storage)
    - Short ID format: `sch_xxx`
    - Legacy format: `sch-<32hex>` (Temporal compatibility)
    """

    prefix = SCH_ID_PREFIX
    legacy_prefix = LEGACY_SCHEDULE_ID_PREFIX


AnyScheduleID = ScheduleUUID | ScheduleIDShort | LegacyScheduleID | UUID

TemporalScheduleID = str
"""Schedule ID format used in Temporal APIs (legacy 'sch-<hex>' format)."""


def schedule_id_to_temporal(schedule_id: ScheduleUUID | str) -> TemporalScheduleID:
    """Convert a ScheduleID to legacy format for Temporal APIs."""
    if isinstance(schedule_id, ScheduleUUID):
        return schedule_id.to_legacy()
    return ScheduleUUID.new(schedule_id).to_legacy()
