import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tracecat.contexts import RunContext, ctx_role
from tracecat.dsl.common import create_default_dsl_context
from tracecat.dsl.models import ActionStatement, RunActionInput
from tracecat.executor.service import run_action_from_input, sync_executor_entrypoint
from tracecat.expressions.expectations import ExpectedField
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


@pytest.mark.anyio
async def test_executor_can_run_udf_with_secrets(
    mock_package, test_role, db_session_with_repo, mock_run_context
):
    """Test that the executor can run a UDF with secrets through Ray."""
    session, db_repo_id = db_session_with_repo

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
            exec_context=create_default_dsl_context(),
            run_context=mock_run_context,
        )

        # Act
        ctx_role.set(test_role)
        result = await run_action_from_input(input)

        # Assert
        assert result == "__SECRET_VALUE_UDF__"
    finally:
        secret = await sec_service.get_secret_by_name("test", raise_on_error=True)
        await sec_service.delete_secret_by_id(secret.id)


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
            exec_context=create_default_dsl_context(),
            run_context=mock_run_context,
        )

        # Act
        result = await run_action_from_input(input)

        # Assert
        assert result == "__SECRET_VALUE__"
    finally:
        secret = await sec_service.get_secret_by_name("test", raise_on_error=True)
        await sec_service.delete_secret_by_id(secret.id)


async def mock_action(input: Any):
    """Mock action that simulates some async work"""
    await asyncio.sleep(0.1)
    return input


def test_sync_executor_entrypoint(test_role, mock_run_context):
    """Test that the sync executor entrypoint properly handles async operations."""

    # Create a test input

    # Mock the run_action_from_input function
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("tracecat.executor.service.run_action_from_input", mock_action)

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
            result = sync_executor_entrypoint(input, test_role)
            assert result == input
