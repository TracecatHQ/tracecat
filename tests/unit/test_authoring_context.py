"""Unit tests for the shared workflow authoring-context module.

These cover the pure, infra-free pieces (schema/example/requirement shaping)
used by the MCP ``get_workflow_authoring_context`` tool, the
``/internal/workflows/authoring-context`` endpoint, and the
``core.workflow.get_authoring_context`` registry action. The DB- and
registry-backed builders (``build_action_contexts`` etc.) are exercised through
the MCP integration path.
"""

from __future__ import annotations

from tracecat.agent.authoring_context import (
    ActionRequirementPayload,
    build_example_from_schema,
    evaluate_configuration,
    optional_secret_names,
)


class TestBuildExampleFromSchema:
    """``build_example_from_schema`` derives a payload from required props."""

    def test_only_required_props_typed_by_json_type(self):
        schema = {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "items": {"type": "array"},
                "body": {"type": "object"},
                "anything": {},
                "optional": {"type": "string"},
            },
            "required": [
                "url",
                "count",
                "ratio",
                "enabled",
                "items",
                "body",
                "anything",
            ],
        }

        example = build_example_from_schema(schema)

        assert example == {
            "url": "example",
            "count": 1,
            "ratio": 1.0,
            "enabled": True,
            "items": [],
            "body": {},
            "anything": "value",
        }
        # Non-required props are never included.
        assert "optional" not in example

    def test_no_required_yields_empty_example(self):
        assert (
            build_example_from_schema({"properties": {"x": {"type": "string"}}}) == {}
        )


class TestOptionalSecretNames:
    """``optional_secret_names`` lists only optional *secret* requirements."""

    def test_filters_to_optional_secrets(self):
        requirements: list[ActionRequirementPayload] = [
            {
                "type": "secret",
                "name": "required_api",
                "required_keys": ["KEY"],
                "optional_keys": [],
                "optional": False,
            },
            {
                "type": "secret",
                "name": "ca_cert",
                "required_keys": [],
                "optional_keys": ["CERT"],
                "optional": True,
            },
            {
                "type": "oauth",
                "name": "github_oauth",
                "provider_id": "github",
                "grant_type": "authorization_code",
                "optional": True,
            },
        ]

        assert optional_secret_names(requirements) == ["ca_cert"]


class TestEvaluateConfiguration:
    """``evaluate_configuration`` reports readiness against the inventories."""

    def test_missing_secret_key_is_reported(self):
        requirements: list[ActionRequirementPayload] = [
            {
                "type": "secret",
                "name": "api",
                "required_keys": ["TOKEN"],
                "optional_keys": [],
                "optional": False,
            }
        ]

        configured, missing = evaluate_configuration(requirements, {"api": set()})

        assert configured is False
        assert missing == ["missing key: api.TOKEN"]

    def test_optional_secret_never_blocks(self):
        requirements: list[ActionRequirementPayload] = [
            {
                "type": "secret",
                "name": "ca_cert",
                "required_keys": ["CERT"],
                "optional_keys": [],
                "optional": True,
            }
        ]

        configured, missing = evaluate_configuration(requirements, {})

        assert configured is True
        assert missing == []

    def test_all_required_keys_present_is_configured(self):
        requirements: list[ActionRequirementPayload] = [
            {
                "type": "secret",
                "name": "api",
                "required_keys": ["TOKEN"],
                "optional_keys": [],
                "optional": False,
            }
        ]

        configured, missing = evaluate_configuration(requirements, {"api": {"TOKEN"}})

        assert configured is True
        assert missing == []
