from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tracecat.dsl.workflow_time import adjusted_workflow_time, wf_time_offset


@pytest.fixture
def mock_workflow_info():
    """Mock workflow.info() for testing."""
    return MagicMock(workflow_type="DSLWorkflow")


@pytest.fixture
def mock_non_dsl_workflow_info():
    """Mock workflow.info() for a non-DSL workflow."""
    return MagicMock(workflow_type="OtherWorkflow")


def test_adjusted_workflow_time_datetime_no_workflow():
    """Test decorator with datetime when not in a workflow."""
    test_time = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

    @adjusted_workflow_time
    def get_time() -> datetime:
        return test_time

    # Should return original time when not in workflow
    with patch("temporalio.workflow.info", side_effect=Exception):
        assert get_time() == test_time


def test_adjusted_workflow_time_date_no_workflow():
    """Test decorator with date when not in a workflow."""
    test_date = date(2024, 1, 1)

    @adjusted_workflow_time
    def get_date() -> date:
        return test_date

    # Should return original date when not in workflow
    with patch("temporalio.workflow.info", side_effect=Exception):
        assert get_date() == test_date


def test_adjusted_workflow_time_datetime_in_workflow(mock_workflow_info):
    """Test decorator with datetime inside a DSL workflow."""
    test_time = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    offset = timedelta(hours=2)

    @adjusted_workflow_time
    def get_time() -> datetime:
        return test_time

    # Mock being in a workflow
    with patch("temporalio.workflow.info", return_value=mock_workflow_info):
        # Set the workflow time offset
        wf_time_offset.set(offset)
        # Should return adjusted time
        assert get_time() == test_time - offset


def test_adjusted_workflow_time_date_in_workflow(mock_workflow_info):
    """Test decorator with date inside a DSL workflow."""
    test_date = date(2024, 1, 1)
    offset = timedelta(days=2)

    @adjusted_workflow_time
    def get_date() -> date:
        return test_date

    # Mock being in a workflow
    with patch("temporalio.workflow.info", return_value=mock_workflow_info):
        # Set the workflow time offset
        wf_time_offset.set(offset)
        # Should return adjusted date
        assert get_date() == test_date - timedelta(days=offset.days)


def test_adjusted_workflow_time_non_dsl_workflow(mock_non_dsl_workflow_info):
    """Test decorator in a non-DSL workflow."""
    test_time = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    offset = timedelta(hours=2)

    @adjusted_workflow_time
    def get_time() -> datetime:
        return test_time

    # Mock being in a non-DSL workflow
    with patch("temporalio.workflow.info", return_value=mock_non_dsl_workflow_info):
        # Set the workflow time offset
        wf_time_offset.set(offset)
        # Should return original time (no adjustment)
        assert get_time() == test_time


def test_adjusted_workflow_time_no_offset(mock_workflow_info):
    """Test decorator when no offset is set."""
    test_time = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)

    @adjusted_workflow_time
    def get_time() -> datetime:
        return test_time

    # Mock being in a workflow
    with patch("temporalio.workflow.info", return_value=mock_workflow_info):
        # Don't set any offset
        # Should return original time
        assert get_time() == test_time


def test_now_function_adjustment(mock_workflow_info):
    """Test that the now() function is properly adjusted."""
    from tracecat.expressions.functions import now

    offset = timedelta(hours=2)

    # Mock being in a workflow
    with patch("temporalio.workflow.info", return_value=mock_workflow_info):
        # Set the workflow time offset
        wf_time_offset.set(offset)

        # Get current time and adjusted time
        current_time = datetime.now()
        adjusted_time = now()

        # The adjusted time should be approximately offset hours behind
        # We use timedelta(seconds=1) for tolerance due to execution time
        assert abs((current_time - offset) - adjusted_time) < timedelta(seconds=1)


def test_today_function_adjustment(mock_workflow_info):
    """Test that the today() function is properly adjusted."""
    from tracecat.expressions.functions import today

    offset = timedelta(days=2)

    # Mock being in a workflow
    with patch("temporalio.workflow.info", return_value=mock_workflow_info):
        # Set the workflow time offset
        wf_time_offset.set(offset)

        # Get current date and adjusted date
        current_date = date.today()
        adjusted_date = today()

        # The adjusted date should be offset days behind
        assert adjusted_date == current_date - timedelta(days=offset.days)
