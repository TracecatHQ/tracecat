import logging
import uuid
from contextlib import asynccontextmanager
from typing import cast

import pytest
from litellm.caching.dual_cache import DualCache
from litellm.proxy._types import ProxyException, UserAPIKeyAuth
from starlette.requests import Request

from tracecat.agent.gateway import (
    TracecatCallbackHandler,
    _hook_request_counters,
    _inject_provider_credentials,
    _sanitize_exception_message,
    get_runtime_credentials,
    user_api_key_auth,
)
from tracecat.agent.litellm_observability import LiteLLMLoadTracker
from tracecat.agent.tokens import verify_llm_token
from tracecat.agent.types import AgentConfig


def test_custom_source_injects_dummy_api_key_and_openai_prefix():
    data = {"model": "gpt-5"}

    _inject_provider_credentials(
        data,
        "openai_compatible_gateway",
        {},
        source_id="00000000-0000-0000-0000-000000000001",
    )

    assert data["api_key"] == "not-needed"
    assert data["model"] == "openai/gpt-5"


def test_custom_source_preserves_custom_header_and_api_version():
    data = {"model": "gpt-5"}

    _inject_provider_credentials(
        data,
        "openai_compatible_gateway",
        {
            "TRACECAT_SOURCE_API_KEY": "source-key",
            "TRACECAT_SOURCE_API_KEY_HEADER": "X-Api-Key",
            "TRACECAT_SOURCE_API_VERSION": "2024-06-01",
            "TRACECAT_SOURCE_BASE_URL": "https://gateway.example/v1",
        },
        source_id="00000000-0000-0000-0000-000000000001",
    )

    assert "api_key" not in data
    assert data["api_base"] == "https://gateway.example/v1"
    assert data["api_version"] == "2024-06-01"
    assert data["extra_headers"] == {"X-Api-Key": "source-key"}
    assert data["model"] == "openai/gpt-5"


def test_manual_custom_source_uses_declared_provider_routing():
    data = {"model": "claude-3-7-sonnet"}

    _inject_provider_credentials(
        data,
        "anthropic",
        {
            "TRACECAT_SOURCE_API_KEY": "source-key",
            "TRACECAT_SOURCE_API_KEY_HEADER": "X-Api-Key",
            "TRACECAT_SOURCE_BASE_URL": "https://anthropic.gateway.example",
            "ANTHROPIC_API_KEY": "source-key",
            "ANTHROPIC_BASE_URL": "https://anthropic.gateway.example",
        },
        source_id="00000000-0000-0000-0000-000000000001",
    )

    assert "api_key" not in data
    assert data["api_base"] == "https://anthropic.gateway.example"
    assert data["extra_headers"] == {"X-Api-Key": "source-key"}
    assert data["model"] == "anthropic/claude-3-7-sonnet"


def test_source_backed_openai_uses_source_endpoint_without_vendor_key():
    data = {"model": "gpt-4o-mini"}

    _inject_provider_credentials(
        data,
        "openai",
        {
            "TRACECAT_SOURCE_BASE_URL": "http://localhost:4000/v1",
        },
        source_id="00000000-0000-0000-0000-000000000001",
    )

    assert data["api_key"] == "not-needed"
    assert data["api_base"] == "http://localhost:4000/v1"
    assert data["model"] == "openai/gpt-4o-mini"


def test_source_backed_anthropic_uses_source_endpoint_without_vendor_key():
    data = {"model": "claude-3-7-sonnet"}

    _inject_provider_credentials(
        data,
        "anthropic",
        {
            "TRACECAT_SOURCE_BASE_URL": "http://localhost:4000/v1",
        },
        source_id="00000000-0000-0000-0000-000000000001",
    )

    assert data["api_key"] == "not-needed"
    assert data["api_base"] == "http://localhost:4000/v1"
    assert data["model"] == "anthropic/claude-3-7-sonnet"


def test_source_backed_gemini_uses_source_endpoint_without_vendor_key():
    data = {"model": "gemini-2.5-flash"}

    _inject_provider_credentials(
        data,
        "gemini",
        {
            "TRACECAT_SOURCE_BASE_URL": "http://localhost:4000/v1",
        },
        source_id="00000000-0000-0000-0000-000000000001",
    )

    assert data["api_key"] == "not-needed"
    assert data["api_base"] == "http://localhost:4000/v1"
    assert data["model"] == "gemini/gemini-2.5-flash"


def test_gemini_injects_api_key_and_prefixes_model():
    data = {"model": "gemini-2.5-flash"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds, source_id=None)

    assert data["api_key"] == "test-gemini-key"
    assert data["model"] == "gemini/gemini-2.5-flash"


