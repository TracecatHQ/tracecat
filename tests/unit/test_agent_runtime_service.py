"""Tests for the agent runtime service (model resolution, credential assembly)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tracecat.agent.runtime.constants import (
    SOURCE_RUNTIME_API_KEY,
    SOURCE_RUNTIME_API_KEY_HEADER,
    SOURCE_RUNTIME_API_VERSION,
    SOURCE_RUNTIME_BASE_URL,
)
from tracecat.agent.runtime.service import AgentRuntimeService
from tracecat.agent.runtime.types import ResolvedExecutionContext
from tracecat.agent.schemas import ModelSelection
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.db.models import AgentCatalog, AgentModelSelectionLink, AgentSource
from tracecat.exceptions import TracecatNotFoundError


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset(
            {
                "agent:read",
                "agent:update",
                "org:secret:read",
                "workspace:read",
                "workspace:update",
            }
        ),
    )


def _make_service(role: Role) -> AgentRuntimeService:
    service = AgentRuntimeService(AsyncMock(), role=role)
    service.selections = Mock()
    return service


# ---------------------------------------------------------------------------
# resolve_runtime_agent_config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_runtime_agent_config_prefers_enabled_selection(
    role: Role,
) -> None:
    service = _make_service(role)
    source_id = uuid.uuid4()
    config = AgentConfig(
        model_name="stale-model",
        model_provider="stale-provider",
        source_id=source_id,
        instructions="Use this style",
        actions=["core.http_request"],
        namespaces=["tools.openai"],
        model_settings={"temperature": 0.1},
        retries=7,
        enable_internet_access=True,
        base_url="https://override.example/v1",
    )
    resolved_config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
        source_id=source_id,
        base_url="https://override.example/v1",
        instructions="Use this style",
        actions=["core.http_request"],
        namespaces=["tools.openai"],
        model_settings={"temperature": 0.1},
        retries=7,
        enable_internet_access=True,
    )
    service.resolve_execution_context = AsyncMock(
        return_value=ResolvedExecutionContext(
            config=resolved_config,
            credentials={},
            catalog=None,
            selection_link=None,
            source=None,
        )
    )

    resolved = await service.resolve_runtime_agent_config(config)

    service.resolve_execution_context.assert_awaited_once_with(config)
    assert resolved.model_name == "gpt-5.2"
    assert resolved.model_provider == "openai"
    assert resolved.source_id == source_id
    assert resolved.base_url == "https://override.example/v1"
    assert resolved.instructions == "Use this style"
    assert resolved.actions == ["core.http_request"]
    assert resolved.namespaces == ["tools.openai"]
    assert resolved.model_settings == {"temperature": 0.1}
    assert resolved.retries == 7
    assert resolved.enable_internet_access is True


# ---------------------------------------------------------------------------
# _build_runtime_credentials — bedrock profile override
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_runtime_credentials_prefers_enabled_bedrock_profile(
    role: Role,
) -> None:
    service = _make_service(role)
    catalog = Mock(spec=AgentCatalog)
    catalog.source_id = None
    catalog.model_provider = "bedrock"
    catalog.model_name = "anthropic.claude-sonnet-4-6"

    selection_link = Mock(spec=AgentModelSelectionLink)
    selection_link.enabled_config = {"bedrock_inference_profile_id": "profile-123"}

    config = AgentConfig(
        model_name="anthropic.claude-sonnet-4-6", model_provider="bedrock"
    )

    service._load_provider_credentials = AsyncMock(
        return_value={"AWS_REGION": "us-east-1"}
    )

    credentials = await service._build_runtime_credentials(
        catalog=catalog,
        source=None,
        selection_link=selection_link,
        config=config,
    )

    assert credentials == {
        "AWS_REGION": "us-east-1",
        "AWS_INFERENCE_PROFILE_ID": "profile-123",
    }


# ---------------------------------------------------------------------------
# _build_runtime_credentials — source-backed protocol details
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_runtime_credentials_preserves_source_protocol_details(
    role: Role,
) -> None:
    service = _make_service(role)
    source_id = uuid.uuid4()
    catalog = Mock(spec=AgentCatalog)
    catalog.source_id = source_id
    catalog.model_provider = "anthropic"
    catalog.model_name = "claude-3-7-sonnet"

    source = Mock(spec=AgentSource)
    source.id = source_id
    source.base_url = "https://anthropic.gateway.example"
    source.api_key_header = "X-Api-Key"
    source.api_version = "2024-06-01"
    source.declared_models = []
    source.model_provider = "manual_custom"

    config = AgentConfig(model_name="claude-3-7-sonnet", model_provider="anthropic")

    with (
        patch(
            "tracecat.agent.runtime.service.deserialize_source_config",
            return_value={"api_key": "source-key"},
        ),
        patch(
            "tracecat.agent.runtime.service.source_runtime_base_url",
            return_value="https://anthropic.gateway.example",
        ),
    ):
        credentials = await service._build_runtime_credentials(
            catalog=catalog,
            source=source,
            selection_link=None,
            config=config,
        )

    assert credentials == {
        SOURCE_RUNTIME_API_KEY: "source-key",
        SOURCE_RUNTIME_API_KEY_HEADER: "X-Api-Key",
        SOURCE_RUNTIME_API_VERSION: "2024-06-01",
        SOURCE_RUNTIME_BASE_URL: "https://anthropic.gateway.example",
        "ANTHROPIC_API_KEY": "source-key",
        "ANTHROPIC_BASE_URL": "https://anthropic.gateway.example",
    }


# ---------------------------------------------------------------------------
# _build_runtime_credentials — org secret only (no source, no profile)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_runtime_credentials_uses_org_secret_only(
    role: Role,
) -> None:
    service = _make_service(role)
    catalog = Mock(spec=AgentCatalog)
    catalog.source_id = None
    catalog.model_provider = "openai"
    catalog.model_name = "gpt-5.2"

    config = AgentConfig(model_name="gpt-5.2", model_provider="openai")

    service._load_provider_credentials = AsyncMock(return_value=None)

    credentials = await service._build_runtime_credentials(
        catalog=catalog,
        source=None,
        selection_link=None,
        config=config,
    )

    assert credentials == {}


# ---------------------------------------------------------------------------
# get_runtime_credentials_for_config — prefers enabled selection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_runtime_credentials_for_config_prefers_enabled_selection(
    role: Role,
) -> None:
    service = _make_service(role)
    config = AgentConfig(
        model_name="claude-3-7-sonnet",
        model_provider="bedrock",
    )
    service.resolve_execution_context = AsyncMock(
        return_value=ResolvedExecutionContext(
            config=config,
            credentials={"AWS_INFERENCE_PROFILE_ID": "profile-123"},
            catalog=Mock(spec=AgentCatalog),
            selection_link=Mock(spec=AgentModelSelectionLink),
            source=None,
        )
    )

    credentials = await service.get_runtime_credentials_for_config(config)

    assert credentials == {"AWS_INFERENCE_PROFILE_ID": "profile-123"}
    service.resolve_execution_context.assert_awaited_once_with(config)


# ---------------------------------------------------------------------------
# get_runtime_credentials_for_config — falls back when selection disabled
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_runtime_credentials_for_config_falls_back_when_selection_disabled(
    role: Role,
) -> None:
    """When the model is not in the catalog, resolve_execution_context falls
    through to the direct-provider credential path and returns the raw
    provider credentials."""
    service = _make_service(role)
    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
    )
    # Simulate the fallback path: no catalog match, so credentials come
    # from the provider secret directly.
    service.resolve_execution_context = AsyncMock(
        return_value=ResolvedExecutionContext(
            config=config,
            credentials={"OPENAI_API_KEY": "raw-key"},
            catalog=None,
            selection_link=None,
            source=None,
        )
    )

    credentials = await service.get_runtime_credentials_for_config(config)

    assert credentials == {"OPENAI_API_KEY": "raw-key"}
    service.resolve_execution_context.assert_awaited_once_with(config)


# ---------------------------------------------------------------------------
# with_model_config — rejects workspace-excluded default model
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_with_model_config_rejects_workspace_excluded_default_model(
    role: Role,
) -> None:
    service = _make_service(role)
    default_selection = ModelSelection(
        source_id=None,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    service.selections.get_default_model = AsyncMock(return_value=default_selection)
    # _resolve_enabled_context (called by with_model_config) delegates to
    # selections._resolve_enabled_catalog which raises when excluded.
    service._resolve_enabled_context = AsyncMock(
        side_effect=TracecatNotFoundError("Model openai/gpt-5.2 is not enabled")
    )

    with pytest.raises(
        TracecatNotFoundError,
        match="Model openai/gpt-5.2 is not enabled",
    ):
        async with service.with_model_config():
            pytest.fail(
                "with_model_config should not yield when the default is excluded"
            )


# ---------------------------------------------------------------------------
# _resolve_preset_selection — rejects disabled catalog selection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_preset_selection_rejects_disabled_catalog_selection(
    role: Role,
) -> None:
    service = _make_service(role)
    config = AgentConfig(
        model_name="gpt-5.2",
        model_provider="openai",
    )
    service.selections.is_model_enabled = AsyncMock(return_value=False)
    service.selections._lookup_from_selection = Mock(
        return_value=Mock(source_id=None, model_provider="openai", model_name="gpt-5.2")
    )
    # Legacy fallback also finds nothing.
    with patch(
        "tracecat.agent.runtime.service.resolve_enabled_catalog_match_for_provider_model",
        new_callable=AsyncMock,
        return_value=Mock(
            status="unmatched", source_id=None, model_provider=None, model_name=None
        ),
    ):
        service.selections._selection_from_match = Mock(return_value=None)

        result = await service._resolve_preset_selection(config)

    assert result is None


# ---------------------------------------------------------------------------
# _resolve_preset_selection — does not fallback for source-backed model
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resolve_preset_selection_does_not_fallback_when_source_backed_model_is_missing(
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _make_service(role)
    config = AgentConfig(
        source_id=uuid.uuid4(),
        model_name="gpt-5.2",
        model_provider="openai",
    )
    service.selections.is_model_enabled = AsyncMock(return_value=False)
    service.selections._lookup_from_selection = Mock(
        return_value=Mock(
            source_id=config.source_id,
            model_provider="openai",
            model_name="gpt-5.2",
        )
    )
    # When source_id is set and the exact selection is not enabled, the
    # legacy fallback must NOT run — it would drop source_id and could
    # silently route to a different source's credentials.
    legacy_fallback = AsyncMock()
    monkeypatch.setattr(
        "tracecat.agent.runtime.service.resolve_enabled_catalog_match_for_provider_model",
        legacy_fallback,
    )

    result = await service._resolve_preset_selection(config)

    assert result is None
    legacy_fallback.assert_not_awaited()


# ---------------------------------------------------------------------------
# _build_runtime_credentials — discovered runtime base URL for gateway
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_runtime_credentials_uses_discovered_runtime_base_url_for_gateway(
    role: Role,
) -> None:
    service = _make_service(role)
    source_id = uuid.uuid4()
    catalog = Mock(spec=AgentCatalog)
    catalog.source_id = source_id
    catalog.model_provider = "openai_compatible_gateway"
    catalog.model_name = "qwen2.5:0.5b"

    source = Mock(spec=AgentSource)
    source.id = source_id
    source.base_url = "http://host.docker.internal:11434"
    source.api_key_header = None
    source.api_version = None
    source.encrypted_config = b"encrypted"
    source.declared_models = None
    source.model_provider = "openai_compatible_gateway"

    config = AgentConfig(
        model_name="qwen2.5:0.5b",
        model_provider="openai_compatible_gateway",
    )

    with (
        patch(
            "tracecat.agent.runtime.service.deserialize_source_config",
            return_value={
                "api_key": "not-needed",
                "runtime_base_url": "http://host.docker.internal:11434/v1",
            },
        ),
        patch(
            "tracecat.agent.runtime.service.source_runtime_base_url",
            return_value="http://host.docker.internal:11434/v1",
        ),
    ):
        credentials = await service._build_runtime_credentials(
            catalog=catalog,
            source=source,
            selection_link=None,
            config=config,
        )

    assert (
        credentials[SOURCE_RUNTIME_BASE_URL] == "http://host.docker.internal:11434/v1"
    )
    assert credentials["OPENAI_BASE_URL"] == "http://host.docker.internal:11434/v1"
    assert credentials["OPENAI_API_KEY"] == "not-needed"


# ---------------------------------------------------------------------------
# _build_runtime_credentials — no catalog (direct provider path)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_build_runtime_credentials_no_catalog_loads_provider_secret(
    role: Role,
) -> None:
    """When catalog is None and a model_provider is given, credentials come
    straight from the org provider secret."""
    service = _make_service(role)
    config = AgentConfig(model_name="gpt-5.2", model_provider="openai")

    service._load_provider_credentials = AsyncMock(
        return_value={"OPENAI_API_KEY": "org-key"}
    )

    credentials = await service._build_runtime_credentials(
        catalog=None,
        source=None,
        selection_link=None,
        config=config,
    )

    service._load_provider_credentials.assert_awaited_once_with("openai")
    assert credentials == {"OPENAI_API_KEY": "org-key"}


# ---------------------------------------------------------------------------
# _runtime_config_from_catalog — overrides preserved
# ---------------------------------------------------------------------------


def test_runtime_config_from_catalog_preserves_overrides(role: Role) -> None:
    service = _make_service(role)
    catalog = Mock(spec=AgentCatalog)
    catalog.source_id = None
    catalog.model_provider = "openai"
    catalog.model_name = "gpt-5.2"

    overrides = AgentConfig(
        model_name="ignored",
        model_provider="ignored",
        instructions="Be concise",
        actions=["core.http_request"],
        namespaces=["tools.slack"],
        model_settings={"temperature": 0.5},
        retries=3,
        enable_internet_access=True,
        base_url="https://custom.api/v1",
    )

    result = service._runtime_config_from_catalog(
        catalog=catalog,
        source=None,
        overrides=overrides,
    )

    # Model identity comes from catalog, not overrides.
    assert result.model_name == "gpt-5.2"
    assert result.model_provider == "openai"
    # Behavioral overrides are preserved.
    assert result.instructions == "Be concise"
    assert result.actions == ["core.http_request"]
    assert result.namespaces == ["tools.slack"]
    assert result.model_settings == {"temperature": 0.5}
    assert result.retries == 3
    assert result.enable_internet_access is True
    # base_url override takes priority over source.
    assert result.base_url == "https://custom.api/v1"
