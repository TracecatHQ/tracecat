"""Unit tests for registry resolver module."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.exceptions import EntitlementRequired, RegistryError
from tracecat.executor import registry_resolver
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    RegistryVersionManifestAction,
)


@pytest.fixture(autouse=True)
async def clear_resolver_cache():
    """Clear resolver cache before each test."""
    await registry_resolver.clear_cache()
    yield
    await registry_resolver.clear_cache()


def _make_manifest(
    actions: dict[str, dict],
) -> RegistryVersionManifest:
    """Create a test manifest with the given actions."""
    manifest_actions = {}
    for action_name, impl in actions.items():
        namespace, name = action_name.rsplit(".", 1)
        manifest_actions[action_name] = RegistryVersionManifestAction(
            namespace=namespace,
            name=name,
            action_type="udf" if impl.get("type") == "udf" else "template",
            description=f"Test action {action_name}",
            interface={"expects": {}, "returns": {}},
            implementation=impl,
        )
    return RegistryVersionManifest(actions=manifest_actions)


class TestBuildImplIndex:
    """Tests for _build_impl_index function."""

    def test_build_udf_impl(self):
        """Test building impl index for UDF actions."""
        manifest = _make_manifest(
            {
                "core.transform.reshape": {
                    "type": "udf",
                    "url": "tracecat_registry",
                    "module": "tracecat_registry.integrations.core.transform",
                    "name": "reshape",
                }
            }
        )

        index = registry_resolver._build_impl_index(manifest, "tracecat_registry")

        assert "core.transform.reshape" in index
        impl = index["core.transform.reshape"]
        assert impl.type == "udf"
        assert impl.module == "tracecat_registry.integrations.core.transform"
        assert impl.name == "reshape"
        assert impl.origin == "tracecat_registry"

    def test_build_template_impl(self):
        """Test building impl index for template actions."""
        manifest = _make_manifest(
            {
                "tools.zendesk.get_ticket": {
                    "type": "template",
                    "template_action": {
                        "definition": {
                            "name": "get_ticket",
                            "namespace": "tools.zendesk",
                            "title": "Get Zendesk Ticket",
                            "description": "Get a ticket",
                            "display_group": "Zendesk",
                            "expects": {},
                            "steps": [
                                {
                                    "ref": "get_ticket",
                                    "action": "core.http_request",
                                    "args": {"url": "https://api.zendesk.com"},
                                }
                            ],
                            "returns": "${{ steps.get_ticket.result }}",
                        }
                    },
                }
            }
        )

        index = registry_resolver._build_impl_index(manifest, "custom_registry")

        assert "tools.zendesk.get_ticket" in index
        impl = index["tools.zendesk.get_ticket"]
        assert impl.type == "template"
        assert impl.template_definition is not None
        assert impl.origin == "custom_registry"


class TestResolveAction:
    """Tests for resolve_action function."""

    @pytest.mark.anyio
    async def test_resolve_action_success(self):
        """Test successful action resolution with mocked cache."""
        manifest = _make_manifest(
            {
                "core.transform.reshape": {
                    "type": "udf",
                    "url": "tracecat_registry",
                    "module": "tracecat_registry.integrations.core.transform",
                    "name": "reshape",
                }
            }
        )
        impl_index = registry_resolver._build_impl_index(manifest, "tracecat_registry")

        lock = RegistryLock(
            origins={"tracecat_registry": "2024.12.10"},
            actions={"core.transform.reshape": "tracecat_registry"},
        )

        # Mock _get_manifest_entry to return our test data
        with patch.object(
            registry_resolver,
            "_get_manifest_entry",
            new_callable=AsyncMock,
            return_value=(manifest, impl_index),
        ):
            action_impl = await registry_resolver.resolve_action(
                "core.transform.reshape",
                lock,
                organization_id=uuid.uuid4(),
            )

        assert action_impl.type == "udf"
        assert action_impl.module == "tracecat_registry.integrations.core.transform"
        assert action_impl.origin == "tracecat_registry"

    @pytest.mark.anyio
    async def test_resolve_action_not_bound_error(self):
        """Test error when action is not bound in lock."""
        lock = RegistryLock(
            origins={"tracecat_registry": "2024.12.10"},
            actions={},  # No actions bound
        )

        with pytest.raises(RegistryError) as exc_info:
            await registry_resolver.resolve_action(
                "core.transform.reshape",
                lock,
                organization_id=uuid.uuid4(),
            )

        assert "not bound in registry_lock" in str(exc_info.value)

    def test_resolve_action_origin_not_found_error(self):
        """Test error when origin is not in lock.

        Note: RegistryLock model validation now prevents creating locks with
        actions referencing non-existent origins. This test verifies the
        validation error is raised at model construction time.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            RegistryLock(
                origins={},  # No origins
                actions={"core.transform.reshape": "tracecat_registry"},
            )

        assert "Actions reference unknown origins" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_resolve_action_cache_not_populated_error(self):
        """Test error when manifest not found in DB."""
        lock = RegistryLock(
            origins={"tracecat_registry": "2024.12.10"},
            actions={"core.transform.reshape": "tracecat_registry"},
        )

        # Mock _get_manifest_entry to raise RegistryError (simulating DB miss)
        with patch.object(
            registry_resolver,
            "_get_manifest_entry",
            new_callable=AsyncMock,
            side_effect=RegistryError("Registry version not found"),
        ):
            with pytest.raises(RegistryError) as exc_info:
                await registry_resolver.resolve_action(
                    "core.transform.reshape",
                    lock,
                    organization_id=uuid.uuid4(),
                )

            assert "Registry version not found" in str(exc_info.value)


