import json
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
    _flatten_message_content,
    _inject_provider_credentials,
    _resolve_bedrock_runtime_credentials,
    _sanitize_ollama_messages,
    _sanitize_ollama_request,
    user_api_key_auth,
)
from tracecat.agent.tokens import verify_llm_token


def test_gemini_injects_api_key_and_prefixes_model() -> None:
    data = {"model": "gemini-2.5-flash"}
    creds = {"GEMINI_API_KEY": "test-gemini-key"}

    _inject_provider_credentials(data, "gemini", creds)

    assert data["api_key"] == "test-gemini-key"
    assert data["model"] == "gemini/gemini-2.5-flash"


def test_mistral_injects_api_key_and_prefixes_model() -> None:
    data = {"model": "mistral-large-latest"}
    creds = {"MISTRAL_API_KEY": "test-mistral-key"}

    _inject_provider_credentials(data, "mistral", creds)

    assert data["api_key"] == "test-mistral-key"
    assert data["model"] == "mistral/mistral-large-latest"


def test_mistral_injects_optional_base_url() -> None:
    data = {"model": "mistral-large-latest"}
    creds = {
        "MISTRAL_API_KEY": "test-mistral-key",
        "MISTRAL_BASE_URL": "https://api.mistral.example/v1",
    }

    _inject_provider_credentials(data, "mistral", creds)

    assert data["api_key"] == "test-mistral-key"
    assert data["api_base"] == "https://api.mistral.example/v1"


def test_mistral_missing_api_key_raises() -> None:
    data = {"model": "mistral-large-latest"}

    with pytest.raises(ProxyException):
        _inject_provider_credentials(data, "mistral", {})


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


def test_bedrock_uses_invoke_prefix_when_use_converse_absent() -> None:
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
    }
    _inject_provider_credentials(data, "bedrock", creds)
    assert data["model"] == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"


def test_bedrock_uses_converse_prefix_for_model_id_when_flag_true() -> None:
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
        "AWS_BEDROCK_USE_CONVERSE": "true",
    }
    _inject_provider_credentials(data, "bedrock", creds)
    assert data["model"] == "bedrock/converse/anthropic.claude-3-haiku-20240307-v1:0"


def test_bedrock_uses_converse_prefix_for_inference_profile_when_flag_true() -> None:
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_INFERENCE_PROFILE_ID": "us.anthropic.claude-3-haiku-20240307-v1:0",
        "AWS_BEDROCK_USE_CONVERSE": "true",
    }
    _inject_provider_credentials(data, "bedrock", creds)
    assert data["model"] == "bedrock/converse/us.anthropic.claude-3-haiku-20240307-v1:0"


def test_bedrock_false_flag_does_not_use_converse_prefix() -> None:
    data = {"model": "bedrock"}
    creds = {
        "AWS_ACCESS_KEY_ID": "AKIA123",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "AWS_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
        "AWS_BEDROCK_USE_CONVERSE": "false",
    }
    _inject_provider_credentials(data, "bedrock", creds)
    assert data["model"] == "bedrock/anthropic.claude-3-haiku-20240307-v1:0"


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
    assert resolved_model("mistral/*") == "mistral/*"
    # Unqualified names fall through to the hosted_vllm catch-all so custom
    # providers bridge to Chat Completions instead of the Responses API.
    assert resolved_model("custom") == "hosted_vllm/custom"


def _make_request(
    path: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
    json_body: dict[str, Any] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": "POST" if json_body is not None else "GET",
        "path": path,
        "headers": headers or [],
        "query_string": query_string,
        "scheme": "http",
        "server": ("127.0.0.1", 4000),
        "client": ("127.0.0.1", 12345),
    }
    if json_body is None:
        return Request(scope)

    body = json.dumps(json_body).encode()
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


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


@pytest.mark.parametrize(
    (
        "root_model",
        "root_provider",
        "route_key",
        "route_model",
        "route_provider",
        "preserves_metadata",
    ),
    [
        pytest.param(
            "gpt-5",
            "openai",
            "anthropic/claude-sonnet-4",
            "claude-sonnet-4",
            "anthropic",
            True,
            id="anthropic-route",
        ),
        pytest.param(
            "claude-sonnet-4",
            "anthropic",
            "openai/gpt-5",
            "gpt-5",
            "openai",
            False,
            id="non-anthropic-route",
        ),
    ],
)
@pytest.mark.anyio
async def test_user_api_key_auth_applies_anthropic_beta_metadata_by_route(
    monkeypatch: pytest.MonkeyPatch,
    root_model: str,
    root_provider: str,
    route_key: str,
    route_model: str,
    route_provider: str,
    preserves_metadata: bool,
) -> None:
    monkeypatch.setattr(
        "tracecat.agent.gateway.verify_llm_token",
        lambda _: SimpleNamespace(
            workspace_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
            session_id="00000000-0000-0000-0000-000000000003",
            catalog_id=None,
            use_workspace_credentials=False,
            model=root_model,
            provider=root_provider,
            base_url=None,
            model_settings={},
            routes={
                route_key: SimpleNamespace(
                    model=route_model,
                    provider=route_provider,
                    catalog_id=None,
                    base_url=None,
                    model_settings={},
                    use_workspace_credentials=False,
                )
            },
        ),
    )
    request = _make_request(
        "/v1/messages",
        headers=[(b"anthropic-beta", b"clear_thinking_20251015")],
        query_string=b"beta=true",
        json_body={"model": route_key},
    )

    await user_api_key_auth(request, api_key="valid-token")

    if preserves_metadata:
        assert request.headers["anthropic-beta"] == "clear_thinking_20251015"
        assert request.query_params["beta"] == "true"
    else:
        assert request.headers.get("anthropic-beta") is None
        assert "beta" not in request.query_params


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


