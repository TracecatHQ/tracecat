import pytest
from tracecat_registry._internal.models import RegistrySecret

from tracecat.expressions.expectations import ExpectedField
from tracecat.registry.store import Registry
from tracecat.registry.template_actions import ActionLayer, TemplateAction


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
            "layers": [
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
                            "${{ layers.base.result.data + 100 }}",
                            "${{ layers.base.result.service_source }}",
                        ]
                    },
                },
            ],
            "returns": "${{ layers.final.result }}",
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
        assert action.definition.layers == [
            ActionLayer(
                ref="base",
                action="core.transform.reshape",
                args={
                    "value": {
                        "service_source": "${{ inputs.service_source }}",
                        "data": 100,
                    }
                },
            ),
            ActionLayer(
                ref="final",
                action="core.transform.reshape",
                args={
                    "value": [
                        "${{ layers.base.result.data + 100 }}",
                        "${{ layers.base.result.service_source }}",
                    ]
                },
            ),
        ]


@pytest.mark.asyncio
async def test_template_action_run(base_registry: Registry):
    assert "core.transform.reshape" in base_registry
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
                    "limit": {"type": "int | None", "description": "The limit"},
                },
                "layers": [
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
                                "${{ layers.base.result.data + 100 }}",
                                "${{ layers.base.result.service_source }}",
                            ]
                        },
                    },
                ],
                "returns": "${{ layers.final.result }}",
            },
        }
    )

    result = await action.run(
        args={"service_source": "elastic"},
        base_context={},
        registry=base_registry,
    )
    assert result == [200, "elastic"]
