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
from unittest.mock import AsyncMock, Mock, patch

import pytest
from tracecat_registry import RegistryOAuthSecret, RegistrySecret

from tracecat.agent.authoring_context import (
    ActionRequirementPayload,
    build_enabled_models,
    build_example_from_schema,
    evaluate_configuration,
    filter_configured_actions,
    optional_secret_names,
)
from tracecat.auth.types import Role
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    RegistryVersionManifestAction,
)


@pytest.mark.anyio
async def test_configured_action_filter_uses_required_credentials_and_nested_steps():
    actions = {
        "core.free": [],
        "tools.unconnected": [],
        "core.optional": [
            RegistrySecret(name="optional", keys=["TOKEN"], optional=True)
        ],
        "tools.ready": [RegistrySecret(name="ready", keys=["TOKEN"])],
        "tools.incomplete": [RegistrySecret(name="ready", keys=["TOKEN", "URL"])],
        "tools.missing": [RegistrySecret(name="missing", keys=["TOKEN"])],
        "tools.org": [RegistrySecret(name="org", keys=["TOKEN"])],
        "tools.auth_alternatives": [
            RegistrySecret(
                name="ca_cert",
                keys=["CA_CERTIFICATE"],
                optional=True,
                secret_type="ca_cert",
            ),
            RegistrySecret(name="missing", keys=["TOKEN"], optional=True),
            RegistryOAuthSecret(
                provider_id="example", grant_type="authorization_code", optional=True
            ),
        ],
        "tools.oauth": [
            RegistryOAuthSecret(provider_id="example", grant_type="authorization_code")
        ],
    }
    manifest = RegistryVersionManifest(
        actions={
            name: RegistryVersionManifestAction(
                namespace=name.rsplit(".", 1)[0],
                name=name.rsplit(".", 1)[1],
                description=name,
                action_type="udf",
                interface={"expects": {}, "returns": {}},
                implementation={},
                secrets=secrets,
            )
            for name, secrets in actions.items()
        }
    )
    manifest.actions["tools.nested"] = RegistryVersionManifestAction(
        namespace="tools",
        name="nested",
        description="Nested",
        action_type="template",
        interface={"expects": {}, "returns": {}},
        implementation={
            "template_action": {"definition": {"steps": [{"action": "tools.missing"}]}}
        },
    )
    registry = RegistryActionsService(
        AsyncMock(),
        Role(
            type="service",
            service_id="tracecat-api",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        ),
    )
    names = [*manifest.actions, "tools.deleted"]
    indexed = {name: SimpleNamespace(manifest=manifest) for name in manifest.actions}
    with (
        patch.object(
            registry, "get_actions_from_index", new=AsyncMock(return_value=indexed)
        ) as lookup,
        patch(
            "tracecat.agent.authoring_context.load_secret_inventory",
            new=AsyncMock(
                side_effect=lambda role: {
                    "ready": {"TOKEN"},
                    "ca_cert": {"CA_CERTIFICATE"},
                }
            ),
        ),
        patch(
            "tracecat.agent.authoring_context.load_oauth_inventory",
            new=AsyncMock(
                return_value={
                    ProviderKey(
                        id="example", grant_type=OAuthGrantType.AUTHORIZATION_CODE
                    )
                }
            ),
        ) as oauth,
        patch(
            "tracecat.agent.authoring_context.SecretsService.with_session"
        ) as secret_session,
    ):
        secret_service = secret_session.return_value.__aenter__.return_value
        secret_service.list_org_secrets = AsyncMock(
            return_value=[
                SimpleNamespace(
                    name=name, environment="default", encrypted_keys=b"synthetic"
                )
                for name in ("org", "ready")
            ]
        )
        secret_service.decrypt_keys = Mock(
            return_value=[
                SimpleNamespace(key="TOKEN"),
                SimpleNamespace(key="URL"),
            ]
        )
        assert await filter_configured_actions(
            names, registry=registry, role=registry.role
        ) == [
            "core.free",
            "core.optional",
            "tools.ready",
            "tools.auth_alternatives",
            "tools.oauth",
        ]
        lookup.assert_awaited_once_with(names)
        secret_service.list_org_secrets.assert_not_called()
        role_with_org_secrets = registry.role.model_copy(
            update={"scopes": frozenset({"org:secret:read"})}
        )
        assert await filter_configured_actions(
            names, registry=registry, role=role_with_org_secrets
        ) == [
            "core.free",
            "core.optional",
            "tools.ready",
            "tools.org",
            "tools.auth_alternatives",
            "tools.oauth",
        ]
        # The org URL must not fill the incomplete workspace credential.
        secret_service.list_org_secrets.assert_awaited_once()
        oauth.return_value = set()
        assert await filter_configured_actions(
            names, registry=registry, role=registry.role
        ) == [
            "core.free",
            "core.optional",
            "tools.ready",
        ]


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


