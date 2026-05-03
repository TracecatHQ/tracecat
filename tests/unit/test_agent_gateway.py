from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from litellm.caching.dual_cache import DualCache
from litellm.proxy._types import ProxyException, UserAPIKeyAuth
from litellm.router import Router
from starlette.requests import Request

from tracecat.agent.gateway import (
    TracecatCallbackHandler,
    _filter_allowed_model_settings,
    _inject_provider_credentials,
    _resolve_bedrock_runtime_credentials,
    user_api_key_auth,
)
from tracecat.agent.tokens import verify_llm_token


def test_gemini_injects_api_key_and_prefixes_model() -> None:
    data = {"model": "gemini-2.5-flash"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds)

    assert data["api_key"] == "test-gemini-key"
    assert data["model"] == "gemini/gemini-2.5-flash"


def test_filter_allowed_model_settings_still_drops_thinking_for_openai() -> None:
    filtered = _filter_allowed_model_settings(
        {
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "temperature": 0.2,
        },
        provider="openai",
    )

    assert filtered["temperature"] == 0.2
    assert "thinking" not in filtered


def test_openai_injects_optional_base_url() -> None:
    data = {"model": "gpt-5"}
    creds = {
        "OPENAI_API_KEY": "test-openai-key",
        "OPENAI_BASE_URL": "https://api.openai.example/v1",
    }

    _inject_provider_credentials(data, "openai", creds)

    assert data["api_key"] == "test-openai-key"
    assert data["api_base"] == "https://api.openai.example/v1"


def test_azure_ai_does_not_require_api_version() -> None:
    data = {"model": "azure_ai"}
    creds = {
        "AZURE_API_BASE": "https://example.services.ai.azure.com/anthropic",
        "AZURE_API_KEY": "test-azure-ai-key",
        "AZURE_AI_MODEL_NAME": "claude-sonnet-4-5",
    }

    _inject_provider_credentials(data, "azure_ai", creds)

    assert data["api_key"] == "test-azure-ai-key"
    assert data["api_base"] == "https://example.services.ai.azure.com/anthropic"
    assert "api_version" not in data
    assert data["model"] == "azure_ai/claude-sonnet-4-5"


def test_azure_ai_injects_api_version_when_present() -> None:
    data = {"model": "azure_ai"}
    creds = {
        "AZURE_API_BASE": "https://example.services.ai.azure.com/anthropic",
        "AZURE_API_KEY": "test-azure-ai-key",
        "AZURE_API_VERSION": "2024-05-01-preview",
        "AZURE_AI_MODEL_NAME": "claude-sonnet-4-5",
    }

    _inject_provider_credentials(data, "azure_ai", creds)

    assert data["api_version"] == "2024-05-01-preview"
    assert data["model"] == "azure_ai/claude-sonnet-4-5"


def test_bedrock_uses_static_keys_when_configured() -> None:
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


@pytest.mark.anyio
async def test_bedrock_resolves_assumed_role_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def mock_assume_bedrock_role(
        role_arn: str,
        *,
        external_id: str,
        session_name: str,
    ) -> dict[str, str]:
        captured["role_arn"] = role_arn
        captured["external_id"] = external_id
        captured["session_name"] = session_name
        return {
            "AWS_ACCESS_KEY_ID": "ASIA456",
            "AWS_SECRET_ACCESS_KEY": "assumed-secret",
            "AWS_SESSION_TOKEN": "assumed-session-token",
        }

    monkeypatch.setattr(
        "tracecat.agent.gateway._assume_bedrock_role",
        mock_assume_bedrock_role,
    )

    resolved = await _resolve_bedrock_runtime_credentials(
        {
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
            "AWS_ROLE_SESSION_NAME": "custom-audit-session",
            "TRACECAT_AWS_EXTERNAL_ID": "ws-external-id",
            "AWS_REGION": "us-west-2",
            "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
            "AWS_BEARER_TOKEN_BEDROCK": "ignored-bearer-token",
        }
    )

    assert captured == {
        "role_arn": "arn:aws:iam::123456789012:role/customer-role",
        "external_id": "ws-external-id",
        "session_name": "custom-audit-session",
    }
    assert resolved["AWS_ACCESS_KEY_ID"] == "ASIA456"
    assert resolved["AWS_SECRET_ACCESS_KEY"] == "assumed-secret"
    assert resolved["AWS_SESSION_TOKEN"] == "assumed-session-token"
    assert resolved["AWS_BEARER_TOKEN_BEDROCK"] == "ignored-bearer-token"


