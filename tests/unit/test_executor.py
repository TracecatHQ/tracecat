import uuid
from pathlib import Path

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
from tracecat.registry.repositories.models import RegistryRepositoryCreate
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.repository import Repository
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService


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
    mock_package, test_role, db_session_with_repo
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

    # bound_action = repo.get(action.definition.action)
    wf_id = "wf-" + "0" * 32
    exec_id = "exec-" + "0" * 32
    wf_exec_id = f"{wf_id}:{exec_id}"
    wf_run_id = uuid.uuid4()
    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.template_action",
            run_if=None,
            for_each=None,
            args={"secret_key_name": "KEY"},
        ),
        exec_context=create_default_dsl_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            wf_run_id=wf_run_id,
            environment="default",
        ),
    )

    # Act
    result = await executor.run_action_from_input(input)

    # Assert
    assert result == "__SECRET_VALUE__"
