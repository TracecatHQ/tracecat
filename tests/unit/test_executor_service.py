import uuid
from unittest.mock import AsyncMock, patch

import pytest
from tracecat_registry import RegistrySecret

from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.executor.models import DispatchActionContext
from tracecat.executor.service import (
    _dispatch_action,
    dispatch_action_on_cluster,
    run_action_from_input,
)
from tracecat.expressions.common import ExprContext
from tracecat.git import GitUrl
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.types.auth import Role


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def basic_task_input():
    """Fixture that provides a basic RunActionInput without looping."""
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = wf_id.short() + "/exec_test"
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
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = wf_id.short() + "/exec_test"
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
        git_url=GitUrl(host="github.com", org="org", repo="repo", ref="abc123"),
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
        patch("tracecat.executor.service.get_ssh_command") as mock_ssh_cmd,
    ):
        mock_git_url.return_value = GitUrl(
            host="github.com", org="org", repo="repo", ref="abc123"
        )
        mock_ssh_cmd.return_value = "ssh -i /tmp/key"
        mock_dispatch.return_value = {"result": "success"}

        result = await dispatch_action_on_cluster(
            input=basic_task_input, session=mock_session
        )

        assert result == {"result": "success"}
        mock_git_url.assert_called_once()
        mock_ssh_cmd.assert_called_once()


@pytest.mark.anyio
async def test_run_action_from_input_secrets_handling(mocker, test_role):
    """Test that run_action_from_input correctly handles secrets as sets without converting to lists."""
    # Mock the registry action service
    mock_reg_service = mocker.AsyncMock(spec=RegistryActionsService)
    mock_reg_service.get_action.return_value = mocker.MagicMock()
    mock_reg_service.get_bound.return_value = mocker.MagicMock()

    # Create some registry secrets
    registry_secrets = [
        RegistrySecret(name="required_secret1", keys=["REQ_KEY1"], optional=False),
        RegistrySecret(name="required_secret2", keys=["REQ_KEY2"], optional=False),
        RegistrySecret(name="optional_secret1", keys=["OPT_KEY1"], optional=True),
        RegistrySecret(name="optional_secret2", keys=["OPT_KEY2"], optional=True),
    ]
    mock_reg_service.fetch_all_action_secrets.return_value = registry_secrets

    # Mock the with_session context manager
    mocker.patch(
        "tracecat.registry.actions.service.RegistryActionsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_reg_service)
        ),
    )

    # Mock the task args with templated secrets
    task_args = {
        "param1": "{{ secrets.args_secret1 }}",
        "param2": {"nested": "{{ secrets.args_secret2 }}"},
    }

    # Create a run action input
    wf_id = WorkflowUUID.new_uuid4()
    wf_exec_id = wf_id.short() + "/exec_test"
    wf_run_id = uuid.uuid4()

    task = ActionStatement(ref="test_task", action="test_action", args=task_args)
    input = RunActionInput(
        task=task,
        exec_context={},
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="test-env",
        ),
    )

    # Mock extract_templated_secrets to return some args secrets
    mocker.patch(
        "tracecat.executor.service.extract_templated_secrets",
        return_value=["args_secret1", "args_secret2"],
    )

    # Mock get_runtime_env
    mocker.patch("tracecat.executor.service.get_runtime_env", return_value="test_env")

    # Mock the AuthSandbox
    mock_sandbox = mocker.MagicMock()
    mock_sandbox.secrets = {}
    mock_sandbox.__aenter__.return_value = mock_sandbox
    mock_sandbox.__aexit__.return_value = None

    auth_sandbox_mock = mocker.patch("tracecat.executor.service.AuthSandbox")
    auth_sandbox_mock.return_value = mock_sandbox

    # Mock run_single_action to avoid actually running the action
    mocker.patch("tracecat.executor.service.run_single_action", return_value="result")

    # Mock evaluate_templated_args
    mocker.patch("tracecat.executor.service.evaluate_templated_args", return_value={})

    # Mock env_sandbox
    mocker.patch("tracecat.executor.service.env_sandbox")

    # Mock flatten_secrets
    mocker.patch("tracecat.executor.service.flatten_secrets", return_value={})

    # Run the function
    await run_action_from_input(input, test_role)

    # Check that AuthSandbox was called with sets, not lists
    auth_sandbox_mock.assert_called_once()
    call_args, call_kwargs = auth_sandbox_mock.call_args

    # Verify that secrets parameter is a set
    assert isinstance(call_kwargs["secrets"], set)
    expected_secrets = {
        "required_secret1",
        "required_secret2",
        "optional_secret1",
        "optional_secret2",
        "args_secret1",
        "args_secret2",
    }
    assert call_kwargs["secrets"] == expected_secrets

    # Verify that optional_secrets parameter is a set
    assert isinstance(call_kwargs["optional_secrets"], set)
    expected_optional_secrets = {"optional_secret1", "optional_secret2"}
    assert call_kwargs["optional_secrets"] == expected_optional_secrets

    # Verify environment parameter
    assert call_kwargs["environment"] == "test_env"