def test_gemini_does_not_double_prefix_model():
    data = {"model": "gemini/gemini-3-flash-preview"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds, source_id=None)

    assert data["model"] == "gemini/gemini-3-flash-preview"


def test_openai_injects_optional_base_url():
    data = {"model": "gpt-5"}
    creds = {
        "OPENAI_API_KEY": "test-openai-key",
        "OPENAI_BASE_URL": "https://api.openai.eu/v1",
    }

    _inject_provider_credentials(data, "openai", creds, source_id=None)

    assert data["api_key"] == "test-openai-key"
    assert data["api_base"] == "https://api.openai.eu/v1"
    assert data["model"] == "openai/gpt-5"


def test_openai_does_not_double_prefix_model():
    data = {"model": "openai/gpt-5"}
    creds = {
        "OPENAI_API_KEY": "test-openai-key",
    }

    _inject_provider_credentials(data, "openai", creds, source_id=None)

    assert data["model"] == "openai/gpt-5"


def test_anthropic_injects_optional_base_url():
    data = {"model": "claude-sonnet-4-5-20250929"}
    creds = {
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "ANTHROPIC_BASE_URL": "https://api.eu-west-1.anthropic.com",
    }

    _inject_provider_credentials(data, "anthropic", creds, source_id=None)

    assert data["api_key"] == "test-anthropic-key"
    assert data["api_base"] == "https://api.eu-west-1.anthropic.com"
    assert data["model"] == "anthropic/claude-sonnet-4-5-20250929"


def test_vertex_ai_injects_project_credentials_and_model():
    data = {"model": "gemini-2.5-flash"}
    creds = {
        "GOOGLE_API_CREDENTIALS": '{"type":"service_account"}',
        "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }

    _inject_provider_credentials(data, "vertex_ai", creds, source_id=None)

    assert data["vertex_credentials"] == '{"type":"service_account"}'
    assert data["vertex_project"] == "my-gcp-project"
    assert data["vertex_location"] == "us-central1"
    assert data["model"] == "vertex_ai/gemini-2.5-flash"


def test_vertex_ai_requires_credentials_and_project():
    data = {"model": "gemini-2.5-flash"}
    creds = {
        "GOOGLE_API_CREDENTIALS": '{"type":"service_account"}',
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "vertex_ai", creds, source_id=None)


def test_bedrock_uses_selected_model_when_no_provider_override_is_configured():
    data = {"model": "anthropic.claude-3-haiku-20240307-v1:0"}
    creds = {
        "AWS_REGION": "us-east-1",
    }

    _inject_provider_credentials(data, "bedrock", creds, source_id=None)

    assert data["model"] == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"


def test_azure_openai_uses_selected_model_as_deployment_name_by_default():
    data = {"model": "gpt-4o"}
    creds = {
        "AZURE_API_BASE": "https://example.openai.azure.com",
        "AZURE_API_VERSION": "2024-02-15-preview",
        "AZURE_API_KEY": "azure-key",
    }

    _inject_provider_credentials(data, "azure_openai", creds, source_id=None)

    assert data["model"] == "azure/gpt-4o"


def test_azure_openai_does_not_double_prefix_selected_deployment_name():
    data = {"model": "azure/eu/gpt-5.1"}
    creds = {
        "AZURE_API_BASE": "https://example.openai.azure.com",
        "AZURE_API_VERSION": "2024-02-15-preview",
        "AZURE_API_KEY": "azure-key",
    }

    _inject_provider_credentials(data, "azure_openai", creds, source_id=None)

    assert data["model"] == "azure/eu/gpt-5.1"


def test_azure_ai_uses_selected_model_by_default():
    data = {"model": "claude-sonnet-4-5"}
    creds = {
        "AZURE_API_BASE": "https://example.services.ai.azure.com/anthropic",
        "AZURE_API_KEY": "azure-ai-key",
    }

    _inject_provider_credentials(data, "azure_ai", creds, source_id=None)

    assert data["model"] == "azure_ai/claude-sonnet-4-5"


def test_bedrock_falls_back_to_ambient_iam_role_when_static_keys_missing():
    data = {"model": "bedrock"}
    creds = {
        "AWS_REGION": "us-east-1",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    _inject_provider_credentials(data, "bedrock", creds, source_id=None)

    assert "api_key" not in data
    assert "aws_access_key_id" not in data
    assert "aws_secret_access_key" not in data
    assert data["aws_region_name"] == "us-east-1"
    assert data["model"] == "bedrock/us.anthropic.claude-sonnet-4-20250514-v1:0"


def test_bedrock_uses_static_keys_when_configured():
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_SESSION_TOKEN": "session-token",
        "AWS_REGION": "us-west-2",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
    }

    _inject_provider_credentials(data, "bedrock", creds, source_id=None)

    assert data["aws_access_key_id"] == "AKIA123"
    assert data["aws_secret_access_key"] == "secret"
    assert data["aws_session_token"] == "session-token"
    assert data["aws_region_name"] == "us-west-2"
    assert data["model"] == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"


def test_bedrock_rejects_partial_static_keys():
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "bedrock", creds, source_id=None)


