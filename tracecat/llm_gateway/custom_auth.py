"""Custom authentication for LiteLLM proxy.

This module implements multi-tenant credential resolution for LiteLLM.
Credentials are fetched server-side based on workspace_id from request headers.
"""

from __future__ import annotations

import secrets as stdlib_secrets
import uuid

import orjson
from fastapi import Request
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.proxy_server import ProxyException

from tracecat import config
from tracecat.agent.config import MODEL_CONFIGS
from tracecat.agent.schemas import ModelConfig
from tracecat.agent.service import AgentManagementService
from tracecat.auth.types import Role
from tracecat.logger import logger

# Allowed providers - must match PROVIDER_CREDENTIAL_CONFIGS keys
ALLOWED_PROVIDERS = frozenset(
    {"openai", "anthropic", "bedrock", "custom-model-provider"}
)


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    """Validate incoming requests and extract tenant context.

    This is called by LiteLLM before processing any request.
    We use it to:
    1. Validate the service key (same pattern as tracecat/auth/credentials.py)
    2. Extract workspace_id from headers
    3. Return UserAPIKeyAuth with team_id set to workspace_id
    """
    # Validate service key (replicating _authenticate_service pattern)
    service_key = request.headers.get("x-tracecat-service-key")
    if not service_key or not stdlib_secrets.compare_digest(
        service_key, config.TRACECAT__SERVICE_KEY or ""
    ):
        raise ProxyException(
            message="Invalid service key",
            type="auth_error",
            param=None,
            code=401,
        )

    # Validate service ID is in whitelist
    service_id = request.headers.get("x-tracecat-role-service-id")
    if service_id not in config.TRACECAT__SERVICE_ROLES_WHITELIST:
        raise ProxyException(
            message=f"Service ID '{service_id}' not in whitelist",
            type="auth_error",
            param=None,
            code=403,
        )

    # Extract workspace context
    workspace_id = request.headers.get("x-tracecat-role-workspace-id")
    if not workspace_id:
        raise ProxyException(
            message="Missing workspace ID",
            type="auth_error",
            param=None,
            code=400,
        )

    # Validate workspace_id is a valid UUID
    try:
        uuid.UUID(workspace_id)
    except ValueError as err:
        raise ProxyException(
            message="Invalid workspace ID format",
            type="auth_error",
            param=None,
            code=400,
        ) from err

    # Extract credential scope preference
    use_workspace_creds = (
        request.headers.get("x-tracecat-use-workspace-credentials", "false").lower()
        == "true"
    )

    # Extract model settings from header (non-sensitive config like temperature, reasoning_effort)
    model_settings: dict = {}
    if model_settings_header := request.headers.get("x-tracecat-model-settings"):
        try:
            model_settings = orjson.loads(model_settings_header)
        except orjson.JSONDecodeError:
            pass  # Ignore malformed settings

    return UserAPIKeyAuth(
        api_key=api_key,
        team_id=workspace_id,
        user_id=service_id,
        metadata={
            "workspace_id": workspace_id,
            "use_workspace_credentials": use_workspace_creds,
            "model_settings": model_settings,
        },
    )


async def async_pre_call_hook(
    user_api_key_dict: UserAPIKeyAuth,
    cache,
    data: dict,
    call_type: str,
) -> dict:
    """Inject credentials before LLM call.

    SECURITY: This hook ONLY fetches LLM provider credentials.
    - Model must be in MODEL_CONFIGS (including custom-model-provider)
    - Provider is derived from model config, NOT user input
    - Credentials fetched via AgentManagementService which uses
      standardized secret names: agent-{provider}-credentials
    """
    workspace_id = user_api_key_dict.metadata.get("workspace_id")
    use_workspace_creds = user_api_key_dict.metadata.get(
        "use_workspace_credentials", False
    )
    model = data.get("model", "")

    # SECURITY: Validate model and get provider from config (not user input)
    model_config = _validate_and_get_model_config(model)
    provider = model_config.provider

    # Build role for credential access
    role = Role(
        type="service",
        workspace_id=uuid.UUID(workspace_id),
        service_id="tracecat-llm-gateway",
    )

    # Fetch credentials - scoped to LLM providers only
    async with AgentManagementService.with_session(role=role) as svc:
        if use_workspace_creds:
            creds = await svc.get_workspace_provider_credentials(provider)
        else:
            creds = await svc.get_provider_credentials(provider)

    if not creds:
        raise ProxyException(
            message=f"No credentials configured for provider '{provider}'",
            type="auth_error",
            param=None,
            code=401,
        )

    # Inject credentials based on provider type
    _inject_provider_credentials(data, provider, creds)

    # Inject model settings (temperature, reasoning_effort, response_format, etc.)
    model_settings = user_api_key_dict.metadata.get("model_settings", {})
    data.update(model_settings)

    logger.info(
        "Injected credentials for LLM call",
        workspace_id=workspace_id,
        provider=provider,
        model=model,
    )

    return data


def _validate_and_get_model_config(model: str) -> ModelConfig:
    """Validate model is allowed and return its config.

    SECURITY: This ensures we only process known models and derive
    the provider from our config, not from user-supplied strings.
    """
    # Handle custom model provider - uses "custom" key in MODEL_CONFIGS
    if model == "custom" or model.startswith("custom-model-provider/"):
        if "custom" not in MODEL_CONFIGS:
            raise ProxyException(
                message="Custom model provider not configured",
                type="invalid_request_error",
                param=None,
                code=400,
            )
        return MODEL_CONFIGS["custom"]

    # Standard model lookup
    if model not in MODEL_CONFIGS:
        raise ProxyException(
            message=f"Model '{model}' not found in allowed models",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    model_config = MODEL_CONFIGS[model]

    # Double-check provider is in allowed list
    if model_config.provider not in ALLOWED_PROVIDERS:
        raise ProxyException(
            message=f"Provider '{model_config.provider}' not allowed",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    return model_config


def _inject_provider_credentials(
    data: dict,
    provider: str,
    creds: dict[str, str],
) -> None:
    """Inject provider-specific credentials into request.

    SECURITY: Only injects credentials for known providers.
    Custom provider credentials include base_url and model_name overrides.
    """
    if provider == "openai":
        data["api_key"] = creds.get("OPENAI_API_KEY")

    elif provider == "anthropic":
        data["api_key"] = creds.get("ANTHROPIC_API_KEY")

    elif provider == "bedrock":
        # Bedrock supports either IAM credentials or bearer token
        if bearer_token := creds.get("AWS_BEARER_TOKEN_BEDROCK"):
            data["api_key"] = bearer_token
        else:
            data["aws_access_key_id"] = creds.get("AWS_ACCESS_KEY_ID")
            data["aws_secret_access_key"] = creds.get("AWS_SECRET_ACCESS_KEY")
        data["aws_region_name"] = creds.get("AWS_REGION")
        # Override model with ARN if provided
        if arn := creds.get("AWS_MODEL_ARN"):
            data["model"] = f"bedrock/{arn}"

    elif provider == "custom-model-provider":
        # Custom provider - supports api_key, base_url, model_name
        if api_key := creds.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
            data["api_key"] = api_key
        if base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL"):
            data["api_base"] = base_url
        if model_name := creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            data["model"] = model_name

    else:
        # Should never reach here due to ALLOWED_PROVIDERS check
        raise ProxyException(
            message=f"Unsupported provider: {provider}",
            type="invalid_request_error",
            param=None,
            code=400,
        )