# ---------------------------------------------------------------------------
# Ollama request sanitization
# ---------------------------------------------------------------------------


def test_flatten_message_content_joins_text_parts_and_drops_thinking() -> None:
    content = [
        {"type": "thinking", "thinking": "internal reasoning"},
        {"type": "text", "text": "Hello "},
        {"type": "redacted_thinking", "data": "xxxx"},
        {"type": "text", "text": "there!"},
    ]

    assert _flatten_message_content(content) == "Hello there!"


def test_flatten_message_content_passes_through_plain_string() -> None:
    assert _flatten_message_content("already a string") == "already a string"


def test_sanitize_ollama_messages_flattens_and_strips_reasoning_keys() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "the user greeted me"},
                {"type": "text", "text": "Hello there!"},
            ],
            "thinking_blocks": [{"type": "thinking", "thinking": "hmm"}],
            "reasoning_content": "hmm",
        },
        {"role": "user", "content": "say bye"},
    ]

    sanitized = _sanitize_ollama_messages(messages)

    assert sanitized[0] == {"role": "user", "content": "hi"}
    assert sanitized[1] == {"role": "assistant", "content": "Hello there!"}
    assert sanitized[2] == {"role": "user", "content": "say bye"}
    # Source structures are not mutated.
    assert isinstance(messages[1]["content"], list)
    assert "thinking_blocks" in messages[1]


def test_sanitize_ollama_messages_preserves_tool_calls() -> None:
    messages = [
        {"role": "user", "content": "what is 2+2"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "calc", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": [{"type": "text", "text": "4"}],
        },
    ]

    sanitized = _sanitize_ollama_messages(messages)

    assert sanitized[1]["tool_calls"] == messages[1]["tool_calls"]
    assert sanitized[1]["content"] == ""
    # tool message keeps its structure; list content is flattened.
    assert sanitized[2]["role"] == "tool"
    assert sanitized[2]["tool_call_id"] == "c1"
    assert sanitized[2]["content"] == "4"


def test_sanitize_ollama_request_drops_thinking_and_reasoning_effort() -> None:
    data = {
        "model": "qwen2.5",
        "api_base": "http://host:11434/v1",
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "reasoning_effort": {"effort": "high", "summary": "detailed"},
        "messages": [{"role": "user", "content": "hi"}],
    }

    _sanitize_ollama_request(data, {"CUSTOM_MODEL_PROVIDER_TYPE": "ollama"})

    assert "thinking" not in data
    assert "reasoning_effort" not in data
    # Route stays on the catch-all against the /v1 base URL.
    assert data["model"] == "qwen2.5"
    assert data["api_base"] == "http://host:11434/v1"


def test_sanitize_ollama_request_sanitizes_messages() -> None:
    data = {
        "model": "qwen2.5",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "x"},
                    {"type": "text", "text": "Hi"},
                ],
            }
        ],
    }

    _sanitize_ollama_request(data, {"CUSTOM_MODEL_PROVIDER_TYPE": "ollama"})

    assert data["messages"][0]["content"] == "Hi"


@pytest.mark.parametrize("provider_type", ["generic_openai_compatible", "litellm"])
def test_sanitize_ollama_request_noop_for_non_ollama(provider_type: str) -> None:
    data = {
        "model": "gpt-4o",
        "api_base": "https://gateway.example.com/v1",
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi"}],
            }
        ],
    }

    _sanitize_ollama_request(data, {"CUSTOM_MODEL_PROVIDER_TYPE": provider_type})

    assert data["model"] == "gpt-4o"
    assert data["api_base"] == "https://gateway.example.com/v1"
    # Non-ollama routes are untouched: thinking and list content pass through.
    assert data["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert data["messages"][0]["content"] == [{"type": "text", "text": "Hi"}]


@pytest.mark.anyio
async def test_pre_call_hook_ollama_drops_thinking_and_sanitizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mock_get_provider_credentials(**_: object) -> dict[str, str]:
        return {
            "CUSTOM_MODEL_PROVIDER_TYPE": "ollama",
            "CUSTOM_MODEL_PROVIDER_API_KEY": "ollama",
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "http://host:11434/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "qwen2.5",
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
            "model": "qwen2.5",
            "provider": "custom-model-provider",
            "catalog_id": "00000000-0000-0000-0000-000000000003",
            "base_url": "http://host:11434",
            "model_settings": {"reasoning_effort": "high"},
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={
            "model": "qwen2.5",
            "thinking": {"type": "enabled", "budget_tokens": 2048},
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "x"},
                        {"type": "text", "text": "Hello!"},
                    ],
                },
            ],
        },
        call_type="completion",
    )

    # Route stays on the catch-all; api_base carries /v1.
    assert result["model"] == "qwen2.5"
    assert result["api_base"] == "http://host:11434/v1"
    assert result["api_base"].endswith("/v1")
    assert "thinking" not in result
    assert "reasoning_effort" not in result
    assert result["messages"][1]["content"] == "Hello!"


