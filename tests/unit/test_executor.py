import asyncio
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tracecat.contexts import RunContext
from tracecat.db.engine import get_async_session_context_manager
from tracecat.dsl.common import create_default_dsl_context
from tracecat.dsl.models import ActionStatement, RunActionInput
from tracecat.expressions.expectations import ExpectedField
from tracecat.logger import logger
from tracecat.registry import executor
from tracecat.registry.actions.models import (
    ActionStep,
    RegistryActionCreate,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.executor import _init_worker_process, get_executor
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.repository import Repository
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService


@pytest.fixture
def mock_run_context():
    wf_id = "wf-" + "0" * 32
    exec_id = "exec-" + "0" * 32
    wf_exec_id = f"{wf_id}:{exec_id}"
    run_id = uuid.uuid4()
    return RunContext(
        wf_id=wf_id,
        wf_exec_id=wf_exec_id,
        wf_run_id=run_id,
        environment="default",
    )


@pytest.fixture
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


@pytest.fixture
async def db_session_with_repo(test_role):
    """Fixture that creates a db session and temporary repository."""

    async with get_async_session_context_manager() as session:
        rr_service = RegistryReposService(session, role=test_role)
        db_repo = await rr_service.create_repository(
            RegistryRepositoryCreate(origin="__test_repo__")
        )
        try:
            yield session, db_repo.id
        finally:
            try:
                await rr_service.delete_repository(db_repo)
                logger.info("Cleaned up db repo")
            except Exception as e:
                logger.error("Error cleaning up repo", e=e)


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
    assert repo.get("testing.fetch_secret").fn("KEY") is None  # type: ignore

    # 2. Add secrets
    sec_service = SecretsService(session, role=test_role)
    await sec_service.create_secret(
        SecretCreate(
            name="test",
            environment="default",
            keys=[SecretKeyValue(key="KEY", value=SecretStr("__SECRET_VALUE__"))],
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
        RegistryActionCreate.from_bound(repo.get("testing.template_action"), db_repo_id)
    )
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.fetch_secret"), db_repo_id)
    )

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.template_action",
            run_if=None,
            for_each=None,
            args={"secret_key_name": "KEY"},
        ),
        exec_context=create_default_dsl_context(),
        run_context=mock_run_context,
    )

    # Act
    result = await executor.run_action_from_input(input)

    # Assert
    assert result == "__SECRET_VALUE__"


@pytest.mark.anyio
async def test_executor_initialization():
    """Test that the executor is properly initialized with a process pool."""
    # Test singleton behavior
    executor1 = executor.get_executor()
    executor2 = executor.get_executor()

    assert executor1 is executor2
    assert isinstance(executor1, ProcessPoolExecutor)


def get_process_id():
    """Helper function to return process ID"""
    _init_worker_process()  # Initialize worker
    return os.getpid()


@pytest.mark.anyio
async def test_executor_process_isolation():
    """Test that each worker process gets its own event loop."""

    # Create multiple concurrent tasks to verify process isolation
    executor = get_executor()
    futures = [executor.submit(get_process_id) for _ in range(3)]

    # Get results and verify they're different process IDs
    process_ids = [future.result() for future in futures]
    assert all(pid != os.getpid() for pid in process_ids)  # Different from main process


async def mock_action(input: Any):
    """Mock action that simulates some async work"""
    await asyncio.sleep(0.1)
    return input


def test_sync_executor_entrypoint(test_role, mock_run_context):
    """Test that the sync executor entrypoint properly handles async operations."""
    _init_worker_process()

    # Create a test input

    # Mock the run_action_from_input function
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("tracecat.registry.executor.run_action_from_input", mock_action)

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
                exec_context=create_default_dsl_context(),
                run_context=mock_run_context,
            )
            result = executor.sync_executor_entrypoint(input, test_role)
            assert result == input


def slow_task(sleep_time: float) -> float:
    """Helper function that simulates a slow task"""
    _init_worker_process()
    time.sleep(sleep_time)
    return sleep_time


@pytest.mark.anyio
async def test_executor_concurrent_execution():
    """Test that the executor can handle multiple concurrent executions."""

    # Submit multiple tasks with different durations
    executor = get_executor()

    futures = [
        executor.submit(slow_task, 0.2),
        executor.submit(slow_task, 0.3),
        executor.submit(slow_task, 0.1),
    ]

    # Wait for all tasks to complete
    results = [future.result() for future in futures]

    # Verify results
    assert results == [0.2, 0.3, 0.1]


def _test_worker_task():
    """Task that creates and uses an async operation in the worker"""

    async def async_operation():
        await asyncio.sleep(0.1)
        return id(asyncio.get_running_loop())

    # Get the loop from the worker process
    loop = asyncio.get_event_loop()
    # Run the async operation
    return loop.run_until_complete(async_operation())


@pytest.mark.anyio
async def test_executor_prevents_loop_conflicts():
    """Test that the executor prevents 'Task attached to different loop' errors."""

    # Run multiple worker tasks
    with ProcessPoolExecutor(initializer=_init_worker_process) as executor:
        loop = asyncio.get_running_loop()
        futures = [loop.run_in_executor(executor, _test_worker_task) for _ in range(10)]
        ids = await asyncio.gather(*futures)

        # Get the main process loop ID
        main_loop_id = id(asyncio.get_running_loop())

        # Verify that:
        # 1. Each worker has a different loop from the main process
        assert all(loop_id != main_loop_id for loop_id in ids)
