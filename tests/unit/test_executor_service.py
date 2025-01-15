import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.executor.models import DispatchActionContext
from tracecat.executor.service import _dispatch_action, dispatch_action_on_cluster
from tracecat.expressions.common import ExprContext
from tracecat.git import GitUrl
from tracecat.types.auth import Role


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def basic_task_input():
    """Fixture that provides a basic RunActionInput without looping."""
    wf_id = "wf-" + uuid.uuid4().hex
    wf_exec_id = wf_id + ":exec-test"
    wf_run_id = uuid.uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="test_action",
            args={"key": "value"},
            ref="test_ref",
        ),
        exec_context={
            ExprContext.ACTIONS: {
                "test_action": {
                    "args": {"key": "value"},
                    "ref": "test-ref",
                }
            }
        },
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="test-env",
        ),
    )


@pytest.fixture
def basic_looped_task_input():
    wf_id = "wf-" + uuid.uuid4().hex
    wf_exec_id = wf_id + ":exec-test"
    wf_run_id = uuid.uuid4()
    return RunActionInput(
        task=ActionStatement(
            action="test_action",
            args={"key": "value"},
            ref="test_ref",
            for_each="${{ for var.x in [1,2,3] }}",
        ),
        exec_context={
            ExprContext.ACTIONS: {
                "test_action": {
                    "args": {"key": "value"},
                    "ref": "test-ref",
                }
            }
        },
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="test-env",
        ),
    )


@pytest.fixture
def dispatch_context():
    return DispatchActionContext(
        role=Role(type="service", service_id="tracecat-executor"),
        ssh_command="ssh -i /tmp/key",
        git_url=GitUrl(host="github.com", org="org", repo="repo", sha="abc123"),
    )


@pytest.mark.anyio
async def test_dispatch_action_basic(mock_session, basic_task_input, dispatch_context):
    with patch("tracecat.executor.service.run_action_on_ray_cluster") as mock_ray:
        mock_ray.return_value = {"result": "success"}

        result = await _dispatch_action(input=basic_task_input, ctx=dispatch_context)

        assert result == {"result": "success"}
        mock_ray.assert_called_once_with(basic_task_input, dispatch_context)


@pytest.mark.anyio
async def test_dispatch_action_with_foreach(
    mock_session, basic_looped_task_input, dispatch_context
):
    with patch("tracecat.executor.service.run_action_on_ray_cluster") as mock_ray:
        mock_ray.return_value = {"result": "success"}

        result = await _dispatch_action(
            input=basic_looped_task_input, ctx=dispatch_context
        )

        assert result == [{"result": "success"}] * 3

        # Assert the number of calls
        assert mock_ray.call_count == 3

        # Get all calls and their arguments
        calls = mock_ray.call_args_list

        # Verify each call's arguments
        for i, call in enumerate(calls, 1):
            args, kwargs = call
            input_arg = args[0]
            # Verify the loop variable 'x' was set to different values (1, 2, 3)
            assert input_arg.task.args["key"] == "value"
            assert input_arg.exec_context[ExprContext.LOCAL_VARS] == {"x": i}
            assert args[1] == dispatch_context


@pytest.mark.anyio
async def test_dispatch_action_with_git_url(mock_session, basic_task_input):
    with (
        patch("tracecat.executor.service.prepare_git_url") as mock_git_url,
        patch("tracecat.executor.service._dispatch_action") as mock_dispatch,
        patch("tracecat.executor.service.opt_temp_key_file") as mock_key_file,
    ):
        mock_git_url.return_value = GitUrl(
            host="github.com", org="org", repo="repo", sha="abc123"
        )
        mock_key_file.return_value.__aenter__.return_value = "ssh -i /tmp/key"
        mock_dispatch.return_value = {"result": "success"}

        result = await dispatch_action_on_cluster(
            input=basic_task_input, session=mock_session
        )

        assert result == {"result": "success"}
        mock_git_url.assert_called_once()
        mock_key_file.return_value.__aenter__.assert_called_once()
