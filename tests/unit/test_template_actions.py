import pytest
from tracecat_registry import RegistrySecret

from tracecat.expressions.expectations import ExpectedField
from tracecat.registry import executor
from tracecat.registry.manager import RegistryManager
from tracecat.registry.models import ActionLayer, TemplateAction
from tracecat.registry.store import Registry


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

    version = test_template_action_run.__name__
    registry = Registry(version)
    registry.init(include_base=True, include_remote=False, include_templates=False)
    registry.register_template_action(action)
    assert action.definition.action == "integrations.test.wrapper"
    assert "core.transform.reshape" in registry
    assert action.definition.action in registry

    RegistryManager().add_registry(registry)
    udf = registry.get(action.definition.action)
    result = await executor.run_template(
        udf=udf,
        args={"service_source": "elastic"},
        base_context={},
        version=version,
    )
    assert result == [200, "elastic"]
