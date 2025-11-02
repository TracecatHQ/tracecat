import os
import sys
import textwrap
import uuid
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr, TypeAdapter
from tracecat_registry import RegistrySecret

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.dsl.schemas import (
    ActionStatement,
    RunActionInput,
    RunContext,
)
from tracecat.exceptions import RegistryValidationError, TracecatValidationError
from tracecat.executor import service
from tracecat.executor.service import run_action_from_input
from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.actions.schemas import (
    ActionStep,
    BoundRegistryAction,
    RegistryActionCreate,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.repository import Repository
from tracecat.secrets.schemas import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.variables.schemas import VariableCreate
from tracecat.variables.service import VariablesService


@pytest.fixture
def mock_package(tmp_path):
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
    test_role,
    monkeypatch,
    db_session_with_repo,
    mock_package,
):
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

    ra_service = RegistryActionsService(session, role=test_role)
    # create actions for each step
    action_names = {step.action for step in template_action.definition.steps} | {
        "testing.template_action",
    }
    for action_name in action_names:
        if action_name.startswith("testing"):
            step_create_params = RegistryActionCreate.from_bound(
                repo.get(action_name), db_repo_id
            )
            await ra_service.create_action(step_create_params)
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
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_with_secrets"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result = await run_action_from_input(input=input, role=test_role)
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
async def test_template_action_runs(test_args, expected, should_raise):
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
                "secrets": [{"name": "test_secret", "keys": ["KEY"]}],
                "expects": {
                    # Required field
                    "user_id": {
                        "type": "str",
                        "description": "The user ID",
                    },
                    # Optional field with string defaultß
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

    # Register the action
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(action)

    # Check that the action is registered
    assert action.definition.action == "integrations.test.wrapper"
    assert "core.transform.reshape" in repo
    assert action.definition.action in repo

    # Get the registered action
    bound_action = repo.get(action.definition.action)

    # Run the action
    if should_raise:
        with pytest.raises(RegistryValidationError):
            await service.run_template_action(
                action=bound_action,
                args=test_args,
                context={},
            )
    else:
        result = await service.run_template_action(
            action=bound_action,
            args=test_args,
            context={},
        )
        assert result == expected


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
async def test_template_action_with_enums(test_args, expected, should_raise):
    """Test template action with enums.
    This test verifies that:
    1. The action can be constructed with an enum status
    2. The action can be run with a valid enum status
    3. The action can be run with a default enum status
    4. Invalid enum values are properly rejected
    """
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

    # Register the action
    repo = Repository()
    repo.init(include_base=True, include_templates=False)
    repo.register_template_action(action)

    # Get the registered action
    bound_action = repo.get(action.definition.action)

    # Run the action
    if should_raise:
        with pytest.raises(RegistryValidationError):
            await service.run_template_action(
                action=bound_action,
                args=test_args,
                context={},
            )
    else:
        result = await service.run_template_action(
            action=bound_action,
            args=test_args,
            context={},
        )
        assert result == expected


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_with_vars_expressions(
    test_role,
    db_session_with_repo,
):
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

    # Register the action in the database
    ra_service = RegistryActionsService(session, role=test_role)
    bound_action = repo.get(template_action.definition.action)
    action_create_params = RegistryActionCreate.from_bound(bound_action, db_repo_id)
    await ra_service.create_action(action_create_params)

    # Test case 1: Without inputs, should use VARS defaults
    input1 = RunActionInput(
        task=ActionStatement(
            ref="test_vars_no_inputs",
            action="testing.test_vars",
            args={},
        ),
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_vars_no_inputs"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result1 = await run_action_from_input(input=input1, role=test_role)
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
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_vars_with_inputs"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result2 = await run_action_from_input(input=input2, role=test_role)
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
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_template_action_vars_partial"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result3 = await run_action_from_input(input=input3, role=test_role)
    assert result3 == {
        "url": "https://another.example.com",  # From inputs.url (overrides VARS)
        "base_url": "https://example.com",  # From VARS.api_config.base_url
        "full_url": "https://example.com/api/v1",  # Concatenated VARS
        "timeout": 30,  # From VARS.test.timeout (inputs.custom_timeout not provided)
    }


@pytest.mark.integration
@pytest.mark.anyio
async def test_template_action_with_multi_level_fallback_chain(
    test_role,
    db_session_with_repo,
):
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

    # Register the action in the database
    ra_service = RegistryActionsService(session, role=test_role)
    bound_action = repo.get(template_action.definition.action)
    action_create_params = RegistryActionCreate.from_bound(bound_action, db_repo_id)
    await ra_service.create_action(action_create_params)

    # Test case 1: inputs.url provided -> should use inputs.url (first in chain)
    input1 = RunActionInput(
        task=ActionStatement(
            ref="test_fallback_1",
            action="testing.test_fallback_chain",
            args={"url": "http://input-url.com"},
        ),
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_fallback_chain_input"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result1 = await run_action_from_input(input=input1, role=test_role)
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
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_fallback_chain_vars"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result2 = await run_action_from_input(input=input2, role=test_role)
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
        exec_context={},
        run_context=RunContext(
            wf_id=TEST_WF_ID,
            wf_exec_id=generate_test_exec_id("test_fallback_chain_default"),
            wf_run_id=uuid.uuid4(),
            environment="default",
        ),
    )
    result3 = await run_action_from_input(input=input3, role=test_role)
    assert result3 == "http://default-url.com", (
        "Should use literal default when neither inputs.url nor VARS.config.url available (third in fallback chain)"
    )