class TestPrefetchLock:
    """Tests for prefetch_lock function."""

    @pytest.mark.anyio
    async def test_prefetch_lock_default_origin_does_not_check_custom_entitlement(self):
        lock = RegistryLock(origins={"tracecat_registry": "2024.12.10"}, actions={})
        manifest = _make_manifest({})

        with (
            patch.object(
                registry_resolver,
                "_has_custom_registry_entitlement",
                new_callable=AsyncMock,
            ) as mock_has_entitlement,
            patch.object(
                registry_resolver,
                "_get_manifest_entry",
                new_callable=AsyncMock,
                return_value=(manifest, {}),
            ) as mock_get_manifest_entry,
        ):
            await registry_resolver.prefetch_lock(lock, organization_id=uuid.uuid4())

        mock_has_entitlement.assert_not_awaited()
        mock_get_manifest_entry.assert_awaited_once()

    @pytest.mark.anyio
    async def test_prefetch_lock_custom_origin_requires_entitlement(self):
        custom_origin = "git+ssh://git@github.com/acme/custom.git"
        lock = RegistryLock(
            origins={custom_origin: "v1"},
            actions={"tools.custom.only_action": custom_origin},
        )

        with (
            patch.object(
                registry_resolver,
                "_has_custom_registry_entitlement",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_has_entitlement,
            patch.object(
                registry_resolver,
                "_get_manifest_entry",
                new_callable=AsyncMock,
            ) as mock_get_manifest_entry,
        ):
            with pytest.raises(
                EntitlementRequired, match="custom_registry"
            ) as exc_info:
                await registry_resolver.prefetch_lock(
                    lock,
                    organization_id=uuid.uuid4(),
                )

        assert exc_info.value.detail is not None
        assert exc_info.value.detail["entitlement"] == "custom_registry"
        assert exc_info.value.detail["unavailable_actions"] == [
            "tools.custom.only_action"
        ]
        assert exc_info.value.detail["unavailable_origin_count"] == 1
        assert "tools.custom.only_action" in str(exc_info.value)
        assert custom_origin not in str(exc_info.value)
        mock_has_entitlement.assert_awaited_once()
        mock_get_manifest_entry.assert_not_awaited()

    @pytest.mark.anyio
    async def test_prefetch_lock_custom_origin_succeeds_when_entitled(self):
        lock = RegistryLock(
            origins={"git+ssh://git@github.com/acme/custom.git": "v1"},
            actions={},
        )
        manifest = _make_manifest({})

        with (
            patch.object(
                registry_resolver,
                "_has_custom_registry_entitlement",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_has_entitlement,
            patch.object(
                registry_resolver,
                "_get_manifest_entry",
                new_callable=AsyncMock,
                return_value=(manifest, {}),
            ) as mock_get_manifest_entry,
        ):
            await registry_resolver.prefetch_lock(lock, organization_id=uuid.uuid4())

        mock_has_entitlement.assert_awaited_once()
        mock_get_manifest_entry.assert_awaited_once()


class TestCollectActionSecretsFromManifest:
    """Tests for collect_action_secrets_from_manifest function."""

    @pytest.mark.anyio
    async def test_collect_udf_secrets(self):
        """Test collecting secrets from UDF action."""
        from tracecat_registry import RegistrySecret

        manifest = _make_manifest(
            {
                "tools.api.call": {
                    "type": "udf",
                    "url": "tracecat_registry",
                    "module": "tracecat_registry.integrations.tools.api",
                    "name": "call",
                }
            }
        )
        # Add secrets to the manifest action using proper RegistrySecret type
        manifest.actions["tools.api.call"].secrets = [
            RegistrySecret(name="api_key", keys=["API_KEY"])
        ]

        impl_index = registry_resolver._build_impl_index(manifest, "tracecat_registry")

        lock = RegistryLock(
            origins={"tracecat_registry": "2024.12.10"},
            actions={"tools.api.call": "tracecat_registry"},
        )

        # Mock _get_manifest_entry to return our test data
        with patch.object(
            registry_resolver,
            "_get_manifest_entry",
            new_callable=AsyncMock,
            return_value=(manifest, impl_index),
        ):
            secrets = await registry_resolver.collect_action_secrets_from_manifest(
                "tools.api.call",
                lock,
                organization_id=uuid.uuid4(),
            )

        assert len(secrets) == 1
        secret = list(secrets)[0]
        assert secret.name == "api_key"

    @pytest.mark.anyio
    async def test_collect_secrets_action_not_bound_error(self):
        """Test error when action is not bound."""
        lock = RegistryLock(
            origins={"tracecat_registry": "2024.12.10"},
            actions={},
        )

        with pytest.raises(RegistryError) as exc_info:
            await registry_resolver.collect_action_secrets_from_manifest(
                "tools.api.call",
                lock,
                organization_id=uuid.uuid4(),
            )

        assert "not bound in registry_lock" in str(exc_info.value)