def test_bedrock_rejects_session_token_without_static_keys():
    data = {"model": "bedrock"}
    creds = {
        "AWS_SESSION_TOKEN": "session-token",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "bedrock", creds, source_id=None)


@pytest.mark.anyio
async def test_get_runtime_credentials_uses_config_path_for_legacy_presets(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    class FakeService:
        async def get_runtime_credentials_for_config(
            self, config: AgentConfig
        ) -> dict[str, str]:
            captured["config"] = config
            return {"OPENAI_API_KEY": "workspace-key"}

    @asynccontextmanager
    async def fake_with_session(*, role=None, session=None):
        del role, session
        yield FakeService()

    monkeypatch.setattr(
        "tracecat.agent.gateway.AgentManagementService.with_session",
        fake_with_session,
    )

    credentials = await get_runtime_credentials(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        model_name="gpt-4o-mini",
        provider="openai",
        source_id=None,
    )

    assert credentials == {"OPENAI_API_KEY": "workspace-key"}
    assert captured["config"] == AgentConfig(
        source_id=None,
        model_provider="openai",
        model_name="gpt-4o-mini",
    )


@pytest.mark.anyio
async def test_pre_call_hook_uses_preset_base_url_over_openai_credential_base_url(
    monkeypatch: pytest.MonkeyPatch,
):
    async def mock_get_runtime_credentials(**_: object) -> dict[str, str]:
        return {
            "OPENAI_API_KEY": "test-openai-key",
            "OPENAI_BASE_URL": "https://creds.openai.example/v1",
        }

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_runtime_credentials",
        mock_get_runtime_credentials,
    )

    user_api_key_dict = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "model": "gpt-5",
            "provider": "openai",
            "base_url": "https://preset.openai.example/v1",
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=DualCache(),
        data={},
        call_type="completion",
    )

    assert result["api_key"] == "test-openai-key"
    assert result["api_base"] == "https://preset.openai.example/v1"
    assert result["model"] == "openai/gpt-5"


@pytest.mark.anyio
async def test_pre_call_hook_allows_custom_source_without_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    async def mock_get_runtime_credentials(**_: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_runtime_credentials",
        mock_get_runtime_credentials,
    )

    user_api_key_dict = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "model": "gpt-5",
            "provider": "openai_compatible_gateway",
            "source_id": "00000000-0000-0000-0000-000000000001",
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={},
        call_type="completion",
    )

    assert result["api_key"] == "not-needed"
    assert result["model"] == "openai/gpt-5"


@pytest.mark.anyio
async def test_pre_call_hook_uses_preset_base_url_over_custom_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    async def mock_get_runtime_credentials(**_: object) -> dict[str, str]:
        return {
            "CUSTOM_MODEL_PROVIDER_API_KEY": "test-custom-key",
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://creds.custom.example/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "qwen2.5-coder",
        }

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_runtime_credentials",
        mock_get_runtime_credentials,
    )

    user_api_key_dict = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "model": "custom",
            "provider": "custom-model-provider",
            "base_url": "https://preset.custom.example/v1",
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=DualCache(),
        data={},
        call_type="completion",
    )

    assert result["api_key"] == "test-custom-key"
    assert result["api_base"] == "https://preset.custom.example/v1"


def _make_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("127.0.0.1", 4000),
            "client": ("127.0.0.1", 12345),
        }
    )


@pytest.mark.anyio
async def test_user_api_key_auth_allows_health_readiness_without_token() -> None:
    auth = await user_api_key_auth(_make_request("/health/readiness"), api_key=None)

    assert auth.api_key == "health-probe"
    assert auth.user_role == "internal_user_viewer"


