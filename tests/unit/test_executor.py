import asyncio
import traceback
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.models import ActionStatement, RunActionInput, RunContext
from tracecat.executor.models import ExecutorActionErrorInfo
from tracecat.executor.service import (
    _dispatch_action,
    flatten_wrapped_exc_error_group,
    run_action_from_input,
    sync_executor_entrypoint,
)
from tracecat.expressions.common import ExprContext
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    ActionStep,
    RegistryActionCreate,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.repository import Repository
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import ExecutionError, LoopExecutionError


@pytest.fixture
def mock_run_context():
    wf_id = "wf-" + "0" * 32
    exec_id = "exec-" + "0" * 32
    wf_exec_id = f"{wf_id}:{exec_id}"
    run_id = uuid.uuid4()
    return RunContext(
        wf_id=WorkflowUUID.from_legacy(wf_id),
        wf_exec_id=wf_exec_id,
        wf_run_id=run_id,
        environment="default",
    )


@pytest.fixture(scope="function")
def mock_package(tmp_path):
    """Pytest fixture that creates a mock package with files and cleans up after the test."""
    import sys
    from importlib.machinery import ModuleSpec
    from types import ModuleType

    # Create a new module
    test_module = ModuleType("test_module")

    # Create a module spec for the test module
    module_spec = ModuleSpec("test_module", None)
    test_module.__spec__ = module_spec
    # Set __path__ to the temporary directory
    test_module.__path__ = [str(tmp_path)]

    try:
        # Add the module to sys.modules
        sys.modules["test_module"] = test_module
        # Create a file for the sync function
        base_path = Path(__file__)
        path = base_path.joinpath(
            "../../data/test_actions/test_executor_functions.py"
        ).resolve()
        logger.info("PATH", path=path)
        tmp_path.joinpath("test_executor_functions.py").symlink_to(path)
        yield test_module
    finally:
        # Clean up
        del sys.modules["test_module"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_executor_can_run_udf_with_secrets(
    mock_package, test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that the executor can run a UDF with secrets through Ray."""
    session, db_repo_id = db_session_with_repo

    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    # Arrange
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Sanity check: Returns None because we haven't set secrets
    assert repo.get("testing.fetch_secret").fn("TEST_UDF_SECRET_KEY") is None  # type: ignore

    sec_service = SecretsService(session, role=test_role)
    try:
        await sec_service.create_secret(
            SecretCreate(
                name="test",
                environment="default",
                keys=[
                    SecretKeyValue(
                        key="TEST_UDF_SECRET_KEY",
                        value=SecretStr("__SECRET_VALUE_UDF__"),
                    )
                ],
            )
        )

        ra_service = RegistryActionsService(session, role=test_role)
        await ra_service.create_action(
            RegistryActionCreate.from_bound(
                repo.get("testing.fetch_secret"), db_repo_id
            )
        )

        input = RunActionInput(
            task=ActionStatement(
                ref="test",
                action="testing.fetch_secret",
                run_if=None,
                for_each=None,
                args={"secret_key_name": "TEST_UDF_SECRET_KEY"},
            ),
            exec_context=create_default_execution_context(),
            run_context=mock_run_context,
        )

        # Act
        result = await run_action_from_input(input, test_role)

        # Assert
        assert result == "__SECRET_VALUE_UDF__"
    finally:
        secret = await sec_service.get_secret_by_name("test")
        await sec_service.delete_secret(secret)


@pytest.mark.integration
@pytest.mark.anyio
async def test_executor_can_run_template_action_with_secret(
    mock_package, test_role, db_session_with_repo, mock_run_context
):
    """Test that checks that Template Action steps correctly pull in their dependent secrets."""

    session, db_repo_id = db_session_with_repo
    # Arrange
    # 1. Register test udfs
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Sanity check: We've registered the UDFs correctly
    assert "testing.add_100" in repo
    assert repo.get("testing.add_100").fn(100) == 200  # type: ignore

    # Sanity check: Returns None because we haven't set secrets
    assert repo.get("testing.fetch_secret").fn("TEST_TEMPLATE_SECRET_KEY") is None  # type: ignore

    # 2. Add secrets
    sec_service = SecretsService(session, role=test_role)
    try:
        await sec_service.create_secret(
            SecretCreate(
                name="test",
                environment="default",
                keys=[
                    SecretKeyValue(
                        key="TEST_TEMPLATE_SECRET_KEY",
                        value=SecretStr("__SECRET_VALUE__"),
                    )
                ],
            )
        )

        # Here, 'testing.template_action' wraps 'testing.fetch_secret'.
        # It then returns the fetched secret
        action = TemplateAction(
            type="action",
            definition=TemplateActionDefinition(
                title="Test Action",
                description="This is just a test",
                name="template_action",
                namespace="testing",
                display_group="Testing",
                expects={
                    "secret_key_name": ExpectedField(
                        type="str",
                        description="Secret name to fetch",
                    )
                },
                secrets=[],  # NOTE: We have no secrets at the template level
                steps=[
                    ActionStep(
                        ref="base",
                        action="testing.fetch_secret",
                        args={
                            "secret_key_name": "${{ inputs.secret_key_name }}",
                        },
                    )
                ],
                returns="${{ steps.base.result }}",
            ),
        )

        repo.register_template_action(action)
        logger.info("REPO", store=repo.store.keys())

        ra_service = RegistryActionsService(session, role=test_role)
        await ra_service.create_action(
            RegistryActionCreate.from_bound(
                repo.get("testing.template_action"), db_repo_id
            )
        )
        await ra_service.create_action(
            RegistryActionCreate.from_bound(
                repo.get("testing.fetch_secret"), db_repo_id
            )
        )

        input = RunActionInput(
            task=ActionStatement(
                ref="test",
                action="testing.template_action",
                run_if=None,
                for_each=None,
                args={"secret_key_name": "TEST_TEMPLATE_SECRET_KEY"},
            ),
            exec_context=create_default_execution_context(),
            run_context=mock_run_context,
        )

        # Act
        result = await run_action_from_input(input, test_role)

        # Assert
        assert result == "__SECRET_VALUE__"
    finally:
        secret = await sec_service.get_secret_by_name("test")
        await sec_service.delete_secret(secret)


async def mock_action(input: Any, **kwargs):
    """Mock action that simulates some async work"""
    await asyncio.sleep(0.1)
    return input


@pytest.mark.integration
def test_sync_executor_entrypoint(
    test_role: Role, mock_run_context: RunContext, monkeypatch: pytest.MonkeyPatch
):
    """Test that the sync executor entrypoint properly handles async operations."""

    # Mock the run_action_from_input function
    monkeypatch.setattr("tracecat.executor.service.run_action_from_input", mock_action)

    # Run the entrypoint
    for i in range(10):
        input = RunActionInput(
            task=ActionStatement(
                ref="test",
                action="test.mock_action",
                args={"value": i},
                run_if=None,
                for_each=None,
            ),
            exec_context=create_default_execution_context(),
            run_context=mock_run_context,
        )
        result = sync_executor_entrypoint(input, test_role)
        assert result == input


async def mock_error(*args, **kwargs):
    """Mock run_action_from_input to raise an error"""
    raise ValueError("__EXPECTED_MESSAGE__")


@pytest.mark.integration
def test_sync_executor_entrypoint_returns_wrapped_error(
    test_role: Role, mock_run_context: RunContext, monkeypatch: pytest.MonkeyPatch
):
    """Test that the sync executor entrypoint properly handles wrapped errors."""
    # Create a test input with an action that will raise an error
    monkeypatch.setattr("tracecat.executor.service.run_action_from_input", mock_error)

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="test.error_action",
            args={},
            run_if=None,
            for_each=None,
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
    )

    # Run the entrypoint and verify it returns a RegistryActionErrorInfo
    result = sync_executor_entrypoint(input, test_role)
    assert isinstance(result, ExecutorActionErrorInfo)
    assert result.type == "ValueError"
    assert result.message == "__EXPECTED_MESSAGE__"
    assert result.action_name == "test.error_action"
    assert result.filename == __file__
    assert result.function == "mock_error"


@pytest.mark.skip(reason="Flaky test")
@pytest.mark.anyio
async def test_dispatcher(
    mock_package,
    test_role,
    mock_run_context,
    db_session_with_repo,
    monkeypatch: pytest.MonkeyPatch,
):
    """Try to replicate `Error in loop`ty error, where usually we fail validation inside the executor loop.

    We will execute everything in the current thread.
    1. Add mock package with a function that will raise an error
    """

    # Mock out run_action_on_ray_cluster
    async def mocked_executor_entrypoint(
        input: RunActionInput, role: Role, *args, **kwargs
    ):
        try:
            return await run_action_from_input(input=input, role=role)
        except Exception as e:
            # Raise the error proxy here
            logger.error(
                "Error running action, raising error proxy",
                error=e,
                type=type(e).__name__,
                traceback=traceback.format_exc(),
            )
            iteration = kwargs.get("iteration", None)
            exec_result = ExecutorActionErrorInfo.from_exc(e, input.task.action)
            if iteration is not None:
                exec_result.loop_iteration = iteration
                exec_result.loop_vars = input.exec_context[ExprContext.LOCAL_VARS]
            raise ExecutionError(info=exec_result) from None

    monkeypatch.setattr(
        "tracecat.executor.service.run_action_on_ray_cluster",
        mocked_executor_entrypoint,
    )
    session, db_repo_id = db_session_with_repo
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Sanity check: We've registered the UDFs correctly
    assert repo.get("testing.add_100").fn(1) == 101  # type: ignore

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.add_100"), db_repo_id)
    )

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.add_100",
            run_if=None,
            args={"num": "${{ var.x }}"},
            for_each="${{ for var.x in [1,2,3,4,5] }}",
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
    )

    # Act

    result = await _dispatch_action(input, test_role)

    # This should run correctly
    assert result == [101, 102, 103, 104, 105]

    # Now, force a validation error

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.add_100",
            run_if=None,
            args={"num": "${{ var.x }}"},
            for_each="${{ for var.x in [1,2,None,4,5] }}",
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
    )

    # Act
    with pytest.raises(LoopExecutionError):
        result = await _dispatch_action(input, test_role)

    # Try another dispatch with a different error

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.add_100",
            run_if=None,
            args={"num": "${{ FN.flatten(var.x) }}"},
            for_each="${{ for var.x in [[1], None, [3], [4], [5]] }}",
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
    )

    # Act
    with pytest.raises(LoopExecutionError):
        result = await _dispatch_action(input, test_role)


@pytest.fixture
def sample_execution_error() -> ExecutionError:
    """Create a sample ExecutionError for testing."""
    return ExecutionError(
        info=ExecutorActionErrorInfo(
            action_name="test_action",
            type="ValueError",
            message="Test error",
            filename=__file__,
            function="sample_execution_error",
        )
    )


def test_flatten_single_error(sample_execution_error: ExecutionError) -> None:
    """Test flattening a single ExecutionError."""
    result = flatten_wrapped_exc_error_group(sample_execution_error)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] == sample_execution_error


def test_flatten_exception_group(sample_execution_error: ExecutionError) -> None:
    """Test flattening an ExceptionGroup containing ExecutionErrors."""
    # Create an ExceptionGroup with multiple ExecutionErrors
    eg = ExceptionGroup(
        "test_group",
        [sample_execution_error, sample_execution_error, sample_execution_error],
    )

    result = flatten_wrapped_exc_error_group(eg)
    assert isinstance(result, list)
    assert len(result) == 3
    assert all(isinstance(err, ExecutionError) for err in result)


def test_flatten_nested_exception_groups(
    sample_execution_error: ExecutionError,
) -> None:
    """Test flattening nested ExceptionGroups containing ExecutionErrors."""
    # Create nested ExceptionGroups
    inner_group = ExceptionGroup(
        "inner_group", [sample_execution_error, sample_execution_error]
    )
    outer_group = ExceptionGroup("outer_group", [sample_execution_error, inner_group])

    result = flatten_wrapped_exc_error_group(outer_group)  # type: ignore
    assert isinstance(result, list)
    assert len(result) == 3  # 1 from outer + 2 from inner
    assert all(isinstance(err, ExecutionError) for err in result)
