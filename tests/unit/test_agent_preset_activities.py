from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.api.failure.v1 import Failure
from temporalio.exceptions import ActivityError, ApplicationError
from tracecat_ee.agent.workflows.durable import _agent_activity_classification

from tracecat.agent.preset.activities import (
    ResolveAgentPresetConfigActivityInput,
    ResolveAgentPresetVersionRefActivityInput,
    ResolveAgentsConfigActivityInput,
    resolve_agent_preset_config_activity,
    resolve_agent_preset_version_ref_activity,
    resolve_agents_config_activity,
    resolve_custom_model_provider_config_activity,
)
from tracecat.agent.preset.resolver import (
    ResolvedAgentsRuntimeConfig,
    ResolvedSubagentConfig,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.subagents import AgentSubagentsConfig, ResolvedAttachedSubagentRef
from tracecat.agent.types import AgentConfig
from tracecat.agent.workflow_schemas import AgentConfigPayload
from tracecat.auth.types import Role
from tracecat.dsl._converter import get_data_converter
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.runtime.errors import (
    RetryDisposition,
    RuntimeErrorKind,
    RuntimeErrorOwner,
)
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.temporal.errors import (
    extract_error_classification,
    raise_wrapped_application_error,
)


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FailingAsyncContext:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aenter__(self) -> None:
        raise self._error

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture(scope="session")
def minio_server() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.fixture(scope="session", autouse=True)
def default_org() -> Iterator[None]:
    """Storage is stubbed throughout this activity unit-test module."""
    yield


@pytest.fixture(autouse=True)
def clean_redis_db() -> Iterator[None]:
    """These activity unit tests do not use Redis."""
    yield


@pytest.mark.anyio
async def test_resolve_agent_preset_version_ref_activity_returns_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(id=uuid.uuid4(), preset_id=uuid.uuid4())
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agent_preset_version_ref_activity(
        ResolveAgentPresetVersionRefActivityInput(
            role=role,
            preset_slug="triage-agent",
            preset_version=3,
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(
        slug="triage-agent",
    )
    assert result.preset_id == version.preset_id
    assert result.preset_version_id == version.id


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        TracecatNotFoundError("Agent preset not found"),
        TracecatValidationError("Preset version does not belong to preset"),
        ScopeDeniedError(required_scopes=["agent:read"], missing_scopes=["agent:read"]),
        TracecatAuthorizationError("Synthetic model access denial"),
    ],
)
async def test_resolve_agent_preset_config_classifies_user_input_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    service = SimpleNamespace(
        with_preset_config=lambda **_: _FailingAsyncContext(error)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agent_preset_config_activity(
            ResolveAgentPresetConfigActivityInput(
                role=role,
                preset_slug="missing-preset",
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scopes",
    [
        pytest.param(None, id="unresolved"),
        pytest.param(frozenset(), id="empty"),
        pytest.param(frozenset({"agent:execute"}), id="execute-only"),
        pytest.param(frozenset({"agent:read"}), id="missing-secret-read"),
        pytest.param(frozenset({"agent:read", "org:secret:read"}), id="authorized"),
    ],
)
async def test_preset_preparation_enforces_serialized_role_scopes(
    monkeypatch: pytest.MonkeyPatch,
    scopes: frozenset[str] | None,
) -> None:
    """Exercise the real catalog/provider credential guard across the wire."""
    monkeypatch.setattr(
        "tracecat.config.TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        scopes=scopes,
    )
    catalog_id = uuid.uuid4()
    config = AgentConfig(
        model_name="test-model", model_provider="openai", catalog_id=catalog_id
    )
    session = Mock(spec=AsyncSession)
    session.execute = AsyncMock(
        return_value=Mock(
            scalar_one_or_none=Mock(
                return_value=SimpleNamespace(
                    custom_provider_id=None,
                    model_provider="openai",
                    encrypted_config=None,
                )
            )
        )
    )
    converter = get_data_converter()
    args = ResolveAgentPresetConfigActivityInput(role=role, preset_slug="test-agent")
    (restored_args,) = await converter.decode(
        await converter.encode([args]), [ResolveAgentPresetConfigActivityInput]
    )
    assert restored_args.role.scopes == scopes
    service = AgentManagementService(session, restored_args.role)
    assert service.presets is not None
    monkeypatch.setattr(
        service.presets, "resolve_agent_preset_config", AsyncMock(return_value=config)
    )
    monkeypatch.setattr(
        "tracecat.agent.service.AgentModelAccessService.is_catalog_enabled",
        AsyncMock(return_value=True),
    )
    secret_lookup = AsyncMock(return_value=SimpleNamespace(encrypted_keys=b"synthetic"))
    monkeypatch.setattr(
        service.secrets_service, "_get_org_secret_by_name", secret_lookup
    )
    # Only storage/decryption is stubbed; credential loading and scope checks run.
    monkeypatch.setattr(
        service.secrets_service,
        "decrypt_keys",
        Mock(
            return_value=[
                SecretKeyValue(
                    key="OPENAI_API_KEY",
                    value=SecretStr("synthetic-key"),
                )
            ]
        ),
    )
    monkeypatch.setattr(
        AgentManagementService, "with_session", lambda **_: _AsyncContext(service)
    )

    if scopes == frozenset({"agent:read", "org:secret:read"}):
        result = await resolve_agent_preset_config_activity(restored_args)
        assert result.catalog_id == catalog_id
        assert result.model_name == "test-model"
        secret_lookup.assert_awaited_once()
        assert "synthetic-key" not in result.model_dump_json()
        return

    with pytest.raises(ApplicationError) as denied:
        await resolve_agent_preset_config_activity(restored_args)
    secret_lookup.assert_not_awaited()
    classification = extract_error_classification(denied.value)
    assert classification is not None
    assert classification.cause_type == "ScopeDeniedError"
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE

    # Use Temporal's actual failure converter, then the durable workflow's
    # classification and terminal wrapping paths, rather than matching text.
    failure = Failure()
    await converter.encode_failure(denied.value, failure)
    restored = await converter.decode_failure(failure)
    assert isinstance(restored, ApplicationError)
    assert restored.non_retryable
    activity_error = ActivityError(
        "Activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test-worker",
        activity_type="resolve_agent_preset_config_activity",
        activity_id="test-activity",
        retry_state=None,
    )
    activity_error.__cause__ = restored
    assert _agent_activity_classification(activity_error) == classification
    with pytest.raises(ApplicationError) as terminal:
        raise_wrapped_application_error(
            activity_error,
            fallback_classification=classification,
            include_implicit_context=False,
        )
    await converter.encode_failure(terminal.value, failure)
    restored_terminal = await converter.decode_failure(failure)
    assert isinstance(restored_terminal, ApplicationError)
    assert restored_terminal.non_retryable
    assert extract_error_classification(restored_terminal) == classification


@pytest.mark.anyio
@pytest.mark.parametrize("subagents", [False, True])
@pytest.mark.parametrize(
    "error",
    [
        ScopeDeniedError(required_scopes=["agent:read"], missing_scopes=["agent:read"]),
        TracecatAuthorizationError("Synthetic private resource"),
        ConnectionError("Synthetic database unavailable"),
    ],
)
async def test_preparation_boundary_distinguishes_denials_from_platform_errors(
    monkeypatch: pytest.MonkeyPatch,
    subagents: bool,
    error: Exception,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
    )
    service_class = AgentPresetService if subagents else AgentManagementService
    monkeypatch.setattr(
        service_class, "with_session", lambda **_: _FailingAsyncContext(error)
    )
    with pytest.raises(Exception) as raised:
        if subagents:
            await resolve_agents_config_activity(
                ResolveAgentsConfigActivityInput(role=role)
            )
        else:
            await resolve_agent_preset_config_activity(
                ResolveAgentPresetConfigActivityInput(
                    role=role, preset_slug="test-agent"
                )
            )

    failure = Failure()
    converter = get_data_converter()
    await converter.encode_failure(raised.value, failure)
    restored = await converter.decode_failure(failure)
    activity_error = ActivityError(
        "Activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test-worker",
        activity_type="test-preparation",
        activity_id="test-activity",
        retry_state=None,
    )
    activity_error.__cause__ = restored
    classification = _agent_activity_classification(activity_error)
    if isinstance(error, TracecatAuthorizationError):
        assert isinstance(restored, ApplicationError)
        assert restored.non_retryable
        assert classification.owner is RuntimeErrorOwner.USER
        assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
        assert classification.retry_disposition is RetryDisposition.NON_RETRYABLE
        assert str(error).encode() not in failure.SerializeToString()
    else:
        assert raised.value is error
        assert classification.owner is RuntimeErrorOwner.PLATFORM
        assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED
        assert classification.retry_disposition is RetryDisposition.RETRYABLE


def test_resolve_agents_config_result_derives_session_binding() -> None:
    binding = ResolvedAttachedSubagentRef(
        preset="analyst",
        preset_version=3,
        name=None,
        description=None,
        max_turns=5,
        preset_id=uuid.uuid4(),
        preset_version_id=uuid.uuid4(),
    )
    result = ResolvedAgentsRuntimeConfig(
        subagents=[
            ResolvedSubagentConfig(
                binding=binding,
                description="Runtime fallback description",
                prompt="Subagent prompt",
                config=AgentConfigPayload(
                    model_name="gpt-4o-mini",
                    model_provider="openai",
                    retries=3,
                ),
            )
        ],
    )

    assert result.subagents[0].alias == "analyst"
    assert result.subagents[0].max_turns == 5
    agents_binding = result.to_agents_binding()
    assert agents_binding.subagents == [binding]


@pytest.mark.anyio
async def test_resolve_preset_subagent_allows_no_attached_children() -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = AgentPresetService(cast(Any, SimpleNamespace()), role)
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=8,
        agents={"subagents": []},
        tool_approvals={},
    )
    service.resolve_agent_preset_version = AsyncMock(return_value=version)
    service.resolve_preset_tool_policy = AsyncMock(
        return_value=SimpleNamespace(tool_approvals=version.tool_approvals)
    )
    service._lock_active_subagent_presets = AsyncMock()  # type: ignore[method-assign]

    result = await service._resolve_preset_subagent_configs(
        AgentSubagentsConfig(
            subagents=[
                ResolvedAttachedSubagentRef(
                    preset="old-analyst-slug",
                    preset_version=2,
                    name="analyst",
                    description=None,
                    max_turns=3,
                    preset_id=preset_id,
                    preset_version_id=preset_version_id,
                )
            ],
        ),
        parent_preset_id=uuid.uuid4(),
        parent_slug="parent",
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(
        preset_id=preset_id,
        include_deleted=True,
    )
    assert result["subagents"][0]["preset_version_id"] == str(preset_version_id)
    assert result["subagents"][0]["preset_version"] == 8


@pytest.mark.anyio
async def test_resolve_agents_config_follows_current_preset_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preset_id = uuid.uuid4()
    attached_version_id = uuid.uuid4()
    current_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=current_version_id,
        preset_id=preset_id,
        version=4,
        agents={},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_preset_tool_policy=AsyncMock(
            return_value=SimpleNamespace(tool_approvals=version.tool_approvals)
        ),
        get_preset=AsyncMock(return_value=SimpleNamespace(description="Child preset")),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(
                subagents=[
                    ResolvedAttachedSubagentRef(
                        preset="old-analyst-slug",
                        preset_version=2,
                        name="analyst",
                        description=None,
                        max_turns=None,
                        preset_id=preset_id,
                        preset_version_id=attached_version_id,
                    )
                ],
            ),
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(
        preset_id=preset_id,
        include_deleted=True,
    )
    service.resolve_agent_preset_config.assert_awaited_once_with(
        preset_version_id=current_version_id,
        resolve_dependencies_from_heads=True,
        include_deleted=True,
    )
    assert result.subagents[0].binding.preset_version_id == current_version_id
    assert result.subagents[0].binding.preset_version == 4


@pytest.mark.anyio
async def test_resolve_agents_config_explicitly_disables_latest_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preset_id = uuid.uuid4()
    preset_version_id = uuid.uuid4()
    version = SimpleNamespace(
        id=preset_version_id,
        preset_id=preset_id,
        version=4,
        agents={},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_preset_tool_policy=AsyncMock(
            return_value=SimpleNamespace(tool_approvals=version.tool_approvals)
        ),
        get_preset=AsyncMock(return_value=SimpleNamespace(description="Child preset")),
        resolve_agent_preset_config=AsyncMock(
            return_value=AgentConfig(
                model_name="gpt-4o-mini",
                model_provider="openai",
                retries=3,
            )
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    result = await resolve_agents_config_activity(
        ResolveAgentsConfigActivityInput(
            role=role,
            agents=AgentSubagentsConfig(
                subagents=[
                    ResolvedAttachedSubagentRef(
                        preset="old-analyst-slug",
                        preset_version=2,
                        name="analyst",
                        preset_id=preset_id,
                        preset_version_id=preset_version_id,
                    )
                ],
            ),
            follow_latest_versions=False,
        )
    )

    service.resolve_agent_preset_version.assert_awaited_once_with(
        preset_version_id=preset_version_id,
        include_deleted=True,
    )
    assert result.subagents[0].binding.preset_version_id == preset_version_id


@pytest.mark.anyio
async def test_resolve_agents_config_classifies_missing_subagent_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(
            side_effect=TracecatNotFoundError("Agent preset 'missing-child' not found")
        ),
        use_latest_resource_versions=AsyncMock(return_value=False),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "missing-child"}],
                    }
                ),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_resolve_agents_config_rejects_subagent_with_tool_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        preset_id=uuid.uuid4(),
        version=1,
        agents={},
        tool_approvals={"core.http_request": True},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_preset_tool_policy=AsyncMock(
            return_value=SimpleNamespace(tool_approvals=version.tool_approvals)
        ),
        use_latest_resource_versions=AsyncMock(return_value=False),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "subagents": [{"preset": "approval-child"}],
                    }
                ),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_resolve_agents_config_classifies_malformed_persisted_agents_as_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(
        id=uuid.uuid4(),
        preset_id=uuid.uuid4(),
        version=1,
        agents={"enabled": True, "subagents": {}},
        tool_approvals={},
    )
    service = SimpleNamespace(
        resolve_agent_preset_version=AsyncMock(return_value=version),
        resolve_preset_tool_policy=AsyncMock(
            return_value=SimpleNamespace(tool_approvals=version.tool_approvals)
        ),
        use_latest_resource_versions=AsyncMock(return_value=False),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "enabled": True,
                        "subagents": [{"preset": "malformed-child"}],
                    }
                ),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.PLATFORM
    assert classification.kind is RuntimeErrorKind.AGENT_PREPARATION_FAILED
    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_resolve_agents_config_rejects_invalid_fallback_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentPresetService.with_session",
        lambda **_: _AsyncContext(
            SimpleNamespace(use_latest_resource_versions=AsyncMock(return_value=False))
        ),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_agents_config_activity(
            ResolveAgentsConfigActivityInput(
                role=role,
                agents=AgentSubagentsConfig.model_validate(
                    {
                        "subagents": [{"preset": "Bad Alias"}],
                    }
                ),
            )
        )

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True