@pytest.mark.anyio
async def test_pre_call_hook_generic_route_leaves_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic custom-provider routes are not sanitized (thinking passes)."""

    async def mock_get_provider_credentials(**_: object) -> dict[str, str]:
        return {
            "CUSTOM_MODEL_PROVIDER_TYPE": "generic_openai_compatible",
            "CUSTOM_MODEL_PROVIDER_API_KEY": "key",
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://gateway.example.com/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "gpt-4o",
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
            "model": "gpt-4o",
            "provider": "custom-model-provider",
            "catalog_id": "00000000-0000-0000-0000-000000000003",
            "base_url": "https://gateway.example.com/v1",
            "model_settings": {},
        },
    )

    handler = TracecatCallbackHandler()
    result = await handler.async_pre_call_hook(
        user_api_key_dict=user_api_key_dict,
        cache=cast(DualCache, object()),
        data={
            "model": "gpt-4o",
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        },
        call_type="completion",
    )

    assert result["model"] == "gpt-4o"
    assert result["api_base"] == "https://gateway.example.com/v1"
    assert result["thinking"] == {"type": "enabled", "budget_tokens": 2048}


@pytest.mark.anyio
async def test_pre_call_hook_ollama_subagent_route_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ollama root and a non-ollama subagent each resolve their own route."""
    creds_by_catalog = {
        "00000000-0000-0000-0000-0000000000aa": {
            "CUSTOM_MODEL_PROVIDER_TYPE": "ollama",
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "http://host:11434/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "qwen2.5",
        },
        "00000000-0000-0000-0000-0000000000bb": {
            "CUSTOM_MODEL_PROVIDER_TYPE": "generic_openai_compatible",
            "CUSTOM_MODEL_PROVIDER_BASE_URL": "https://gateway.example.com/v1",
            "CUSTOM_MODEL_PROVIDER_MODEL_NAME": "gpt-4o",
        },
    }

    async def mock_get_provider_credentials(**kwargs: Any) -> dict[str, str]:
        return creds_by_catalog[str(kwargs["catalog_id"])]

    monkeypatch.setattr(
        "tracecat.agent.gateway.get_provider_credentials",
        mock_get_provider_credentials,
    )

    metadata: dict[str, Any] = {
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "organization_id": "00000000-0000-0000-0000-000000000002",
        "model": "qwen2.5",
        "provider": "custom-model-provider",
        "catalog_id": "00000000-0000-0000-0000-0000000000aa",
        "model_settings": {},
        "routes": {
            "hosted_vllm/gpt-4o::tracecat-subagent::helper": {
                "model": "gpt-4o",
                "provider": "custom-model-provider",
                "catalog_id": "00000000-0000-0000-0000-0000000000bb",
                "base_url": None,
                "model_settings": {},
                "use_workspace_credentials": False,
            }
        },
    }
    handler = TracecatCallbackHandler()

    # Root ollama route: thinking dropped, catch-all model + /v1 base URL.
    root = await handler.async_pre_call_hook(
        user_api_key_dict=UserAPIKeyAuth(api_key="llm-token", metadata=metadata),
        cache=cast(DualCache, object()),
        data={
            "model": "qwen2.5",
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        },
        call_type="completion",
    )
    assert root["model"] == "qwen2.5"
    assert root["api_base"] == "http://host:11434/v1"
    assert "thinking" not in root

    # Non-ollama subagent route stays on hosted_vllm passthrough with thinking.
    sub = await handler.async_pre_call_hook(
        user_api_key_dict=UserAPIKeyAuth(api_key="llm-token", metadata=metadata),
        cache=cast(DualCache, object()),
        data={
            "model": "hosted_vllm/gpt-4o::tracecat-subagent::helper",
            "thinking": {"type": "enabled", "budget_tokens": 2048},
        },
        call_type="completion",
    )
    assert sub["model"] == "gpt-4o"
    assert sub["api_base"] == "https://gateway.example.com/v1"
    assert sub["thinking"] == {"type": "enabled", "budget_tokens": 2048}
