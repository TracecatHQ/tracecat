import pytest
from pydantic import ValidationError
from tracecat_registry import RegistrySecret

from tracecat.executor import service
from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.actions.models import ActionStep, TemplateAction
from tracecat.registry.repository import Repository


def test_construct_template_action():
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
async def test_template_action_run():
    action = TemplateAction(
        **{
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
                    "limit": {
                        "type": "int | None",
                        "description": "The limit",
                    },
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
    result = await service.run_template_action(
        action=bound_action,
        args={"service_source": "elastic"},
        context={},
    )

    # Check the result
    assert result == [200, "elastic"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "test_args,expected_result,should_raise",
    [
        # Valid cases
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
        # Invalid case
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
async def test_template_action_with_enum(test_args, expected_result, should_raise):
    """Test template action with enum status.
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
        with pytest.raises(ValidationError):
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
        assert result == expected_result