@pytest.mark.anyio
async def test_resolve_custom_model_provider_config_activity_returns_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        get_workspace_provider_credentials=AsyncMock(
            return_value={
                "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://customer.example",
                "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "provider/custom-model",
                "CUSTOM_MODEL_PROVIDER_PASSTHROUGH": "true",
            }
        )
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )

    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda *_args, **_kwargs: _AsyncContext(service),
    )

    result = await resolve_custom_model_provider_config_activity(role)

    service.get_workspace_provider_credentials.assert_awaited_once_with(
        "custom-model-provider",
    )
    assert result.base_url == "https://customer.example"
    assert result.model_name == "provider/custom-model"
    assert result.passthrough is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "credentials",
    [None, {"CUSTOM_MODEL_PROVIDER_API_KEY": "opaque-secret"}],
)
async def test_resolve_custom_model_provider_config_classifies_invalid_config(
    monkeypatch: pytest.MonkeyPatch,
    credentials: dict[str, str] | None,
) -> None:
    service = SimpleNamespace(
        get_workspace_provider_credentials=AsyncMock(return_value=credentials)
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda *_args, **_kwargs: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_custom_model_provider_config_activity(role)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.message == "Agent configuration is invalid"
    assert "opaque-secret" not in str(exc_info.value)


@pytest.mark.anyio
async def test_resolve_custom_model_provider_config_classifies_revoked_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_id = uuid.uuid4()
    service = SimpleNamespace(
        session=SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalar_one_or_none=lambda: SimpleNamespace(
                        custom_provider_id=uuid.uuid4()
                    )
                )
            )
        ),
        organization_id=uuid.uuid4(),
        get_catalog_credentials=AsyncMock(
            side_effect=TracecatAuthorizationError("catalog access revoked")
        ),
    )
    role = Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "tracecat.agent.preset.activities.AgentManagementService.with_session",
        lambda *_args, **_kwargs: _AsyncContext(service),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await resolve_custom_model_provider_config_activity(role, catalog_id)

    classification = extract_error_classification(exc_info.value)
    assert classification is not None
    assert classification.owner is RuntimeErrorOwner.USER
    assert classification.kind is RuntimeErrorKind.AGENT_CONFIGURATION_INVALID
    assert exc_info.value.non_retryable is True
    assert "catalog access revoked" not in str(exc_info.value)