@pytest.mark.anyio
async def test_user_api_key_auth_preserves_trace_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/chat/completions",
            "headers": [
                (b"authorization", b"Bearer test-token"),
                (b"x-request-id", b"trace-123"),
            ],
            "query_string": b"",
            "scheme": "http",
            "server": ("127.0.0.1", 4000),
            "client": ("127.0.0.1", 12345),
        }
    )

    class _Claims:
        session_id = "00000000-0000-0000-0000-000000000003"
        workspace_id = "00000000-0000-0000-0000-000000000001"
        organization_id = "00000000-0000-0000-0000-000000000002"
        provider = "openai"
        model = "gpt-5"
        source_id = None
        base_url = None
        model_settings: dict[str, object] = {}

    monkeypatch.setattr("tracecat.agent.gateway.verify_llm_token", lambda _: _Claims())

    auth = await user_api_key_auth(request, api_key="test-token")

    assert auth.metadata["trace_request_id"] == "trace-123"


def test_verify_llm_token_rejects_invalid_token_type() -> None:
    with pytest.raises(ValueError, match="Invalid LLM token"):
        verify_llm_token("")


def test_sanitize_exception_message_redacts_api_key_and_bearer_token() -> None:
    sanitized = _sanitize_exception_message(
        RuntimeError(
            "Incorrect API key provided: sk-test123 Authorization: Bearer secret-token"
        )
    )

    assert "sk-test123" not in sanitized
    assert "secret-token" not in sanitized
    assert "[REDACTED]" in sanitized


@pytest.mark.anyio
async def test_pre_call_hook_tracks_active_gateway_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = LiteLLMLoadTracker()

    async def mock_get_runtime_credentials(**_: object) -> dict[str, str]:
        return {"OPENAI_API_KEY": "test-openai-key"}

    monkeypatch.setattr("tracecat.agent.gateway._gateway_load_tracker", tracker)
    monkeypatch.setattr(
        "tracecat.agent.gateway.get_runtime_credentials",
        mock_get_runtime_credentials,
    )

    user_api_key_dict = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "session_id": "00000000-0000-0000-0000-000000000003",
            "model": "gpt-5",
            "provider": "openai",
            "base_url": None,
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    request_data = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=DualCache(),
        data={},
        call_type="completion",
    )

    assert tracker.snapshot().active_requests == 1
    assert "_tracecat_hook_request_id" not in request_data
    assert _hook_request_counters[id(request_data)] == 1

    await handler.async_post_call_success_hook(
        request_data,
        user_api_key_dict,
        response=object(),
    )

    assert tracker.snapshot().active_requests == 0
    assert id(request_data) not in _hook_request_counters


@pytest.mark.anyio
async def test_failure_hook_does_not_require_internal_request_id_in_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = LiteLLMLoadTracker()

    async def mock_get_runtime_credentials(**_: object) -> dict[str, str]:
        return {"OPENAI_API_KEY": "test-openai-key"}

    monkeypatch.setattr("tracecat.agent.gateway._gateway_load_tracker", tracker)
    monkeypatch.setattr(
        "tracecat.agent.gateway.get_runtime_credentials",
        mock_get_runtime_credentials,
    )

    user_api_key_dict = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "session_id": "00000000-0000-0000-0000-000000000003",
            "model": "gpt-5",
            "provider": "openai",
            "base_url": None,
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    request_data = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=DualCache(),
        data={},
        call_type="completion",
    )

    assert "_tracecat_hook_request_id" not in request_data

    await handler.async_post_call_failure_hook(
        request_data=request_data,
        original_exception=RuntimeError("boom"),
        user_api_key_dict=user_api_key_dict,
    )

    assert tracker.snapshot().active_requests == 0
    assert id(request_data) not in _hook_request_counters


@pytest.mark.anyio
async def test_failure_hook_logs_sanitized_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = LiteLLMLoadTracker()

    async def mock_get_runtime_credentials(**_: object) -> dict[str, str]:
        return {"OPENAI_API_KEY": "test-openai-key"}

    monkeypatch.setattr("tracecat.agent.gateway._gateway_load_tracker", tracker)
    monkeypatch.setattr(
        "tracecat.agent.gateway.get_runtime_credentials",
        mock_get_runtime_credentials,
    )

    user_api_key_dict = UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "organization_id": "00000000-0000-0000-0000-000000000002",
            "session_id": "00000000-0000-0000-0000-000000000003",
            "model": "gpt-5",
            "provider": "openai",
            "base_url": None,
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    request_data = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=DualCache(),
        data={},
        call_type="completion",
    )

    with caplog.at_level(logging.ERROR, logger="tracecat.agent.gateway"):
        await handler.async_post_call_failure_hook(
            request_data=request_data,
            original_exception=RuntimeError(
                "Incorrect API key provided: sk-test123 Authorization: Bearer secret-token"
            ),
            user_api_key_dict=user_api_key_dict,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert messages
    error_message = messages[-1]
    assert "sk-test123" not in error_message
    assert "secret-token" not in error_message
    assert "[REDACTED]" in error_message
