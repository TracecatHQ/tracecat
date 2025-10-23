from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any


def ensure_aware_datetime(value: Any) -> datetime:
    """Coerce supported datetime-like inputs to timezone-aware UTC datetimes."""
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
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=UTC)
    else:
        raise TypeError(
            "Unsupported value for datetime conversion: " f"{type(value).__name__}"
        )

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)

