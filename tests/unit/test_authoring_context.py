"""Unit tests for the shared workflow authoring-context module.

These cover the pure, infra-free pieces (schema/example/requirement shaping)
used by the MCP ``get_workflow_authoring_context`` tool, the
``/internal/workflows/authoring-context`` endpoint, and the
``core.workflow.get_authoring_context`` registry action. The DB- and
registry-backed builders (``build_action_contexts`` etc.) are exercised through
the MCP integration path.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from tracecat.agent.authoring_context import (
    ActionRequirementPayload,
    build_enabled_models,
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


pytestmark = pytest.mark.anyio


class _AsyncContext:
    """Minimal async context manager yielding a fixed value."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *_args: Any) -> None:
        return None


class TestBuildEnabledModels:
    """``build_enabled_models`` surfaces workspace-scoped models as hints."""

    async def test_returns_workspace_models_with_agent_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_id = uuid.uuid4()
        catalog_id = uuid.uuid4()
        role = SimpleNamespace(
            scopes=frozenset({"agent:read"}),
            workspace_id=workspace_id,
        )

        class _AccessService:
            async def get_workspace_models(self, ws_id: uuid.UUID) -> list[Any]:
                assert ws_id == workspace_id
                return [
                    SimpleNamespace(
                        id=catalog_id,
                        model_name="claude-opus-4-8",
                        model_provider="anthropic",
                    )
                ]

        monkeypatch.setattr(
            "tracecat.agent.authoring_context.AgentModelAccessService.with_session",
            lambda role: _AsyncContext(_AccessService()),
        )

        models = await build_enabled_models(role=role)  # type: ignore[arg-type]

        assert len(models) == 1
        assert models[0].catalog_id == str(catalog_id)
        assert models[0].model_name == "claude-opus-4-8"
        assert models[0].model_provider == "anthropic"

    async def test_empty_without_agent_read_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        role = SimpleNamespace(
            scopes=frozenset({"workflow:read"}),
            workspace_id=uuid.uuid4(),
        )

        def _fail(_role: Any) -> Any:  # pragma: no cover - must not be called
            raise AssertionError("must not read models without agent:read")

        monkeypatch.setattr(
            "tracecat.agent.authoring_context.AgentModelAccessService.with_session",
            _fail,
        )

        assert await build_enabled_models(role=role) == []  # type: ignore[arg-type]

    async def test_empty_without_workspace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        role = SimpleNamespace(
            scopes=frozenset({"agent:read"}),
            workspace_id=None,
        )

        def _fail(_role: Any) -> Any:  # pragma: no cover - must not be called
            raise AssertionError("must not read models without a workspace")

        monkeypatch.setattr(
            "tracecat.agent.authoring_context.AgentModelAccessService.with_session",
            _fail,
        )

        assert await build_enabled_models(role=role) == []  # type: ignore[arg-type]
