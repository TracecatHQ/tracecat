import uuid
from unittest.mock import AsyncMock, patch

import orjson
import pytest
from fastapi import HTTPException

from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.executor.constants import PAYLOAD_MAX_SIZE_BYTES
from tracecat.executor.router import run_action
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.types.auth import Role


@pytest.fixture
def mock_session():
    return AsyncMock(spec=AsyncDBSession)


@pytest.fixture
def mock_role():
    return Role(type="service", service_id="tracecat-executor")


@pytest.fixture
def basic_action_input():
    """Create a basic action input for testing."""
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = f"{wf_id.short()}/exec_test"

    return RunActionInput(
        task=ActionStatement(
            action="test_action",
            args={"key": "value"},
            ref="test_ref",
        ),
        exec_context={},
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=uuid.uuid4(),
            environment="test-env",
        ),
    )


@pytest.mark.anyio
async def test_run_action_success(mock_session, mock_role, basic_action_input):
    """Test that run_action successfully processes a normal payload."""

    # Create a small result
    small_result = {"result": "success"}

    # Mock the dispatch function
    with patch("tracecat.executor.router.dispatch_action_on_cluster") as mock_dispatch:
        mock_dispatch.return_value = small_result

        # Call the router endpoint
        result = await run_action(
            role=mock_role,
            session=mock_session,
            action_name="test_action",
            action_input=basic_action_input,
        )

        # Verify the result is returned correctly
        assert result == small_result
        mock_dispatch.assert_called_once_with(
            input=basic_action_input, session=mock_session
        )


@pytest.mark.anyio
async def test_run_action_payload_too_large(
    mock_session, mock_role, basic_action_input
):
    """Test that run_action raises appropriate exception when payload exceeds size limit."""

    # Create a payload that exceeds the size limit when serialized
    # Generate a large string to create a serialized result > PAYLOAD_MAX_SIZE_BYTES
    large_data = "x" * (PAYLOAD_MAX_SIZE_BYTES + 1000)
    large_result = {"result": large_data}

    # Ensure it actually exceeds the limit when serialized
    assert len(orjson.dumps(large_result)) > PAYLOAD_MAX_SIZE_BYTES

    # Mock the dispatch function to return the large result
    with patch("tracecat.executor.router.dispatch_action_on_cluster") as mock_dispatch:
        mock_dispatch.return_value = large_result

        # The router should raise an HTTPException with 413 status
        with pytest.raises(HTTPException) as exc_info:
            await run_action(
                role=mock_role,
                session=mock_session,
                action_name="test_action",
                action_input=basic_action_input,
            )

        # Verify the exception has the correct status code
        assert exc_info.value.status_code == 413
        # Verify the exception has the correct detail
        assert (
            f"exceeds the size limit of {PAYLOAD_MAX_SIZE_BYTES / 1000}KB"
            in exc_info.value.detail
        )


@pytest.mark.anyio
async def test_run_action_payload_exactly_at_limit(
    mock_session, mock_role, basic_action_input
):
    """Test behavior with payload size exactly at the limit."""

    # Create a result that's exactly at the limit when serialized
    # First create a template with some buffer
    result_template = {"result": ""}
    template_overhead = len(orjson.dumps(result_template))

    # Calculate how many characters we can add to hit the exact limit
    chars_to_add = PAYLOAD_MAX_SIZE_BYTES - template_overhead
    exact_sized_result = {"result": "x" * chars_to_add}

    # Confirm it's exactly at the limit
    serialized = orjson.dumps(exact_sized_result)
    assert len(serialized) == PAYLOAD_MAX_SIZE_BYTES

    # Mock the dispatch function
    with patch("tracecat.executor.router.dispatch_action_on_cluster") as mock_dispatch:
        mock_dispatch.return_value = exact_sized_result

        # This should not raise an exception
        result = await run_action(
            role=mock_role,
            session=mock_session,
            action_name="test_action",
            action_input=basic_action_input,
        )

        # Verify the result is returned correctly
        assert result == exact_sized_result


@pytest.mark.anyio
async def test_run_action_payload_size_just_over_limit(
    mock_session, mock_role, basic_action_input
):
    """Test with payload just 1 byte over the limit to ensure boundary conditions are handled."""

    # Create a result template
    result_template = {"result": ""}
    template_overhead = len(orjson.dumps(result_template))

    # Calculate how many characters we need to exceed the limit by 1 byte
    chars_to_add = PAYLOAD_MAX_SIZE_BYTES - template_overhead + 1
    slightly_oversized_result = {"result": "x" * chars_to_add}

    # Confirm it exceeds the limit by a small amount
    serialized = orjson.dumps(slightly_oversized_result)
    assert len(serialized) > PAYLOAD_MAX_SIZE_BYTES
    assert len(serialized) <= PAYLOAD_MAX_SIZE_BYTES + 10  # Should be just over

    # Mock the dispatch function
    with patch("tracecat.executor.router.dispatch_action_on_cluster") as mock_dispatch:
        mock_dispatch.return_value = slightly_oversized_result

        # Should raise HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await run_action(
                role=mock_role,
                session=mock_session,
                action_name="test_action",
                action_input=basic_action_input,
            )

        # Verify the exception has the correct status code
        assert exc_info.value.status_code == 413
