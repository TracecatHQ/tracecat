import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from tracecat.agent.internal_router import (
    _provider_secrets_context,
    rank_items_endpoint,
    rank_items_pairwise_endpoint,
    run_agent_endpoint,
)
from tracecat.agent.schemas import (
    AgentConfigSchema,
    InternalRankItemsPairwiseRequest,
    InternalRankItemsRequest,
    InternalRunAgentRequest,
)
from tracecat.agent.types import AgentConfig
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatNotFoundError


@pytest.mark.anyio
async def test_provider_secrets_context_preserves_explicit_base_url_override() -> None:
    config = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
        base_url="https://override.example/v1",
    )
    credentials = {
        "OPENAI_API_KEY": "test-key",
        "OPENAI_BASE_URL": "https://creds.example/v1",
    }

    async with _provider_secrets_context(config, credentials):
        assert config.base_url == "https://override.example/v1"


@pytest.mark.anyio
async def test_provider_secrets_context_uses_workspace_credentials_fallback() -> None:
    config = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
    )
    credentials = {"OPENAI_API_KEY": "workspace-key"}

    async with _provider_secrets_context(config, credentials):
        pass


@pytest.mark.anyio
async def test_provider_secrets_context_uses_provider_base_url_override() -> None:
    config = AgentConfig(
        model_name="gpt-5",
        model_provider="openai",
        base_url=None,
    )
    credentials = {
        "OPENAI_API_KEY": "workspace-key",
        "OPENAI_BASE_URL": "https://gateway.example/v1",
    }
    captured: dict[str, str] = {}

    def _set_context(creds: dict[str, str]) -> str:
        captured.update(creds)
        return "token"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.set_context",
        _set_context,
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.reset_context",
        lambda _token: None,
    )
    try:
        async with _provider_secrets_context(config, credentials):
            assert config.base_url == "https://gateway.example/v1"
            assert captured == credentials
    finally:
        monkeypatch.undo()


@pytest.mark.anyio
async def test_provider_secrets_context_prefers_runtime_credentials_for_enabled_builtin_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AgentConfig(
        model_name="claude-3-7-sonnet",
        model_provider="bedrock",
    )
    credentials = {"AWS_INFERENCE_PROFILE_ID": "profile-123"}
    captured: dict[str, str] = {}

    def _set_context(creds: dict[str, str]) -> str:
        captured.update(creds)
        return "token"

    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.set_context",
        _set_context,
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.reset_context",
        lambda _token: None,
    )

    async with _provider_secrets_context(config, credentials):
        assert captured == {"AWS_INFERENCE_PROFILE_ID": "profile-123"}


@pytest.mark.anyio
async def test_provider_secrets_context_loads_custom_source_credentials_without_catalog_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = uuid.uuid4()
    config = AgentConfig(
        model_name="claude-3-7-sonnet",
        model_provider="anthropic",
        source_id=source_id,
        base_url=None,
    )
    credentials = {
        "ANTHROPIC_API_KEY": "test-key",
        "TRACECAT_SOURCE_BASE_URL": "https://anthropic.gateway.example",
    }
    captured: dict[str, str] = {}

    def _set_context(creds: dict[str, str]) -> str:
        captured.update(creds)
        return "token"

    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.set_context",
        _set_context,
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.reset_context",
        lambda _token: None,
    )

    async with _provider_secrets_context(config, credentials):
        assert config.base_url == "https://anthropic.gateway.example"
        assert captured == credentials


@pytest.mark.anyio
async def test_provider_secrets_context_merges_source_auth_header_into_model_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = AgentConfig(
        model_name="gemini-2.5-flash",
        model_provider="gemini",
        model_settings={"temperature": 0.1},
    )
    credentials = {
        "TRACECAT_SOURCE_API_KEY": "source-key",
        "TRACECAT_SOURCE_API_KEY_HEADER": "X-Api-Key",
        "TRACECAT_SOURCE_BASE_URL": "https://gateway.example/v1",
    }
    captured: dict[str, str] = {}

    def _set_context(creds: dict[str, str]) -> str:
        captured.update(creds)
        return "token"

    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.set_context",
        _set_context,
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.registry_secrets.reset_context",
        lambda _token: None,
    )

    async with _provider_secrets_context(config, credentials):
        assert config.base_url == "https://gateway.example/v1"
        assert config.model_settings == {
            "temperature": 0.1,
            "extra_headers": {"X-Api-Key": "source-key"},
        }
        assert captured == {
            "TRACECAT_SOURCE_API_KEY": "source-key",
            "TRACECAT_SOURCE_API_KEY_HEADER": "X-Api-Key",
            "TRACECAT_SOURCE_BASE_URL": "https://gateway.example/v1",
        }