@pytest.mark.anyio
async def test_bedrock_role_credentials_require_external_id() -> None:
    with pytest.raises(ProxyException) as exc_info:
        await _resolve_bedrock_runtime_credentials(
            {
                "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/customer-role",
                "AWS_REGION": "us-west-2",
                "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
            }
        )

    assert exc_info.value.code == "400"
    assert "workspace External ID" in exc_info.value.message


def test_bedrock_rejects_ambient_credential_fallback() -> None:
    data = {"model": "bedrock"}
    creds = {
        "AWS_REGION": "us-west-2",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
    }

    with pytest.raises(ProxyException) as exc_info:
        _inject_provider_credentials(data, "bedrock", creds)

    assert exc_info.value.code == "401"
    assert "resolved before request dispatch" in exc_info.value.message


def test_litellm_config_routes_provider_placeholders_before_catch_all() -> None:
    config_path = (
        Path(__file__).resolve().parents[2]
        / "tracecat"
        / "agent"
        / "litellm_config.yaml"
    )
    config = yaml.safe_load(config_path.read_text())
    router = Router(model_list=config["model_list"])

    def resolved_model(route_name: str) -> Any:
        route = router.get_model_list(model_name=route_name)
        assert route is not None
        litellm_params = route[0]["litellm_params"]
        assert litellm_params is not None
        model = litellm_params.get("model")
        assert model is not None
        return model

    # Provider wildcard routes resolve before the OpenAI catch-all
    assert resolved_model("bedrock/*") == "bedrock/*"
    assert resolved_model("vertex_ai/*") == "vertex_ai/*"
    assert resolved_model("azure/*") == "azure/*"
    assert resolved_model("azure_ai/*") == "azure_ai/*"
    # Unqualified names fall through to the hosted_vllm catch-all so custom
    # providers bridge to Chat Completions instead of the Responses API.
    assert resolved_model("custom") == "hosted_vllm/custom"


def _make_request(
    path: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers or [],
            "query_string": query_string,
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


def test_verify_llm_token_rejects_invalid_token_type() -> None:
    with pytest.raises(ValueError, match="Invalid LLM token"):
        verify_llm_token("")


@pytest.mark.anyio
async def test_user_api_key_auth_rejects_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.gateway.verify_llm_token",
        lambda _: (_ for _ in ()).throw(ValueError("bad token")),
    )

    with pytest.raises(ProxyException) as exc_info:
        await user_api_key_auth(
            request=_make_request("/v1/chat/completions"),
            api_key="bad-token",
        )
    assert exc_info.value.message == "Invalid or expired token"
    assert exc_info.value.code == "401"


@pytest.mark.anyio
async def test_user_api_key_auth_strips_anthropic_beta_metadata_for_non_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.gateway.verify_llm_token",
        lambda _: SimpleNamespace(
            workspace_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
            session_id="00000000-0000-0000-0000-000000000003",
            catalog_id=None,
            use_workspace_credentials=False,
            model="bedrock",
            provider="bedrock",
            base_url=None,
            model_settings={},
            routes={},
        ),
    )
    request = _make_request(
        "/v1/messages",
        headers=[(b"anthropic-beta", b"clear_thinking_20251015")],
        query_string=b"beta=true",
    )

    await user_api_key_auth(request, api_key="valid-token")

    assert request.headers.get("anthropic-beta") is None
    assert "beta" not in request.query_params


