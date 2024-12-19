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

    try:
        action = TemplateAction.model_validate(data)
    except Exception as e:
        pytest.fail(f"Failed to construct template action: {e}")
    else:
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

    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)
    assert action.definition.action == "integrations.test.wrapper"
    assert "core.transform.reshape" in registry
    assert action.definition.action in registry

    bound_action = registry.get(action.definition.action)
    result = await service.run_template_action(
        action=bound_action,
        args={"service_source": "elastic"},
        context={},
    )
    assert result == [200, "elastic"]


@pytest.mark.anyio
async def test_template_action_with_enum():
    """Test template action with enum field types.
    This test verifies that:
    1. Enum fields can be properly defined in template action expectations
    2. Enum values are correctly validated during model construction
    3. Default enum values are respected when not provided
    4. Enum values are correctly serialized to strings in template expressions
    5. The entire flow works in a realistic alerting system scenario
    The test uses a simulated alert action that accepts a severity level (critical/warning/info)
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

    try:
        action = TemplateAction.model_validate(data)
    except Exception as e:
        pytest.fail(f"Failed to construct template action: {e}")
    else:
        assert action.definition.title == "Test Alert Action"
        assert (
            action.definition.expects["status"].type
            == 'enum["critical", "warning", "info"]'
        )
        assert action.definition.expects["status"].default == "info"

    # Test running the action
    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)

    bound_action = registry.get(action.definition.action)
    result = await service.run_template_action(
        action=bound_action,
        args={"status": "critical", "message": "System CPU usage above 90%"},
        context={},
    )

    assert result == {
        "alert_status": "critical",
        "alert_message": "System CPU usage above 90%",
    }

    # Test with default status
    result_default = await service.run_template_action(
        action=bound_action,
        args={"message": "Informational message"},
        context={},
    )

    assert result_default == {
        "alert_status": "info",
        "alert_message": "Informational message",
    }


@pytest.mark.anyio
async def test_template_action_with_invalid_enum():
    """Test template action with invalid enum value.
    This test verifies that:
    1. Invalid enum values are properly rejected
    2. The error message is descriptive and helpful
    3. The validation happens at runtime during template execution
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

    action = TemplateAction.model_validate(data)
    registry = Repository()
    registry.init(include_base=True, include_templates=False)
    registry.register_template_action(action)

    bound_action = registry.get(action.definition.action)
    with pytest.raises(ValidationError) as exc_info:
        await service.run_template_action(
            action=bound_action,
            args={
                "status": "emergency",  # Invalid status - not in enum
                "message": "This should fail",
            },
            context={},
        )

    error_msg = str(exc_info.value)
    assert "status" in error_msg
    assert "emergency" in error_msg
    assert any(
        "critical" in msg or "warning" in msg or "info" in msg
        for msg in error_msg.split("\n")
    )
