import os
import sys
import textwrap
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, SecretStr, TypeAdapter
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import RegistrySecret

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import (
    ActionStatement,
    RunActionInput,
    RunContext,
)
from tracecat.exceptions import (
    ExecutionError,
    RegistryValidationError,
    TracecatValidationError,
)
from tracecat.executor import service
from tracecat.executor.backends.test import TestBackend
from tracecat.expressions.expectations import ExpectedField
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
from tracecat.variables.schemas import VariableCreate
from tracecat.variables.service import VariablesService

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


async def run_action_test(input: RunActionInput, role: Role) -> Any:
    """Test helper: execute action using production code path."""
    from tracecat.contexts import ctx_role

    ctx_role.set(role)
    backend = TestBackend()
    return await service.dispatch_action(backend, input)


@pytest.fixture
def mock_package(tmp_path: Path) -> Iterator[ModuleType]:
    """Pytest fixture that creates a mock package with files and cleans up after the test."""

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
        with open(os.path.join(tmp_path, "has_secret.py"), "w") as f:
            f.write(
                textwrap.dedent(
                    """
                from tracecat_registry import registry, RegistrySecret, secrets

                secret = RegistrySecret(
                    name="the_secret",
                    keys=["THE_SECRET_KEY"],
                )

                @registry.register(
                    description="This is a deprecated function",
                    namespace="testing",
                    secrets=[secret],
                )
                def has_secret() -> str:
                    return secrets.get("THE_SECRET_KEY")
            """
                )
            )

        yield test_module

    finally:
        # Clean up
        del sys.modules["test_module"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_fetches_nested_secrets(
    test_role: Role,
    monkeypatch: MonkeyPatch,
    db_session_with_repo: tuple[AsyncSession, UUID],
    mock_package: ModuleType,
) -> None:
    """Test template action with secrets.

    The test verifies:
    1. Template action with secrets executes successfully
    """

    monkeypatch.setattr(config, "TRACECAT__UNSAFE_DISABLE_SM_MASKING", True)

    # Arrange
    # 1. Register test udfs
    repo = Repository()

    session, db_repo_id = db_session_with_repo
    repo.init(include_base=True, include_templates=False)
    repo._register_udfs_from_package(mock_package)
    assert repo.get("testing.has_secret") is not None

    template_action_registered = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Action Registered",
            description="Test template registered in the registry",
            name="template_action_registered",
            namespace="testing",
            display_group="Testing",
            expects={
                "num": ExpectedField(
                    type="int",
                    description="Number to add 100 to",
                )
            },
            secrets=[
                RegistrySecret(
                    name="template_secret_registered",
                    keys=["TEMPLATE_SECRET_KEY_REGISTERED"],
                )  # This secret isn't used but we just pull it to verify it's fetched
            ],
            steps=[
                ActionStep(
                    ref="base",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ inputs.num + 100 }}",
                    },
                ),
                ActionStep(
                    ref="secret",
                    action="testing.has_secret",
                    args={},
                ),
            ],
            # Return the secret value from the secret step
            returns="${{ steps.secret.result }}",
        ),
    )
    repo.register_template_action(template_action_registered)

    # It then returns the fetched secret
    template_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Action",
            description="This is just a test",
            name="template_action",
            namespace="testing",
            display_group="Testing",
            expects={
                "num": ExpectedField(
                    type="int",
                    description="Number to add 100 to",
                )
            },
            secrets=[
                RegistrySecret(
                    name="template_secret",
                    keys=["TEMPLATE_SECRET_KEY"],
                )
            ],
            steps=[
                ActionStep(
                    ref="base",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ inputs.num + 100 }}",
                    },
                ),
                ActionStep(
                    ref="secret",
                    action="testing.has_secret",
                    args={},
                ),
                ActionStep(
                    ref="template_secret_registered",
                    action="testing.template_action_registered",
                    args={
                        "num": "${{ inputs.num }}",
                    },
                ),
            ],
            returns={
                "secret_step": "${{ steps.secret.result }}",
                "nested_secret_step": "${{ steps.template_secret_registered.result }}",
            },
        ),
    )

    # We expect the secret to be fetched
    def get_secrets(action: BoundRegistryAction) -> list[RegistrySecret]:
        """Recursively fetch secrets from the template action."""
        secrets = []
        # Base case
        if action.type == "udf":
            if action.secrets:
                secrets.extend(action.secrets)
        elif action.type == "template":
            assert action.template_action is not None
            if template_secrets := action.template_action.definition.secrets:
                secrets.extend(template_secrets)
            for step in action.template_action.definition.steps:
                step_action = repo.get(step.action)
                step_secrets = get_secrets(step_action)
                secrets.extend(step_secrets)
        return secrets

    bound_action = BoundRegistryAction(
        fn=lambda: None,
        type="template",
        name="template_action",
        namespace="testing",
        description="This is just a test",
        secrets=[],
        args_docs={},
        rtype_cls=Any,
        rtype_adapter=TypeAdapter(Any),
        default_title="Test Action",
        display_group="Testing",
        doc_url=None,
        author=None,
        deprecated=None,
        include_in_schema=True,
        template_action=template_action,
        origin="testing.template_action",
        args_cls=BaseModel,
    )
    assert set(get_secrets(bound_action)) == {
        RegistrySecret(
            name="template_secret",
            keys=["TEMPLATE_SECRET_KEY"],
        ),
        RegistrySecret(
            name="the_secret",
            keys=["THE_SECRET_KEY"],
        ),
        RegistrySecret(
            name="template_secret_registered",
            keys=["TEMPLATE_SECRET_KEY_REGISTERED"],
        ),
    }

    # Now run the action

    repo.register_template_action(template_action)

    assert "testing.template_action" in repo

    # Create RegistryAction records in the database for all testing.* actions
    ra_service = RegistryActionsService(session, role=test_role)
    actions_to_register = [
        repo.get("testing.template_action"),
        repo.get("testing.template_action_registered"),
        repo.get("testing.has_secret"),
    ]
    for bound_action in actions_to_register:
        await ra_service.create_action(
            RegistryActionCreate.from_bound(bound_action, db_repo_id)
        )

    # Create manifest for all actions (template actions and their step actions)
    # The create_manifest_for_actions helper will properly set up the database
    # with manifest entries and return a RegistryLock that maps actions to origins
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, actions_to_register, test_role.organization_id
    )

    # Add secrets to the db
    sec_service = SecretsService(session, role=test_role)
    # Add secret for the UDF
    await sec_service.create_secret(
        SecretCreate(
            name="the_secret",
            environment="default",
            keys=[
                SecretKeyValue(
                    key="THE_SECRET_KEY", value=SecretStr("UDF_SECRET_VALUE")
                )
            ],
        )
    )
    # Add secret for the registered template action
    await sec_service.create_secret(
        SecretCreate(
            name="template_secret_registered",
            environment="default",
            keys=[
                SecretKeyValue(
                    key="TEMPLATE_SECRET_KEY_REGISTERED",
                    value=SecretStr("REGISTERED_SECRET_VALUE"),
                )
            ],
        )
    )
    # Add secret for the main template action
    await sec_service.create_secret(
        SecretCreate(
            name="template_secret",
            environment="default",
            keys=[
                SecretKeyValue(
                    key="TEMPLATE_SECRET_KEY",
                    value=SecretStr("TEMPLATE_SECRET_VALUE"),
                )
            ],
        )
    )

    input = RunActionInput(
        task=ActionStatement(
            ref="test_action",
            action="testing.template_action",
            args={"num": 123123},
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_with_secrets"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result = await run_action_test(input=input, role=test_role)
    assert result == {
        "secret_step": "UDF_SECRET_VALUE",
        "nested_secret_step": "UDF_SECRET_VALUE",
    }


def test_template_action_definition_validates_self_reference():
    """Test that TemplateActionDefinition validates against self-referential steps.

    The test verifies:
    1. A template action cannot reference itself in its steps
    2. The validation error message is descriptive
    """
    with pytest.raises(TracecatValidationError) as exc_info:
        TemplateActionDefinition(
            title="Self Referential Action",
            description="This action tries to reference itself",
            name="self_ref",
            namespace="testing",
            display_group="Testing",
            expects={},
            steps=[
                ActionStep(
                    ref="self_ref_step",
                    action="testing.self_ref",  # This references the template itself
                    args={},
                ),
            ],
            returns="${{ steps.self_ref_step.result }}",
        )

    assert "Steps cannot reference the template action itself: testing.self_ref" in str(
        exc_info.value
    )
    assert "1 steps reference the template action" in str(exc_info.value)


def test_template_action_parses_from_dict():
    data = {
        "type": "action",
        "definition": {
            "title": "Test Action",
            "description": "This is just a test",
            "name": "wrapper",
            "namespace": "integrations.test",
            "display_group": "Testing",
            "secrets": [{"name": "test_secret", "keys": ["KEY"]}],
            "expects": {
                "service_source": {
                    "type": "str",
                    "description": "The service source",
                    "default": "elastic",
                },
                "limit": {"type": "int | None", "description": "The limit"},
            },
            "steps": [
                {
                    "ref": "base",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "service_source": "${{ inputs.service_source }}",
                            "data": 100,
                        }
                    },
                },
                {
                    "ref": "final",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": [
                            "${{ steps.base.result.data + 100 }}",
                            "${{ steps.base.result.service_source }}",
                        ]
                    },
                },
            ],
            "returns": "${{ steps.final.result }}",
        },
    }

    # Parse and validate the action
    action = TemplateAction.model_validate(data)

    # Check the action definition
    assert action.definition.title == "Test Action"
    assert action.definition.description == "This is just a test"
    assert action.definition.action == "integrations.test.wrapper"
    assert action.definition.namespace == "integrations.test"
    assert action.definition.display_group == "Testing"
    assert action.definition.secrets == [
        RegistrySecret(name="test_secret", keys=["KEY"])
    ]
    assert action.definition.expects == {
        "service_source": ExpectedField(
            type="str",
            description="The service source",
            default="elastic",
        ),
        "limit": ExpectedField(
            type="int | None",
            description="The limit",
        ),
    }
    assert action.definition.steps == [
        ActionStep(
            ref="base",
            action="core.transform.reshape",
            args={
                "value": {
                    "service_source": "${{ inputs.service_source }}",
                    "data": 100,
                }
            },
        ),
        ActionStep(
            ref="final",
            action="core.transform.reshape",
            args={
                "value": [
                    "${{ steps.base.result.data + 100 }}",
                    "${{ steps.base.result.service_source }}",
                ]
            },
        ),
    ]


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_args,expected,should_raise",
    [
        (
            {"user_id": "john@tracecat.com", "service_source": "custom", "limit": 99},
            ["john@tracecat.com", "custom", 99],
            False,
        ),
        (
            {"user_id": "john@tracecat.com"},
            ["john@tracecat.com", "elastic", 100],
            False,
        ),
        (
            {},
            None,
            True,
        ),
    ],
    ids=["valid", "with_defaults", "missing_required"],
)
async def test_template_action_runs(
    test_args: dict[str, Any],
    expected: Any,
    should_raise: bool,
    test_role: Role,
    db_session_with_repo: tuple[AsyncSession, UUID],
) -> None:
    session, db_repo_id = db_session_with_repo

    action = TemplateAction(
        **{
            "type": "action",
            "definition": {
                "title": "Test Action",
                "description": "This is just a test",
                "name": "wrapper",
                "namespace": "integrations.test",
                "display_group": "Testing",
                "doc_url": "https://example.com/docs",
                "author": "Tracecat",
                "expects": {
                    # Required field
                    "user_id": {
                        "type": "str",
                        "description": "The user ID",
                    },
                    # Optional field with string default
                    "service_source": {
                        "type": "str",
                        "description": "The service source",
                        "default": "elastic",
                    },
                    # Optional field with None as default
                    "limit": {
                        "type": "int | None",
                        "description": "The limit",
                        "default": None,
                    },
                },
                "steps": [
                    {
                        "ref": "base",
                        "action": "core.transform.reshape",
                        "args": {
                            "value": {
                                "service_source": "${{ inputs.service_source }}",
                                "data": "${{ inputs.limit || 100 }}",
                            }
                        },
                    },
                    {
                        "ref": "final",
                        "action": "core.transform.reshape",
                        "args": {
                            "value": [
                                "${{ inputs.user_id }}",
                                "${{ steps.base.result.service_source }}",
                                "${{ steps.base.result.data }}",
                            ]
                        },
                    },
                ],
                "returns": "${{ steps.final.result }}",
            },
        }
    )

    # Register the action in-memory
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(action)

    # Check that the action is registered
    assert action.definition.action == "integrations.test.wrapper"
    assert "core.transform.reshape" in repo
    assert action.definition.action in repo

    # Get the registered action
    bound_action = repo.get(action.definition.action)

    # Create manifest for the test action (enables production code path)
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [bound_action], test_role.organization_id
    )

    # Run the action using production code path
    input = RunActionInput(
        task=ActionStatement(
            ref="test_template_action",
            action="integrations.test.wrapper",
            args=test_args,
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_runs"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )

    if should_raise:
        # Production path wraps RegistryValidationError in ExecutionError
        with pytest.raises(ExecutionError) as exc_info:
            await run_action_test(input=input, role=test_role)
        assert isinstance(exc_info.value.__cause__, RegistryValidationError)
    else:
        result = await run_action_test(input=input, role=test_role)
        assert result == expected


@pytest.mark.integration
@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_args,expected,should_raise",
    [
        (
            {"status": "critical", "message": "System CPU usage above 90%"},
            {"alert_status": "critical", "alert_message": "System CPU usage above 90%"},
            False,
        ),
        (
            {"message": "Informational message"},
            {"alert_status": "info", "alert_message": "Informational message"},
            False,
        ),
        (
            {
                "status": "emergency",
                "message": "This should fail",
            },
            None,
            True,
        ),
    ],
    ids=["valid_status", "default_status", "invalid_status"],
)
async def test_template_action_with_enums(
    test_args: dict[str, Any],
    expected: Any,
    should_raise: bool,
    test_role: Role,
    db_session_with_repo: tuple[AsyncSession, UUID],
) -> None:
    """Test template action with enums.
    This test verifies that:
    1. The action can be constructed with an enum status
    2. The action can be run with a valid enum status
    3. The action can be run with a default enum status
    4. Invalid enum values are properly rejected
    """
    session, db_repo_id = db_session_with_repo

    data = {
        "type": "action",
        "definition": {
            "title": "Test Alert Action",
            "description": "Test action with enum status",
            "name": "alert",
            "namespace": "integrations.test",
            "display_group": "Testing",
            "doc_url": "https://example.com/docs",
            "author": "Tracecat",
            "expects": {
                "status": {
                    "type": 'enum["critical", "warning", "info"]',
                    "description": "Alert severity level",
                    "default": "info",
                },
                "message": {"type": "str", "description": "Alert message"},
            },
            "steps": [
                {
                    "ref": "format",
                    "action": "core.transform.reshape",
                    "args": {
                        "value": {
                            "alert_status": "${{ inputs.status }}",
                            "alert_message": "${{ inputs.message }}",
                        }
                    },
                }
            ],
            "returns": "${{ steps.format.result }}",
        },
    }

    # Parse and validate the action
    action = TemplateAction.model_validate(data)

    # Register the action in-memory
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(action)

    # Get the registered action
    bound_action = repo.get(action.definition.action)

    # Create manifest for the test action (enables production code path)
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [bound_action], test_role.organization_id
    )

    # Run the action using production code path
    input = RunActionInput(
        task=ActionStatement(
            ref="test_template_action_enums",
            action="integrations.test.alert",
            args=test_args,
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_with_enums"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )

    if should_raise:
        # Production path wraps RegistryValidationError in ExecutionError
        with pytest.raises(ExecutionError) as exc_info:
            await run_action_test(input=input, role=test_role)
        assert isinstance(exc_info.value.__cause__, RegistryValidationError)
    else:
        result = await run_action_test(input=input, role=test_role)
        assert result == expected


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_with_vars_expressions(
    test_role: Role,
    db_session_with_repo: tuple[AsyncSession, UUID],
) -> None:
    """Test template action with VARS expressions.

    The test verifies:
    1. Template action can reference workspace variables using VARS expressions
    2. VARS expressions support nested path access (e.g., VARS.config.base_url)
    3. VARS expressions work with fallback logic (e.g., inputs.value || VARS.default)
    4. VARS expressions are properly evaluated during template action execution
    """
    session, db_repo_id = db_session_with_repo

    # Create workspace variables
    var_service = VariablesService(session, role=test_role)
    await var_service.create_variable(
        VariableCreate(
            name="test",
            description="Test configuration",
            values={"url": "https://api.example.com", "timeout": 30},
            environment="default",
        )
    )
    await var_service.create_variable(
        VariableCreate(
            name="api_config",
            description="API configuration",
            values={"base_url": "https://example.com", "version": "v1"},
            environment="default",
        )
    )

    # Create a template action that uses VARS expressions
    template_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test VARS Expression",
            description="Test action that uses VARS expressions",
            name="test_vars",
            namespace="testing",
            display_group="Testing",
            expects={
                "url": ExpectedField(
                    type="str | None",
                    description="The URL to test",
                    default=None,
                ),
                "custom_timeout": ExpectedField(
                    type="int | None",
                    description="Custom timeout",
                    default=None,
                ),
            },
            steps=[
                # Test VARS with fallback using || operator
                ActionStep(
                    ref="url_with_fallback",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ inputs.url || VARS.test.url }}",
                    },
                ),
                # Test direct VARS access
                ActionStep(
                    ref="base_url",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ VARS.api_config.base_url }}",
                    },
                ),
                # Test VARS in complex expression
                ActionStep(
                    ref="full_url",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ VARS.api_config.base_url + '/api/' + VARS.api_config.version }}",
                    },
                ),
                # Test VARS with timeout fallback
                ActionStep(
                    ref="timeout",
                    action="core.transform.reshape",
                    args={
                        "value": "${{ inputs.custom_timeout || VARS.test.timeout }}",
                    },
                ),
            ],
            returns={
                "url": "${{ steps.url_with_fallback.result }}",
                "base_url": "${{ steps.base_url.result }}",
                "full_url": "${{ steps.full_url.result }}",
                "timeout": "${{ steps.timeout.result }}",
            },
        ),
    )

    # Register the action
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(template_action)

    # Register the action in the database and create manifest
    ra_service = RegistryActionsService(session, role=test_role)
    bound_action = repo.get(template_action.definition.action)
    action_create_params = RegistryActionCreate.from_bound(bound_action, db_repo_id)
    await ra_service.create_action(action_create_params)

    # Create manifest for the test actions
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [bound_action], test_role.organization_id
    )

    # Test case 1: Without inputs, should use VARS defaults
    input1 = RunActionInput(
        task=ActionStatement(
            ref="test_vars_no_inputs",
            action="testing.test_vars",
            args={},
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_vars_no_inputs"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result1 = await run_action_test(input=input1, role=test_role)
    assert result1 == {
        "url": "https://api.example.com",  # From VARS.test.url
        "base_url": "https://example.com",  # From VARS.api_config.base_url
        "full_url": "https://example.com/api/v1",  # Concatenated VARS
        "timeout": 30,  # From VARS.test.timeout
    }

    # Test case 2: With inputs, should override VARS defaults
    input2 = RunActionInput(
        task=ActionStatement(
            ref="test_vars_with_inputs",
            action="testing.test_vars",
            args={"url": "https://custom.example.com", "custom_timeout": 60},
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_vars_with_inputs"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result2 = await run_action_test(input=input2, role=test_role)
    assert result2 == {
        "url": "https://custom.example.com",  # From inputs.url (overrides VARS)
        "base_url": "https://example.com",  # From VARS.api_config.base_url
        "full_url": "https://example.com/api/v1",  # Concatenated VARS
        "timeout": 60,  # From inputs.custom_timeout (overrides VARS)
    }

    # Test case 3: Partial inputs, should use VARS for missing inputs
    input3 = RunActionInput(
        task=ActionStatement(
            ref="test_vars_partial_inputs",
            action="testing.test_vars",
            args={"url": "https://another.example.com"},
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_vars_partial"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result3 = await run_action_test(input=input3, role=test_role)
    assert result3 == {
        "url": "https://another.example.com",  # From inputs.url (overrides VARS)
        "base_url": "https://example.com",  # From VARS.api_config.base_url
        "full_url": "https://example.com/api/v1",  # Concatenated VARS
        "timeout": 30,  # From VARS.test.timeout (inputs.custom_timeout not provided)
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_with_multi_level_fallback_chain(
    test_role: Role,
    db_session_with_repo: tuple[AsyncSession, UUID],
) -> None:
    """Test template action with multi-level fallback chain using || operator.

    The test verifies that the fallback chain correctly evaluates in order:
    ${{ inputs.url || VARS.config.url || "http://default-url.com" }}

    Test cases:
    1. inputs.url provided -> uses inputs.url
    2. inputs.url not provided, VARS.config.url available -> uses VARS.config.url
    3. Neither inputs.url nor VARS.config.url available -> uses literal default
    """
    session, db_repo_id = db_session_with_repo

    # Create workspace variable for fallback
    var_service = VariablesService(session, role=test_role)
    await var_service.create_variable(
        VariableCreate(
            name="config",
            description="Configuration with URL",
            values={"url": "http://vars-url.com"},
            environment="default",
        )
    )

    # Create a template action with multi-level fallback
    template_action = TemplateAction(
        type="action",
        definition=TemplateActionDefinition(
            title="Test Multi-level Fallback",
            description="Test action with multi-level fallback chain",
            name="test_fallback_chain",
            namespace="testing",
            display_group="Testing",
            expects={
                "url": ExpectedField(
                    type="str | None",
                    description="The URL to use",
                    default=None,
                ),
            },
            steps=[
                ActionStep(
                    ref="url_with_fallback_chain",
                    action="core.transform.reshape",
                    args={
                        "value": '${{ inputs.url || VARS.config.url || "http://default-url.com" }}',
                    },
                ),
            ],
            returns="${{ steps.url_with_fallback_chain.result }}",
        ),
    )

    # Register the action
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(template_action)

    # Register the action in the database and create manifest
    ra_service = RegistryActionsService(session, role=test_role)
    bound_action = repo.get(template_action.definition.action)
    action_create_params = RegistryActionCreate.from_bound(bound_action, db_repo_id)
    await ra_service.create_action(action_create_params)

    # Create manifest for the test actions
    registry_lock = await create_manifest_for_actions(
        session, db_repo_id, [bound_action], test_role.organization_id
    )

    # Test case 1: inputs.url provided -> should use inputs.url (first in chain)
    input1 = RunActionInput(
        task=ActionStatement(
            ref="test_fallback_1",
            action="testing.test_fallback_chain",
            args={"url": "http://input-url.com"},
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_fallback_chain_input"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result1 = await run_action_test(input=input1, role=test_role)
    assert result1 == "http://input-url.com", (
        "Should use inputs.url when provided (first in fallback chain)"
    )

    # Test case 2: inputs.url not provided, VARS.config.url available -> should use VARS.config.url (second in chain)
    input2 = RunActionInput(
        task=ActionStatement(
            ref="test_fallback_2",
            action="testing.test_fallback_chain",
            args={},  # No input provided
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_fallback_chain_vars"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result2 = await run_action_test(input=input2, role=test_role)
    assert result2 == "http://vars-url.com", (
        "Should use VARS.config.url when inputs.url not provided (second in fallback chain)"
    )

    # Test case 3: Neither inputs.url nor VARS.config.url available -> should use literal default (third in chain)
    # Delete the variable to test the final fallback
    variables = await var_service.list_variables(environment="default")
    config_var = next(var for var in variables if var.name == "config")
    await var_service.delete_variable(config_var)

    input3 = RunActionInput(
        task=ActionStatement(
            ref="test_fallback_3",
            action="testing.test_fallback_chain",
            args={},  # No input provided
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_fallback_chain_default"),
            wf_run_id=uuid.uuid4(),
            environment="default",
            logical_time=datetime.now(UTC),
        ),
        registry_lock=registry_lock,
    )
    result3 = await run_action_test(input=input3, role=test_role)
    assert result3 == "http://default-url.com", (
        "Should use literal default when neither inputs.url nor VARS.config.url available (third in fallback chain)"
    )


def test_aggregate_secrets_from_manifest_collects_nested_secrets():
    """Test that aggregate_secrets_from_manifest correctly collects secrets from nested template steps.

    This tests the API response path (not execution) to ensure the UI displays all required secrets.
    """
    from tracecat_registry import RegistrySecret

    from tracecat.registry.actions.service import RegistryActionsService
    from tracecat.registry.versions.schemas import (
        RegistryVersionManifest,
        RegistryVersionManifestAction,
    )

    # Create a manifest with:
    # 1. A UDF action with its own secret
    # 2. A template action that calls the UDF (should aggregate the UDF's secret)
    # 3. A nested template that calls another template (should aggregate all secrets)

    udf_secret = RegistrySecret(name="udf_secret", keys=["UDF_KEY"])
    template_secret = RegistrySecret(name="template_secret", keys=["TEMPLATE_KEY"])
    nested_template_secret = RegistrySecret(
        name="nested_template_secret", keys=["NESTED_KEY"]
    )

    manifest = RegistryVersionManifest(
        schema_version="1.0",
        actions={
            # UDF action with a secret
            "testing.udf_with_secret": RegistryVersionManifestAction(
                namespace="testing",
                name="udf_with_secret",
                action_type="udf",
                description="UDF with secret",
                secrets=[udf_secret],
                interface={"expects": {}, "returns": None},
                implementation={
                    "type": "udf",
                    "url": "http://test",
                    "module": "test",
                    "name": "test",
                },
            ),
            # Template action that uses the UDF
            "testing.template_with_udf": RegistryVersionManifestAction(
                namespace="testing",
                name="template_with_udf",
                action_type="template",
                description="Template that uses UDF",
                secrets=[template_secret],
                interface={"expects": {}, "returns": None},
                implementation={
                    "type": "template",
                    "template_action": {
                        "type": "action",
                        "definition": {
                            "name": "template_with_udf",
                            "namespace": "testing",
                            "title": "Template with UDF",
                            "display_group": "Testing",
                            "expects": {},
                            "steps": [
                                {
                                    "ref": "step1",
                                    "action": "testing.udf_with_secret",
                                    "args": {},
                                }
                            ],
                            "returns": "${{ steps.step1.result }}",
                        },
                    },
                },
            ),
            # Nested template that uses another template
            "testing.nested_template": RegistryVersionManifestAction(
                namespace="testing",
                name="nested_template",
                action_type="template",
                description="Nested template",
                secrets=[nested_template_secret],
                interface={"expects": {}, "returns": None},
                implementation={
                    "type": "template",
                    "template_action": {
                        "type": "action",
                        "definition": {
                            "name": "nested_template",
                            "namespace": "testing",
                            "title": "Nested Template",
                            "display_group": "Testing",
                            "expects": {},
                            "steps": [
                                {
                                    "ref": "step1",
                                    "action": "testing.template_with_udf",
                                    "args": {},
                                }
                            ],
                            "returns": "${{ steps.step1.result }}",
                        },
                    },
                },
            ),
        },
    )

    # Test 1: UDF should only return its own secret
    udf_secrets = RegistryActionsService.aggregate_secrets_from_manifest(
        manifest, "testing.udf_with_secret"
    )
    assert len(udf_secrets) == 1
    assert udf_secrets[0].name == "udf_secret"

    # Test 2: Template should return its own secret + UDF's secret
    template_secrets = RegistryActionsService.aggregate_secrets_from_manifest(
        manifest, "testing.template_with_udf"
    )
    secret_names = {s.name for s in template_secrets}
    assert secret_names == {"template_secret", "udf_secret"}

    # Test 3: Nested template should return all secrets (nested + template + UDF)
    nested_secrets = RegistryActionsService.aggregate_secrets_from_manifest(
        manifest, "testing.nested_template"
    )
    nested_secret_names = {s.name for s in nested_secrets}
    assert nested_secret_names == {
        "nested_template_secret",
        "template_secret",
        "udf_secret",
    }

    # Test 4: Non-existent action should return empty list
    missing_secrets = RegistryActionsService.aggregate_secrets_from_manifest(
        manifest, "testing.nonexistent"
    )
    assert missing_secrets == []
