"""Custom authentication for LiteLLM proxy.

This module implements multi-tenant credential resolution for LiteLLM.
The gateway receives requests from jailed agent runtimes that authenticate
via JWT tokens minted by the agent executor.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

import jwt
from fastapi import Request
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import ProxyException, UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral
from pydantic import BaseModel, Field

from tracecat.agent.config import MODEL_CONFIGS
from tracecat.agent.service import AgentManagementService
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

SERVICE_KEY = os.environ.get("TRACECAT__SERVICE_KEY", "")

# LLM Token constants
LLM_TOKEN_ISSUER = "tracecat-agent-executor"
LLM_TOKEN_AUDIENCE = "tracecat-llm-gateway"
LLM_TOKEN_SUBJECT = "tracecat-agent-runtime"

# Allowed providers
ALLOWED_PROVIDERS = frozenset(
    {"openai", "anthropic", "bedrock", "custom-model-provider"}
)

logger = logging.getLogger("llm_gateway")


# -----------------------------------------------------------------------------
# LLM Token Claims
# -----------------------------------------------------------------------------


class LLMTokenClaims(BaseModel):
    """Claims extracted from a verified LLM token."""

    workspace_id: str = Field(..., description="Workspace UUID as string")
    session_id: str = Field(..., description="Agent session UUID as string")
    model: str = Field(..., description="The model to use for this run")
    model_settings: dict[str, Any] = Field(default_factory=dict)
    output_type: str | dict | None = Field(default=None)
    use_workspace_credentials: bool = Field(default=False)


def verify_llm_token(token: str) -> LLMTokenClaims:
    """Verify LLM JWT and return extracted claims."""
    if not SERVICE_KEY:
        raise ValueError("TRACECAT__SERVICE_KEY is not set")

    try:
        payload = jwt.decode(
            token,
            SERVICE_KEY,
            algorithms=["HS256"],
            audience=LLM_TOKEN_AUDIENCE,
            issuer=LLM_TOKEN_ISSUER,
        )
    except jwt.PyJWTError as exc:
        raise ValueError(f"Invalid LLM token: {exc}") from exc

    if payload.get("sub") != LLM_TOKEN_SUBJECT:
        raise ValueError("Invalid LLM token subject")

    return LLMTokenClaims.model_validate(payload)


# -----------------------------------------------------------------------------
# Credential Fetching via AgentManagementService
# -----------------------------------------------------------------------------


async def get_provider_credentials(
    workspace_id: str,
    provider: str,
    use_workspace_creds: bool = False,
) -> dict[str, str] | None:
    """Fetch provider credentials using AgentManagementService."""
    # Create a service role for the workspace
    role = Role(
        type="service",
        user_id=None,
        service_id="tracecat-llm-gateway",
        workspace_id=UUID(workspace_id),
    )

    async with get_async_session_context_manager() as session:
        service = AgentManagementService(session=session, role=role)

        if use_workspace_creds:
            return await service.get_workspace_provider_credentials(provider)
        else:
            return await service.get_provider_credentials(provider)


# -----------------------------------------------------------------------------
# LiteLLM Auth Hooks
# -----------------------------------------------------------------------------


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    """Validate LLM token from jailed agent runtime.

    The api_key parameter is the Bearer token sent by the SDK, which is
    actually our JWT token minted by the agent executor.
    """
    try:
        claims = verify_llm_token(api_key)
    except ValueError as e:
        logger.warning(f"LLM token validation failed: {e}")
        raise ProxyException(
            message=f"Invalid LLM token: {e}",
            type="auth_error",
            param=None,
            code=401,
        ) from e

    logger.info(
        f"Authenticated via LLM token: workspace={claims.workspace_id}, "
        f"session={claims.session_id}, model={claims.model}"
    )

    return UserAPIKeyAuth(
        api_key="llm-token",
        team_id=claims.workspace_id,
        user_id=f"agent-session:{claims.session_id}",
        metadata={
            "workspace_id": claims.workspace_id,
            "session_id": claims.session_id,
            "use_workspace_credentials": claims.use_workspace_credentials,
            "model": claims.model,
            "model_settings": claims.model_settings,
            "output_type": claims.output_type,
        },
    )


class TracecatCallbackHandler(CustomLogger):
    """Custom callback handler for LiteLLM proxy.

    Implements async_pre_call_hook to inject credentials and model settings
    before each LLM call.
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: CallTypesLiteral,
    ):
        """Inject credentials and model settings before LLM call."""
        workspace_id: str = user_api_key_dict.metadata.get("workspace_id", "")
        use_workspace_creds: bool = user_api_key_dict.metadata.get(
            "use_workspace_credentials", False
        )

        # Use model from token metadata (trusted)
        model = user_api_key_dict.metadata.get("model") or data.get("model", "")

        # Get provider from model
        provider = _get_provider_for_model(model)

        # Fetch credentials via AgentManagementService
        creds = await get_provider_credentials(
            workspace_id, provider, use_workspace_creds
        )

        if not creds:
            raise ProxyException(
                message=f"No credentials configured for provider '{provider}'",
                type="auth_error",
                param=None,
                code=401,
            )

        # Inject credentials based on provider type
        _inject_provider_credentials(data, provider, creds)

        # Inject model settings from token (only allowed non-sensitive keys)
        model_settings = user_api_key_dict.metadata.get("model_settings", {})
        if model_settings:
            allowed_keys = {
                "temperature",
                "max_tokens",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "stop",
                "reasoning_effort",
                "seed",
            }
            safe_settings = {
                k: v for k, v in model_settings.items() if k in allowed_keys
            }
            data.update(safe_settings)

        # Handle structured outputs via output_type -> response_format
        output_type = user_api_key_dict.metadata.get("output_type")
        if output_type is not None:
            response_format = _build_response_format(output_type)
            if response_format:
                data["response_format"] = response_format

        logger.info(
            f"Injected credentials: workspace={workspace_id}, provider={provider}, model={model}"
        )

        return data


