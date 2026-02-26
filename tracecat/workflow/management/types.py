from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final


@dataclass
class WorkflowDefinitionMinimal:
    """Workflow definition metadata domain model."""

    id: str
    version: int
    created_at: datetime


@dataclass(frozen=True)
class WorkflowTriggerSummaryMinimal:
    """Workflow trigger metadata summarized for list/directory rows."""

    schedule_count_online: int
    schedule_cron: str | None
    schedule_natural: str | None
    webhook_active: bool
    case_trigger_events: tuple[str, ...]


_CRON_WEEKDAY_NAMES: Final[dict[str, str]] = {
    "0": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
    "7": "Sunday",
    "sun": "Sunday",
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
}


def _format_timedelta_natural(duration: timedelta) -> str:
    total_seconds = int(duration.total_seconds())
    if total_seconds <= 0:
        return "0s"

    units: list[str] = []
    day_seconds = 24 * 60 * 60
    hour_seconds = 60 * 60
    minute_seconds = 60

    days, rem = divmod(total_seconds, day_seconds)
    hours, rem = divmod(rem, hour_seconds)
    minutes, seconds = divmod(rem, minute_seconds)

    if days:
        units.append(f"{days}d")
    if hours:
        units.append(f"{hours}h")
    if minutes:
        units.append(f"{minutes}m")
    if seconds and not units:
        units.append(f"{seconds}s")

    return " ".join(units)


def humanize_cron_expression(cron: str) -> str:
    """Return a compact natural-language label for common cron patterns."""
    parts = cron.strip().split()
    if len(parts) not in {5, 6}:
        return f"Cron {cron}"

    if len(parts) == 6:
        _, minute, hour, day_of_month, month, day_of_week = parts
    else:
        minute, hour, day_of_month, month, day_of_week = parts

    if (
        minute in {"*", "*/1"}
        and hour == "*"
        and day_of_month == "*"
        and month == "*"
        and day_of_week == "*"
    ):
        return "Every 1m"

    if (
        minute.startswith("*/")
        and hour == "*"
        and day_of_month == "*"
        and month == "*"
        and day_of_week == "*"
    ):
        interval = minute.removeprefix("*/")
        if interval.isdigit():
            return f"Every {int(interval)}m"

    if (
        minute.isdigit()
        and hour == "*"
        and day_of_month == "*"
        and month == "*"
        and day_of_week == "*"
    ):
        minute_value = int(minute)
        if minute_value == 0:
            return "Every 1h"
        return f"Every 1h :{minute_value:02}"

    if (
        minute.isdigit()
        and hour.isdigit()
        and day_of_month == "*"
        and month == "*"
        and day_of_week == "*"
    ):
        return f"Every 1d {int(hour):02}:{int(minute):02} UTC"

    day_name = _CRON_WEEKDAY_NAMES.get(day_of_week.lower())
    if (
        day_name
        and minute.isdigit()
        and hour.isdigit()
        and day_of_month == "*"
        and month == "*"
    ):
        return f"Every {day_name} at {int(hour):02}:{int(minute):02} UTC"

    return f"Cron {cron}"


def build_workflow_trigger_summary(
    *,
    online_schedule_count: int | None,
    schedule_cron: str | None,
    schedule_every: timedelta | None,
    webhook_active: bool | None,
    case_trigger_event_types: list[str] | None,
) -> WorkflowTriggerSummaryMinimal | None:
    """Build list-row trigger summary fields from denormalized query columns."""
    schedule_count = online_schedule_count or 0
    schedule_natural: str | None
    if schedule_cron:
        schedule_natural = humanize_cron_expression(schedule_cron)
    elif schedule_every is not None:
        schedule_natural = f"Every {_format_timedelta_natural(schedule_every)}"
    else:
        schedule_natural = None

    resolved_webhook_active = bool(webhook_active)
    case_trigger_events = tuple(case_trigger_event_types or [])

    if schedule_count <= 0 and not resolved_webhook_active and not case_trigger_events:
        return None

    return WorkflowTriggerSummaryMinimal(
        schedule_count_online=schedule_count,
        schedule_cron=schedule_cron,
        schedule_natural=schedule_natural,
        webhook_active=resolved_webhook_active,
        case_trigger_events=case_trigger_events,
    )
