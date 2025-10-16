"""Tests for helper functions in tracecat.tables.common."""

from datetime import datetime, timezone

import pytest

from tracecat.tables.common import ensure_tzaware_datetime, to_sql_clause
from tracecat.tables.enums import SqlType


class TestToSqlClauseTimestamptz:
    """Tests for TIMESTAMPTZ handling in to_sql_clause."""

    def test_naive_datetime_assumes_utc(self) -> None:
        naive_datetime = datetime(2024, 1, 2, 3, 4, 5)

        bind_param = to_sql_clause(naive_datetime, "event_time", SqlType.TIMESTAMPTZ)

        assert bind_param.value.tzinfo is timezone.utc

    def test_iso_string_with_z_suffix(self) -> None:
        iso_value = "2024-01-02T03:04:05Z"

        bind_param = to_sql_clause(iso_value, "event_time", SqlType.TIMESTAMPTZ)

        assert bind_param.value.tzinfo is timezone.utc

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            to_sql_clause(123, "event_time", SqlType.TIMESTAMPTZ)


class TestEnsureTzawareDatetime:
    """Tests for ensure_tzaware_datetime utility."""

    def test_assumes_utc_for_naive_datetime(self) -> None:
        naive_datetime = datetime(2024, 1, 2, 3, 4, 5)

        result = ensure_tzaware_datetime(naive_datetime)

        assert result.tzinfo is timezone.utc

    def test_parses_iso_string_with_offset(self) -> None:
        iso_value = "2024-01-02T03:04:05+02:00"

        result = ensure_tzaware_datetime(iso_value)

        assert result.tzinfo is not None

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            ensure_tzaware_datetime("not-a-date")
