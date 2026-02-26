from typing import cast

import pytest
from litellm.caching.dual_cache import DualCache
from litellm.proxy._types import ProxyException, UserAPIKeyAuth

from tracecat.agent.gateway import TracecatCallbackHandler, _inject_provider_credentials


def test_gemini_injects_api_key_and_prefixes_model():
    data = {"model": "gemini-2.5-flash"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds)

    assert data["api_key"] == "test-gemini-key"
    assert data["model"] == "gemini/gemini-2.5-flash"


def test_gemini_does_not_double_prefix_model():
    data = {"model": "gemini/gemini-3-flash-preview"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds)

    assert data["model"] == "gemini/gemini-3-flash-preview"


def test_openai_injects_optional_base_url():
    data = {"model": "gpt-5"}
    creds = {
        "OPENAI_API_KEY": "test-openai-key",
        "OPENAI_BASE_URL": "https://api.openai.eu/v1",
    }

    _inject_provider_credentials(data, "openai", creds)

    assert data["api_key"] == "test-openai-key"
    assert data["api_base"] == "https://api.openai.eu/v1"


def test_anthropic_injects_optional_base_url():
    data = {"model": "claude-sonnet-4-5-20250929"}
    creds = {
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "ANTHROPIC_BASE_URL": "https://api.eu-west-1.anthropic.com",
    }

    _inject_provider_credentials(data, "anthropic", creds)

    assert data["api_key"] == "test-anthropic-key"
    assert data["api_base"] == "https://api.eu-west-1.anthropic.com"


def test_vertex_ai_injects_project_credentials_and_model():
    data = {"model": "vertex_ai"}
    creds = {
        "GOOGLE_API_CREDENTIALS": '{"type":"service_account"}',
        "GOOGLE_CLOUD_PROJECT": "my-gcp-project",
        "VERTEX_AI_MODEL": "gemini-2.5-flash",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }

    _inject_provider_credentials(data, "vertex_ai", creds)

    assert data["vertex_credentials"] == '{"type":"service_account"}'
    assert data["vertex_project"] == "my-gcp-project"
    assert data["vertex_location"] == "us-central1"
    assert data["model"] == "vertex_ai/gemini-2.5-flash"


def test_vertex_ai_requires_credentials_project_and_model():
    data = {"model": "vertex_ai"}
    creds = {
        "GOOGLE_API_CREDENTIALS": '{"type":"service_account"}',
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "vertex_ai", creds)


def test_bedrock_falls_back_to_ambient_iam_role_when_static_keys_missing():
    data = {"model": "bedrock"}
    creds = {
        "AWS_REGION": "us-east-1",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    _inject_provider_credentials(data, "bedrock", creds)

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

    _inject_provider_credentials(data, "bedrock", creds)

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
        _inject_provider_credentials(data, "bedrock", creds)


def test_bedrock_rejects_session_token_without_static_keys():
    data = {"model": "bedrock"}
    creds = {
        "AWS_SESSION_TOKEN": "session-token",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    }

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "bedrock", creds)


@pytest.mark.anyio
async def test_pre_call_hook_uses_preset_base_url_over_openai_credential_base_url(
    monkeypatch: pytest.MonkeyPatch,
):
    async def mock_get_provider_credentials(**_: object) -> dict[str, str]:
        return {
            "OPENAI_API_KEY": "test-openai-key",
            "OPENAI_BASE_URL": "https://creds.openai.example/v1",
        }

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_provider_credentials",
        mock_get_provider_credentials,
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
            "use_workspace_credentials": True,
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={},
        call_type="completion",
    )

    assert result["api_key"] == "test-openai-key"
    assert result["api_base"] == "https://preset.openai.example/v1"


@pytest.mark.anyio
async def test_pre_call_hook_uses_preset_base_url_over_custom_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    async def mock_get_provider_credentials(**_: object) -> dict[str, str]:
        return {
            "CUSTOM_MODEL_PROVIDER_API_KEY": "test-custom-key",
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://creds.custom.example/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "qwen2.5-coder",
        }

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_provider_credentials",
        mock_get_provider_credentials,
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
            "use_workspace_credentials": True,
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={},
        call_type="completion",
    )

    assert result["api_key"] == "test-custom-key"
    assert result["api_base"] == "https://preset.custom.example/v1"