@pytest.mark.anyio
async def test_rank_items_endpoint_resolves_runtime_config_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = cast(
        Role,
        SimpleNamespace(
            type="service",
            service_id="tracecat-service",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            scopes=frozenset({"agent:execute"}),
        ),
    )
    resolved_config = AgentConfig(
        source_id=uuid.uuid4(),
        model_name="resolved-model",
        model_provider="openai",
    )
    require_enabled_model_selection = AsyncMock()
    resolve_execution_context = AsyncMock(
        return_value=SimpleNamespace(
            config=resolved_config,
            credentials={"OPENAI_API_KEY": "key"},
        )
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.AgentRuntimeService",
        lambda session, role: SimpleNamespace(
            resolve_execution_context=resolve_execution_context,
            selections=SimpleNamespace(
                require_enabled_model_selection=require_enabled_model_selection,
            ),
        ),
    )
    ranker_rank_items = AsyncMock(return_value=["item-1"])
    monkeypatch.setattr(
        "tracecat.agent.internal_router.ranker_rank_items", ranker_rank_items
    )

    token = ctx_role.set(role)
    try:
        result = await rank_items_endpoint(
            role=role,
            session=AsyncMock(),
            params=InternalRankItemsRequest(
                items=[{"id": "item-1", "text": "First"}],
                criteria_prompt="Rank items",
                source_id=uuid.uuid4(),
                model_name="stale-model",
                model_provider="openai",
            ),
        )
    finally:
        ctx_role.reset(token)

    assert result == ["item-1"]
    resolve_execution_context.assert_awaited_once()
    require_enabled_model_selection.assert_awaited_once()
    assert require_enabled_model_selection.await_args is not None
    enabled_selection = require_enabled_model_selection.await_args.args[0]
    assert enabled_selection.source_id == resolved_config.source_id
    assert enabled_selection.model_name == resolved_config.model_name
    assert enabled_selection.model_provider == resolved_config.model_provider
    ranker_rank_items.assert_awaited_once()
    assert ranker_rank_items.await_args is not None
    assert ranker_rank_items.await_args.kwargs["model_name"] == "resolved-model"


@pytest.mark.anyio
async def test_rank_items_pairwise_endpoint_resolves_runtime_config_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = cast(
        Role,
        SimpleNamespace(
            type="service",
            service_id="tracecat-service",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            scopes=frozenset({"agent:execute"}),
        ),
    )
    resolved_config = AgentConfig(
        source_id=uuid.uuid4(),
        model_name="resolved-model",
        model_provider="openai",
    )
    require_enabled_model_selection = AsyncMock()
    resolve_execution_context = AsyncMock(
        return_value=SimpleNamespace(
            config=resolved_config,
            credentials={"OPENAI_API_KEY": "key"},
        )
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.AgentRuntimeService",
        lambda session, role: SimpleNamespace(
            resolve_execution_context=resolve_execution_context,
            selections=SimpleNamespace(
                require_enabled_model_selection=require_enabled_model_selection,
            ),
        ),
    )
    ranker_rank_items_pairwise = AsyncMock(return_value=["item-1"])
    monkeypatch.setattr(
        "tracecat.agent.internal_router.ranker_rank_items_pairwise",
        ranker_rank_items_pairwise,
    )

    token = ctx_role.set(role)
    try:
        result = await rank_items_pairwise_endpoint(
            role=role,
            session=AsyncMock(),
            params=InternalRankItemsPairwiseRequest(
                items=[{"id": "item-1", "text": "First"}],
                criteria_prompt="Rank items",
                source_id=uuid.uuid4(),
                model_name="stale-model",
                model_provider="openai",
            ),
        )
    finally:
        ctx_role.reset(token)

    assert result == ["item-1"]
    resolve_execution_context.assert_awaited_once()
    require_enabled_model_selection.assert_awaited_once()
    assert require_enabled_model_selection.await_args is not None
    enabled_selection = require_enabled_model_selection.await_args.args[0]
    assert enabled_selection.source_id == resolved_config.source_id
    assert enabled_selection.model_name == resolved_config.model_name
    assert enabled_selection.model_provider == resolved_config.model_provider
    ranker_rank_items_pairwise.assert_awaited_once()
    assert ranker_rank_items_pairwise.await_args is not None
    assert (
        ranker_rank_items_pairwise.await_args.kwargs["model_name"] == "resolved-model"
    )


@pytest.mark.anyio
async def test_run_agent_endpoint_maps_missing_model_selection_to_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = cast(
        Role,
        SimpleNamespace(
            type="service",
            service_id="tracecat-service",
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            scopes=frozenset({"agent:execute"}),
        ),
    )
    resolve_execution_context = AsyncMock(
        side_effect=TracecatNotFoundError("Model openai/stale-model is not enabled")
    )
    monkeypatch.setattr(
        "tracecat.agent.internal_router.AgentRuntimeService",
        lambda session, role: SimpleNamespace(
            resolve_execution_context=resolve_execution_context,
        ),
    )

    token = ctx_role.set(role)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await run_agent_endpoint(
                role=role,
                session=AsyncMock(),
                params=InternalRunAgentRequest(
                    user_prompt="hello",
                    config=AgentConfigSchema(
                        model_name="stale-model",
                        model_provider="openai",
                    ),
                ),
            )
    finally:
        ctx_role.reset(token)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Model openai/stale-model is not enabled"
