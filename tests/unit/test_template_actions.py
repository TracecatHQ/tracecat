import os
import sys
import textwrap
import uuid
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr, TypeAdapter

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.dsl.models import (
    ActionStatement,
    RunActionInput,
    RunContext,
)
from tracecat.executor import service
from tracecat.executor.service import run_action_from_input
from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.actions.models import (
    ActionStep,
    BoundRegistryAction,
    RegistryActionCreate,
    RegistrySecret,
    TemplateAction,
    TemplateActionDefinition,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.repository import Repository
from tracecat.secrets.models import SecretCreate, SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.types.exceptions import RegistryValidationError, TracecatValidationError


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
    "test_args,expected_result,should_raise",
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
    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)

    # Check that the action is registered
    assert action.definition.action == "integrations.test.wrapper"
    assert "core.transform.reshape" in registry
    assert action.definition.action in registry

    # Get the registered action
    bound_action = registry.get(action.definition.action)

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
    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)

    # Get the registered action
    bound_action = registry.get(action.definition.action)

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
