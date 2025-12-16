"""Datetime utilities for registry actions.

Copied from tracecat/tables/common.py to allow registry actions
to run without importing tracecat.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def coerce_to_utc_datetime(value: str | int | float | datetime | date) -> datetime:
    """Convert supported inputs into a timezone-aware UTC datetime.

    Args:
        value: A datetime, date, ISO string, or Unix timestamp.

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        TypeError: If the value cannot be coerced to a datetime.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TypeError(f"Invalid ISO datetime string: {value!r}") from exc
    elif isinstance(value, int | float):
        dt = datetime.fromtimestamp(value, tz=UTC)
    else:
        raise TypeError(f"Unable to coerce {value!r} to UTC datetime")

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def coerce_optional_to_utc_datetime(
    value: str | int | float | datetime | date | None,
) -> datetime | None:
    """Coerce a value to a timezone-aware UTC datetime.

    Args:
        value: A datetime, date, ISO string, Unix timestamp, or None.

    Returns:
        A timezone-aware UTC datetime, or None if value is None.
    """
    if value is None:
        return None
    return coerce_to_utc_datetime(value)


def coerce_to_date(value: str | int | float | datetime | date) -> date:
    """Convert supported inputs into a date.

    Args:
        value: A date, datetime, ISO string, or Unix timestamp.

    Returns:
        A date object.

    Raises:
        TypeError: If the value cannot be coerced to a date.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).date()
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed_dt = datetime.fromisoformat(text)
            return parsed_dt.date()
        except ValueError:
            try:
                return date.fromisoformat(text)
            except ValueError as exc:
                raise TypeError(f"Invalid ISO date string: {value!r}") from exc
    raise TypeError(f"Unable to coerce {value!r} to date")


def coerce_optional_to_date(
    value: str | int | float | datetime | date | None,
) -> date | None:
    """Coerce a value to a date.

    Args:
        value: A date, datetime, ISO string, Unix timestamp, or None.

    Returns:
        A date object, or None if value is None.
    """
    if value is None:
        return None
    return coerce_to_date(value)