class TestComputeAttributedUserScopes:
    """Service roles with an attributed user resolve that user's real scopes."""

    async def test_none_for_user_role(self) -> None:
        from tracecat.auth.credentials import compute_attributed_user_scopes
        from tracecat.auth.types import Role

        role = Role(
            type="user",
            service_id="tracecat-api",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        )
        assert await compute_attributed_user_scopes(role) is None

    async def test_none_for_unattributed_service_role(self) -> None:
        from tracecat.auth.credentials import compute_attributed_user_scopes
        from tracecat.auth.types import Role

        role = Role(
            type="service",
            service_id="tracecat-executor",
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        )
        assert await compute_attributed_user_scopes(role) is None

    async def test_resolves_user_scopes_for_attributed_service_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tracecat.auth import credentials
        from tracecat.auth.types import Role

        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        ws_id = uuid.uuid4()
        role = Role(
            type="service",
            service_id="tracecat-executor",
            user_id=user_id,
            organization_id=org_id,
            workspace_id=ws_id,
        )

        async def _fake_scopes(
            uid: uuid.UUID, oid: uuid.UUID, wid: uuid.UUID | None
        ) -> frozenset[str]:
            assert (uid, oid, wid) == (user_id, org_id, ws_id)
            return frozenset({"workflow:read"})

        monkeypatch.setattr(
            credentials, "_compute_effective_scopes_cached", _fake_scopes
        )

        scopes = await credentials.compute_attributed_user_scopes(role)
        assert scopes == frozenset({"workflow:read"})


class TestAuthoringContextRouteCallerScoping:
    """The internal authoring-context route gates inventory by the real caller.

    The route authenticates the executor service principal (whose allowlist
    includes secret:read/integration:read/variable:read), so the builders must
    be handed a role narrowed to the attributed chat user's scopes.
    """

    @staticmethod
    def _executor_role() -> Any:
        from tracecat.auth.types import Role

        return Role(
            type="service",
            service_id="tracecat-executor",
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            scopes=frozenset(
                {"workflow:read", "secret:read", "integration:read", "variable:read"}
            ),
        )

    async def _call_route(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        role: Any,
        attributed_scopes: frozenset[str] | None,
    ) -> dict[str, frozenset[str]]:
        from tracecat.contexts import ctx_role
        from tracecat.mcp.schemas import WorkflowAuthoringContextRequest
        from tracecat.workflow.executions import internal_router

        seen: dict[str, frozenset[str]] = {}

        async def _fake_attributed(_role: Any) -> frozenset[str] | None:
            return attributed_scopes

        async def _fake_actions(*, role: Any, **_kwargs: Any) -> list[Any]:
            seen["actions"] = role.scopes
            return []

        def _capture(key: str) -> Any:
            async def _fake(*, role: Any) -> list[Any]:
                seen[key] = role.scopes
                return []

            return _fake

        monkeypatch.setattr(
            internal_router, "compute_attributed_user_scopes", _fake_attributed
        )
        monkeypatch.setattr(internal_router, "build_action_contexts", _fake_actions)
        monkeypatch.setattr(
            internal_router, "build_variable_hints", _capture("variable_hints")
        )
        monkeypatch.setattr(
            internal_router, "build_secret_hints", _capture("secret_hints")
        )
        monkeypatch.setattr(
            internal_router, "build_enabled_models", _capture("enabled_models")
        )

        token = ctx_role.set(role)
        try:
            await internal_router.get_authoring_context(
                role=role,
                session=None,  # type: ignore[arg-type]
                params=WorkflowAuthoringContextRequest(),
            )
        finally:
            ctx_role.reset(token)
        return seen

    async def test_narrows_builders_to_attributed_user_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        role = self._executor_role()
        user_scopes = frozenset({"workflow:read"})

        seen = await self._call_route(
            monkeypatch, role=role, attributed_scopes=user_scopes
        )

        assert seen == {
            "actions": user_scopes,
            "variable_hints": user_scopes,
            "secret_hints": user_scopes,
            "enabled_models": user_scopes,
        }

    async def test_unattributed_calls_keep_service_scopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        role = self._executor_role()

        seen = await self._call_route(monkeypatch, role=role, attributed_scopes=None)

        assert seen == {
            "actions": role.scopes,
            "variable_hints": role.scopes,
            "secret_hints": role.scopes,
            "enabled_models": role.scopes,
        }
