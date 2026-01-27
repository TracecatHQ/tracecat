import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import RegistryOAuthSecret, SecretNotFoundError

from tracecat.auth.types import Role
from tracecat.db.models import RegistryVersion
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.exceptions import ExecutionError, LoopExecutionError
from tracecat.executor.backends.direct import DirectBackend
from tracecat.executor.schemas import ActionImplementation, ExecutorActionErrorInfo
from tracecat.executor.service import (
    dispatch_action,
    flatten_wrapped_exc_error_group,
)
from tracecat.expressions.expectations import ExpectedField
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.registry.actions.bound import BoundRegistryAction
from tracecat.registry.actions.schemas import (
    ActionStep,
    RegistryActionCreate,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.repository import Repository
from tracecat.registry.versions.schemas import RegistryVersionManifestAction
from tracecat.registry.versions.service import RegistryVersionsService
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService

TEST_VERSION = "test-version"


async def create_manifest_for_actions(
    session: AsyncSession,
    repo_id: UUID,
    actions: list[BoundRegistryAction],
    organization_id: UUID | None,
) -> RegistryLock:
    """Create a RegistryVersion with manifest for the given actions.

    Returns a RegistryLock that can be used in RunActionInput.
    """
    assert organization_id is not None, "organization_id must be provided"

    from sqlalchemy import select

    from tracecat.db.models import RegistryRepository

    # Query the repository to get the origin
    result = await session.execute(
        select(RegistryRepository).where(RegistryRepository.id == repo_id)
    )
    repo = result.scalar_one()
    origin = repo.origin

    # Build manifest actions dict
    manifest_actions = {}
    action_bindings = {}

    for bound_action in actions:
        action_create = RegistryActionCreate.from_bound(bound_action, repo_id)
        action_name = f"{action_create.namespace}.{action_create.name}"
        manifest_action = RegistryVersionManifestAction.from_action_create(
            action_create
        )
        manifest_actions[action_name] = manifest_action.model_dump(mode="json")
        action_bindings[action_name] = origin

    # Add core.transform.reshape which is often used in tests
    core_reshape_impl = {
        "type": "udf",
        "url": origin,  # Required field
        "module": "tracecat_registry.core.transform",
        "name": "reshape",
    }
    manifest_actions["core.transform.reshape"] = {
        "namespace": "core.transform",
        "name": "reshape",
        "action_type": "udf",
        "description": "Transform data",
        "interface": {"expects": {}, "returns": None},
        "implementation": core_reshape_impl,
    }
    action_bindings["core.transform.reshape"] = origin

    manifest = {
        "schema_version": "1.0",
        "actions": manifest_actions,
    }

    # Create RegistryVersion
    rv = RegistryVersion(
        organization_id=organization_id,
        repository_id=repo_id,
        version=TEST_VERSION,
        manifest=manifest,
        tarball_uri="s3://test/test.tar.gz",
    )
    session.add(rv)
    await session.commit()

    # Populate index from manifest
    versions_svc = RegistryVersionsService(session)
    await versions_svc.populate_index_from_manifest(rv, commit=True)

    return RegistryLock(
        origins={origin: TEST_VERSION},
        actions=action_bindings,
    )


def make_registry_lock(action: str, origin: str = "tracecat_registry") -> RegistryLock:
    """Helper to create a RegistryLock for a single action.

    Note: This is for unit tests with mocked resolution. For integration tests,
    use create_manifest_for_actions() to create proper database entries.
    """
    return RegistryLock(
        origins={origin: TEST_VERSION},
        actions={action: origin},
    )


async def run_action_test(input: RunActionInput, role: Role) -> Any:
    """Test helper: execute action using production code path.

    Uses dispatch_action to ensure proper service-layer orchestration
    for both UDF and template actions.
    """
    from tracecat.contexts import ctx_role

    ctx_role.set(role)
    backend = DirectBackend()
    return await dispatch_action(backend, input)


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
        logical_time=datetime.now(UTC),
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
        path = base_path.joinpath("../../data/actions/udfs.py").resolve()
        logger.info("PATH", path=path)
        tmp_path.joinpath("udfs.py").symlink_to(path)
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
    with pytest.raises(SecretNotFoundError):
        repo.get("testing.fetch_secret").fn("TEST_UDF_SECRET_KEY")

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

        # Create manifest for the test actions
        registry_lock = await create_manifest_for_actions(
            session,
            db_repo_id,
            [repo.get("testing.fetch_secret")],
            test_role.organization_id,
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
            registry_lock=registry_lock,
        )

        # Act
        result = await run_action_test(input, test_role)

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

    # Sanity check: Raises SecretNotFoundError because we haven't set secrets
    with pytest.raises(SecretNotFoundError):
        repo.get("testing.fetch_secret").fn("TEST_TEMPLATE_SECRET_KEY")

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

        # Create manifest for the test actions (both template and UDF)
        registry_lock = await create_manifest_for_actions(
            session,
            db_repo_id,
            [repo.get("testing.template_action"), repo.get("testing.fetch_secret")],
            test_role.organization_id,
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
            registry_lock=registry_lock,
        )

        # Act
        result = await run_action_test(input, test_role)

        # Assert
        assert result == "__SECRET_VALUE__"
    finally:
        secret = await sec_service.get_secret_by_name("test")
        await sec_service.delete_secret(secret)


@pytest.mark.integration
@pytest.mark.anyio
async def test_executor_can_run_template_action_with_oauth(
    test_role, db_session_with_repo, mock_run_context
):
    """Test that Template Action steps correctly pull in OAuth secrets.

    This test validates that:
    1. OAuth integrations are properly loaded
    2. OAUTH.* expressions resolve correctly
    3. OAuth tokens are mirrored to SECRETS.* for backward compatibility
    4. Template actions can access both namespaces
    """

    session, db_repo_id = db_session_with_repo
    # Test OAuth token value
    oauth_token_value = "__TEST_OAUTH_TOKEN_VALUE__"

    # 1. Create OAuth integration
    svc = IntegrationService(session, role=test_role)
    await svc.store_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        ),
        access_token=SecretStr(oauth_token_value),
        refresh_token=None,
        expires_in=3600,
    )

    # 3. Create a test template action that uses both OAuth and legacy secrets
    # This tests that OAuth tokens are properly resolved and available
    test_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test OAuth Action",
            description="Test that OAuth tokens are resolved correctly",
            name="oauth_test",
            namespace="testing.oauth",
            display_group="Testing",
            expects={
                "message": ExpectedField(
                    type="str",
                    description="A test message",
                )
            },
            secrets=[
                RegistryOAuthSecret(
                    provider_id="microsoft_teams",
                    grant_type="authorization_code",
                )
            ],
            steps=[
                ActionStep(
                    ref="verify_tokens",
                    action="core.transform.reshape",
                    args={
                        "value": {
                            "oauth_token": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_USER_TOKEN }}",
                        }
                    },
                )
            ],
            returns="${{ steps.verify_tokens.result }}",
        ),
    )

    # 4. Register the test template action in the repository
    # NOTE: We use the Repository class to register template actions in memory
    # This allows us to test template execution without database registration
    repo = Repository()
    repo.register_template_action(test_action)

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(
            repo.get("testing.oauth.oauth_test"), db_repo_id
        )
    )

    # Create manifest for the test actions
    registry_lock = await create_manifest_for_actions(
        session,
        db_repo_id,
        [repo.get("testing.oauth.oauth_test")],
        test_role.organization_id,
    )

    # 5. Create and run the action
    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.oauth.oauth_test",
            run_if=None,
            for_each=None,
            args={"message": "test message"},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    # Act
    result = await run_action_test(input, test_role)

    # Assert - the template returns the result from the reshape step
    # which contains oauth_token
    assert isinstance(result, dict), f"Expected dict result, got {type(result)}"
    assert "oauth_token" in result, (
        f"Expected 'oauth_token' in result, got {result.keys()}"
    )

    # Verify the values
    assert result["oauth_token"] == oauth_token_value, (
        f"OAuth token from SECRETS namespace mismatch. "
        f"Expected {oauth_token_value}, got {result['oauth_token']}"
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_executor_can_run_udf_with_oauth(
    mock_package, test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that the executor can run a UDF with OAuth secrets through Ray.

    This test validates that:
    1. OAuth integrations are properly loaded for UDFs
    2. OAuth tokens are accessible via the SECRETS namespace in UDFs
    3. UDFs can directly access OAuth tokens through the secrets manager
    """

    session, db_repo_id = db_session_with_repo

    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    # Test OAuth token value
    oauth_token_value = "__TEST_UDF_OAUTH_TOKEN_VALUE__"

    # 1. Create OAuth integration
    svc = IntegrationService(session, role=test_role)
    await svc.store_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        ),
        access_token=SecretStr(oauth_token_value),
        refresh_token=None,
        expires_in=3600,
    )

    # 2. Register UDFs including the OAuth one
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Sanity check: Verify the OAuth UDF is registered
    assert "testing.fetch_oauth_token" in repo

    # 3. Create registry action for the OAuth UDF
    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(
            repo.get("testing.fetch_oauth_token"), db_repo_id
        )
    )

    # Create manifest for the test actions
    registry_lock = await create_manifest_for_actions(
        session,
        db_repo_id,
        [repo.get("testing.fetch_oauth_token")],
        test_role.organization_id,
    )

    # 4. Create and run the action
    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.fetch_oauth_token",
            run_if=None,
            for_each=None,
            args={},
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    # Act
    result = await run_action_test(input, test_role)

    # Assert - the UDF returns the OAuth token value
    assert result == oauth_token_value, (
        f"OAuth token from UDF mismatch. Expected {oauth_token_value}, got {result}"
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_executor_can_run_udf_with_oauth_in_secret_expression(
    test_role, db_session_with_repo, mock_run_context, monkeysession
):
    """Test that the executor can run a UDF with OAuth secrets in a secret expression."""

    session, db_repo_id = db_session_with_repo

    from tracecat import config

    monkeysession.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    # Test OAuth token value
    oauth_token_value = "__TEST_UDF_OAUTH_TOKEN_VALUE__"

    # 1. Create OAuth integration
    svc = IntegrationService(session, role=test_role)
    await svc.store_integration(
        provider_key=ProviderKey(
            id="microsoft_teams",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        ),
        access_token=SecretStr(oauth_token_value),
        refresh_token=None,
        expires_in=3600,
    )

    # Create manifest for core actions
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [], test_role.organization_id
    )

    # 4. Create and run the action
    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="core.transform.reshape",
            args={
                "value": "${{ SECRETS.microsoft_teams_oauth.MICROSOFT_TEAMS_USER_TOKEN }}",
            },
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    # Act
    result = await run_action_test(input, test_role)

    # Assert - the UDF returns the OAuth token value
    assert result == oauth_token_value, (
        f"OAuth token from UDF mismatch. Expected {oauth_token_value}, got {result}"
    )


async def mock_action(input: Any, role: Any = None):
    """Mock action that simulates some async work"""
    del role  # unused
    await asyncio.sleep(0.1)
    return input


@pytest.mark.integration
@pytest.mark.anyio
async def test_direct_backend_execute(
    test_role: Role, mock_run_context: RunContext, monkeypatch: pytest.MonkeyPatch
):
    """Test that the direct backend properly handles async operations."""
    from tracecat.executor.backends.direct import DirectBackend
    from tracecat.executor.schemas import ExecutorResultSuccess, ResolvedContext

    # Mock _execute_with_context to return a simple result
    async def mock_execute_with_context(self, input, role, resolved_context):
        return {"input": input.task.args}

    monkeypatch.setattr(
        DirectBackend, "_execute_with_context", mock_execute_with_context
    )

    backend = DirectBackend()
    resolved_context = ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(type="udf", module="test", name="mock"),
        evaluated_args={},
        workspace_id="test-workspace",
        workflow_id="test-workflow",
        run_id="test-run",
        executor_token="",
    )

    # Run the backend execute
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
            registry_lock=make_registry_lock("test.mock_action"),
        )
        result = await backend.execute(input, test_role, resolved_context)
        assert isinstance(result, ExecutorResultSuccess)
        assert result.result == {"input": {"value": i}}


async def mock_error(*args, **kwargs):
    """Mock _execute_with_context to raise an error"""
    raise ValueError("__EXPECTED_MESSAGE__")


@pytest.mark.integration
@pytest.mark.anyio
async def test_direct_backend_returns_wrapped_error(
    test_role: Role, mock_run_context: RunContext, monkeypatch: pytest.MonkeyPatch
):
    """Test that the direct backend properly handles wrapped errors."""
    from tracecat.executor.backends.direct import DirectBackend
    from tracecat.executor.schemas import ExecutorResultFailure, ResolvedContext

    # Create a test input with an action that will raise an error
    monkeypatch.setattr(DirectBackend, "_execute_with_context", mock_error)

    backend = DirectBackend()
    resolved_context = ResolvedContext(
        secrets={},
        variables={},
        action_impl=ActionImplementation(type="udf", module="test", name="error"),
        evaluated_args={},
        workspace_id="test-workspace",
        workflow_id="test-workflow",
        run_id="test-run",
        executor_token="",
    )

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
        registry_lock=make_registry_lock("test.error_action"),
    )

    # Run the backend execute and verify it returns a failure result
    result = await backend.execute(input, test_role, resolved_context)
    assert isinstance(result, ExecutorResultFailure)
    error_info = result.error
    assert error_info.type == "ValueError"
    assert error_info.message == "__EXPECTED_MESSAGE__"
    assert error_info.action_name == "test.error_action"
    assert error_info.filename == __file__
    assert error_info.function == "mock_error"


@pytest.mark.anyio
async def test_dispatcher(
    mock_package,
    test_role,
    mock_run_context,
    db_session_with_repo,
):
    """Try to replicate `Error in loop` error, where usually we fail validation inside the executor loop.

    We will execute everything in the current thread.
    1. Add mock package with a function that will raise an error
    """
    from tracecat.contexts import ctx_role
    from tracecat.executor.backends.direct import DirectBackend

    # Set up the role context for dispatch_action
    ctx_role.set(test_role)

    session, db_repo_id = db_session_with_repo
    repo = Repository()
    repo._register_udfs_from_package(mock_package)

    # Sanity check: We've registered the UDFs correctly
    assert repo.get("testing.add_100").fn(1) == 101  # type: ignore
    assert repo.get("testing.add_nums").fn([1, 2, 3, 4, 5]) == 15  # type: ignore

    ra_service = RegistryActionsService(session, role=test_role)
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.add_100"), db_repo_id)
    )
    await ra_service.create_action(
        RegistryActionCreate.from_bound(repo.get("testing.add_nums"), db_repo_id)
    )

    # Create manifest for the test actions
    registry_lock = await create_manifest_for_actions(
        session,
        db_repo_id,
        [repo.get("testing.add_100"), repo.get("testing.add_nums")],
        test_role.organization_id,
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
        registry_lock=registry_lock,
    )

    # Act
    backend = DirectBackend()
    result = await dispatch_action(backend, input)

    # This should run correctly
    assert result == [101, 102, 103, 104, 105]

    # Now, force a validation error
    # This fails because the loop variable is None, but it expects an int

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
        registry_lock=registry_lock,
    )

    # Act
    with pytest.raises(LoopExecutionError) as e:
        result = await dispatch_action(backend, input)
    assert len(e.value.loop_errors) == 1
    assert e.value.loop_errors[0].info.loop_iteration == 2
    assert e.value.loop_errors[0].info.loop_vars == {"x": None}

    # Try another dispatch with a different error

    input = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="testing.add_nums",
            run_if=None,
            args={"nums": "${{ FN.flatten(var.x) }}"},
            for_each="${{ for var.x in [[1], None, [3], [4], [5]] }}",
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
        registry_lock=registry_lock,
    )

    # Act
    with pytest.raises(LoopExecutionError) as e:
        result = await dispatch_action(backend, input)
    assert len(e.value.loop_errors) == 1
    assert e.value.loop_errors[0].info.loop_iteration == 1
    assert e.value.loop_errors[0].info.loop_vars == {"x": None}


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
