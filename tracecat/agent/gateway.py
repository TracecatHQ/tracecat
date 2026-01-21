"""Custom authentication for LiteLLM proxy.

This module implements multi-tenant credential resolution for LiteLLM.
The gateway receives requests from jailed agent runtimes that authenticate
via JWT tokens minted by the agent executor.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import ProxyException, UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral

from tracecat.agent.config import MODEL_CONFIGS
from tracecat.agent.service import AgentManagementService
from tracecat.agent.tokens import verify_llm_token
from tracecat.auth.types import Role
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Allowed providers
ALLOWED_PROVIDERS = frozenset(
    {"openai", "anthropic", "bedrock", "custom-model-provider"}
)


# -----------------------------------------------------------------------------
# Credential Fetching via AgentManagementService
# -----------------------------------------------------------------------------


async def get_provider_credentials(
    workspace_id: uuid.UUID,
    provider: str,
    use_workspace_creds: bool = False,
) -> dict[str, str] | None:
    """Fetch provider credentials using AgentManagementService."""
    # Create a service role for the workspace
    role = Role(
        type="service",
        user_id=None,
        service_id="tracecat-llm-gateway",
        workspace_id=workspace_id,
    )

    async with AgentManagementService.with_session(role=role) as service:
        if use_workspace_creds:
            return await service.get_workspace_provider_credentials(provider)
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
        logger.warning("LLM token validation failed")
        raise ProxyException(
            message="Invalid or expired token",
            type="auth_error",
            param=None,
            code=401,
        ) from e

    logger.debug("LLM token authenticated")

    return UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": str(claims.workspace_id),
            "session_id": str(claims.session_id),
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
            "use_workspace_credentials", True
        )

        # Use model from token metadata (trusted, required claim) - ignore request
        model = user_api_key_dict.metadata.get("model")
        if not model:
            logger.warning(
                "Model not found in token metadata", workspace_id=workspace_id
            )
            raise ProxyException(
                message="Model not specified. Please select a model in the chat settings.",
                type="config_error",
                param=None,
                code=400,
            )
        data["model"] = model

        # Get provider from model
        provider = _get_provider_for_model(model)

        # Fetch credentials via AgentManagementService
        creds = await get_provider_credentials(
            workspace_id=WorkspaceID(workspace_id),
            provider=provider,
            use_workspace_creds=use_workspace_creds,
        )

        if not creds:
            logger.warning(
                "No credentials configured for provider",
                workspace_id=workspace_id,
                provider=provider,
            )
            raise ProxyException(
                message=f"No {provider} API credentials configured. Add them in workspace settings.",
                type="credential_error",
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
            "Injected credentials for LLM call",
            workspace_id=workspace_id,
            provider=provider,
            model=model,
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
        logger.warning("Model not found in allowed models", model=model)
        raise ProxyException(
            message="Invalid model configuration",
            type="invalid_request_error",
            param=None,
            code=400,
        )

    provider = MODEL_CONFIGS[model].provider

    if provider not in ALLOWED_PROVIDERS:
        logger.warning("Provider not allowed", provider=provider, model=model)
        raise ProxyException(
            message="Invalid model configuration",
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
        api_key = creds.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning(
                "Required credential key missing for provider", provider=provider
            )
            raise ProxyException(
                message="Provider credentials incomplete",
                type="auth_error",
                param=None,
                code=401,
            )
        data["api_key"] = api_key

    elif provider == "anthropic":
        api_key = creds.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning(
                "Required credential key missing for provider", provider=provider
            )
            raise ProxyException(
                message="Provider credentials incomplete",
                type="auth_error",
                param=None,
                code=401,
            )
        data["api_key"] = api_key

    elif provider == "bedrock":
        if bearer_token := creds.get("AWS_BEARER_TOKEN_BEDROCK"):
            data["api_key"] = bearer_token
        else:
            access_key = creds.get("AWS_ACCESS_KEY_ID")
            secret_key = creds.get("AWS_SECRET_ACCESS_KEY")
            if not access_key or not secret_key:
                logger.warning(
                    "Required credential keys missing for provider", provider=provider
                )
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["aws_access_key_id"] = access_key
            data["aws_secret_access_key"] = secret_key
        if region := creds.get("AWS_REGION"):
            data["aws_region_name"] = region
        if arn := creds.get("AWS_MODEL_ARN"):
            data["model"] = f"bedrock/{arn}"

    elif provider == "custom-model-provider":
        # Custom provider has flexible requirements - all fields optional
        if api_key := creds.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
            data["api_key"] = api_key
        if base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL"):
            data["api_base"] = base_url
        if model_name := creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
            data["model"] = model_name

    else:
        logger.warning("Unsupported provider requested", provider=provider)
        raise ProxyException(
            message="Invalid provider configuration",
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

    logger.warning(
        "Unknown output_type, skipping response_format", output_type=output_type
    )
    return None