# Callback handler instance for LiteLLM config
callback_handler = TracecatCallbackHandler()


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _get_provider_for_model(model: str) -> str:
    """Get provider for a model name."""
    # Handle custom model provider
    if model == "custom" or model.startswith("custom-model-provider/"):
        return "custom-model-provider"

    if model not in MODEL_CONFIGS:
        raise ProxyException(
            message=f"Model '{model}' not found in allowed models",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    provider = MODEL_CONFIGS[model].provider

    if provider not in ALLOWED_PROVIDERS:
        raise ProxyException(
            message=f"Provider '{provider}' not allowed",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    return provider


def _inject_provider_credentials(
    data: dict,
    provider: str,
    creds: dict[str, str],
) -> None:
    """Inject provider-specific credentials into request."""
    if provider == "openai":
        data["api_key"] = creds.get("OPENAI_API_KEY")

    elif provider == "anthropic":
        data["api_key"] = creds.get("ANTHROPIC_API_KEY")

    elif provider == "bedrock":
        if bearer_token := creds.get("AWS_BEARER_TOKEN_BEDROCK"):
            data["api_key"] = bearer_token
        else:
            data["aws_access_key_id"] = creds.get("AWS_ACCESS_KEY_ID")
            data["aws_secret_access_key"] = creds.get("AWS_SECRET_ACCESS_KEY")
        data["aws_region_name"] = creds.get("AWS_REGION")
        if arn := creds.get("AWS_MODEL_ARN"):
            data["model"] = f"bedrock/{arn}"

    elif provider == "custom-model-provider":
        if api_key := creds.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
            data["api_key"] = api_key
        if base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL"):
            data["api_base"] = base_url
        if model_name := creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            data["model"] = model_name

    else:
        raise ProxyException(
            message=f"Unsupported provider: {provider}",
            type="invalid_request_error",
            param=None,
            code=400,
        )


def _build_response_format(output_type: str | dict) -> dict | None:
    """Build LiteLLM response_format from output_type."""
    if isinstance(output_type, dict):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": output_type,
            },
        }

    primitive_schemas = {
        "str": {"type": "string"},
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "bool": {"type": "boolean"},
        "list[str]": {"type": "array", "items": {"type": "string"}},
        "list[int]": {"type": "array", "items": {"type": "integer"}},
        "list[float]": {"type": "array", "items": {"type": "number"}},
        "list[bool]": {"type": "array", "items": {"type": "boolean"}},
    }

    if output_type in primitive_schemas:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"result": primitive_schemas[output_type]},
                    "required": ["result"],
                    "additionalProperties": False,
                },
            },
        }

    logger.warning(f"Unknown output_type: {output_type}, skipping response_format")
    return None