@pytest.mark.anyio
async def test_user_api_key_auth_preserves_anthropic_beta_metadata_for_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.gateway.verify_llm_token",
        lambda _: SimpleNamespace(
            workspace_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
            session_id="00000000-0000-0000-0000-000000000003",
            catalog_id=None,
            use_workspace_credentials=False,
            model="claude-sonnet-4",
            provider="anthropic",
            base_url=None,
            model_settings={},
            routes={},
        ),
    )
    request = _make_request(
        "/v1/messages",
        headers=[(b"anthropic-beta", b"clear_thinking_20251015")],
        query_string=b"beta=true",
    )

    await user_api_key_auth(request, api_key="valid-token")

    assert request.headers["anthropic-beta"] == "clear_thinking_20251015"
    assert request.query_params["beta"] == "true"


@pytest.mark.anyio
async def test_user_api_key_auth_preserves_legacy_workspace_credentials_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.gateway.verify_llm_token",
        lambda _: SimpleNamespace(
            workspace_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
            session_id="00000000-0000-0000-0000-000000000003",
            catalog_id=None,
            use_workspace_credentials=True,
            model="gpt-5",
            provider="openai",
            base_url=None,
            model_settings={},
        ),
    )

    auth = await user_api_key_auth(
        request=_make_request("/v1/chat/completions"),
        api_key="valid-token",
    )

    assert auth.metadata["use_workspace_credentials"] is True


@pytest.mark.anyio
async def test_pre_call_hook_filters_model_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, object] = {}

    async def mock_get_provider_credentials(**kwargs: object) -> dict[str, str]:
        captured_kwargs.update(kwargs)
        return {"OPENAI_API_KEY": "test-openai-key"}

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
            "model_settings": {
                "temperature": 0.2,
                "seed": 7,
                "api_key": "should-not-pass",
                "metadata": {"unsafe": True},
            },
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

    assert result["temperature"] == 0.2
    assert result["seed"] == 7
    assert "metadata" not in result
    assert result["api_key"] == "test-openai-key"
    assert captured_kwargs["use_workspace_credentials"] is True


@pytest.mark.anyio
async def test_pre_call_hook_uses_request_model_route_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_requests: list[dict[str, Any]] = []

    async def mock_get_provider_credentials(**kwargs: Any) -> dict[str, str]:
        credential_requests.append(kwargs)
        return {"OPENAI_API_KEY": "test-openai-key"}

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
            "model_settings": {"temperature": 0.8},
            "use_workspace_credentials": True,
            "routes": {
                "openai/gpt-5-mini": {
                    "model": "gpt-5-mini",
                    "provider": "openai",
                    "base_url": None,
                    "model_settings": {"temperature": 0.2},
                    "use_workspace_credentials": False,
                }
            },
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={"model": "openai/gpt-5-mini"},
        call_type="completion",
    )

    assert result["model"] == "openai/gpt-5-mini"
    assert result["temperature"] == 0.2
    assert result["api_key"] == "test-openai-key"
    assert credential_requests[0]["use_workspace_credentials"] is False


@pytest.mark.anyio
async def test_pre_call_hook_does_not_inject_reasoning_effort_without_model_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_get_provider_credentials(**_: object) -> dict[str, str]:
        return {"OPENAI_API_KEY": "test-openai-key"}

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

    assert "reasoning_effort" not in result


@pytest.mark.anyio
async def test_pre_call_hook_strips_anthropic_beta_payload_fields_for_non_anthropic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_get_provider_credentials(**_: object) -> dict[str, str]:
        return {"OPENAI_API_KEY": "test-openai-key"}

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
            "model_settings": {},
            "use_workspace_credentials": True,
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={
            "anthropic_beta": ["clear_thinking_20251015"],
            "context_management": {"clear_function_results": True},
            "output_config": {"task_budget": 2048},
            "output_format": {"type": "json_schema"},
        },
        call_type="completion",
    )

    assert "anthropic_beta" not in result
    assert "context_management" not in result
    assert "output_config" not in result
    assert "output_format" not in result
