"""Tests for server-side cron validation on schedules."""

import pytest
from pydantic import ValidationError

from tracecat.workflow.schedules import models


def test_schedule_create_valid_cron():
    """Test that valid cron expressions are accepted."""
    # Valid cron expressions
    valid_crons = [
        "0 0 * * *",  # Daily at midnight
        "*/5 * * * *",  # Every 5 minutes
        "0 9 * * 1-5",  # Weekdays at 9 AM
        "0 0 1 * *",  # First day of every month
    ]

    for cron in valid_crons:
        instance = models.ScheduleCreate(workflow_id="wf_test123", cron=cron)
        assert instance.cron == cron


def test_schedule_create_invalid_cron():
    """Test that invalid cron expressions are rejected."""
    invalid_crons = [
        "invalid",  # Not a cron expression
        "* * * *",  # Missing field
        "60 * * * *",  # Invalid minute (0-59)
        "* 25 * * *",  # Invalid hour (0-23)
        "* * 32 * *",  # Invalid day (1-31)
        "* * * 13 *",  # Invalid month (1-12)
    ]

    for cron in invalid_crons:
        with pytest.raises(ValidationError) as exc:
            models.ScheduleCreate(workflow_id="wf_test123", cron=cron)
        assert "Invalid cron expression" in str(exc.value)


def test_schedule_update_valid_cron():
    """Test that valid cron expressions are accepted in updates."""
    instance = models.ScheduleUpdate(cron="0 0 * * *")
    assert instance.cron == "0 0 * * *"


def test_schedule_update_invalid_cron():
    """Test that invalid cron expressions are rejected in updates."""
    with pytest.raises(ValidationError) as exc:
        models.ScheduleUpdate(cron="invalid")
    assert "Invalid cron expression" in str(exc.value)
