"""Custom authentication for LiteLLM proxy.

This module implements multi-tenant credential resolution for LiteLLM.
The gateway receives requests from jailed agent runtimes that authenticate
via JWT tokens minted by the agent executor.
"""

from __future__ import annotations

from fastapi import Request
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import ProxyException, UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral

from tracecat.agent.service import AgentManagementService
from tracecat.agent.tokens import verify_llm_token
from tracecat.auth.types import Role
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger

# -----------------------------------------------------------------------------
# Credential Fetching via AgentManagementService
# -----------------------------------------------------------------------------


async def get_provider_credentials(
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
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
        organization_id=organization_id,
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
            "organization_id": str(claims.organization_id),
            "session_id": str(claims.session_id),
            "use_workspace_credentials": claims.use_workspace_credentials,
            "model": claims.model,
            "provider": claims.provider,
            "model_settings": claims.model_settings,
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
        organization_id: str = user_api_key_dict.metadata.get("organization_id", "")
        use_workspace_creds: bool = user_api_key_dict.metadata.get(
            "use_workspace_credentials", True
        )

        # Use model and provider from token metadata (trusted, required claims)
        model = user_api_key_dict.metadata.get("model")
        provider = user_api_key_dict.metadata.get("provider")
        if not model or not provider:
            logger.warning(
                "Model or provider not found in token metadata",
                workspace_id=workspace_id,
                model=model,
                provider=provider,
            )
            raise ProxyException(
                message="Model not specified. Please select a model in the chat settings.",
                type="config_error",
                param=None,
                code=400,
            )
        data["model"] = model

        # Fetch credentials via AgentManagementService
        creds = await get_provider_credentials(
            workspace_id=WorkspaceID(workspace_id),
            organization_id=OrganizationID(organization_id),
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


def _inject_provider_credentials(
    data: dict,
    provider: str,
    creds: dict[str, str],
) -> None:
    """Inject provider-specific credentials into request."""
    match provider:
        case "openai":
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

        case "anthropic":
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

        case "bedrock":
            if bearer_token := creds.get("AWS_BEARER_TOKEN_BEDROCK"):
                data["api_key"] = bearer_token
            else:
                access_key = creds.get("AWS_ACCESS_KEY_ID")
                secret_key = creds.get("AWS_SECRET_ACCESS_KEY")
                session_token = creds.get("AWS_SESSION_TOKEN")

                if access_key and secret_key:
                    data["aws_access_key_id"] = access_key
                    data["aws_secret_access_key"] = secret_key
                    if session_token:
                        data["aws_session_token"] = session_token
                elif access_key or secret_key or session_token:
                    logger.warning(
                        "Partial static AWS credentials configured for Bedrock",
                        provider=provider,
                        has_access_key=bool(access_key),
                        has_secret_key=bool(secret_key),
                        has_session_token=bool(session_token),
                    )
                    raise ProxyException(
                        message="Provider credentials incomplete",
                        type="auth_error",
                        param=None,
                        code=401,
                    )
                else:
                    logger.info(
                        "No static Bedrock AWS credentials configured; using ambient IAM role credentials",
                        provider=provider,
                    )
            if region := creds.get("AWS_REGION"):
                data["aws_region_name"] = region

            # Inference Profile ID takes precedence (required for newer models like Claude 4)
            # Can be a system profile ID (us.anthropic.claude-sonnet-4-...) or custom ARN
            if inference_profile_id := creds.get("AWS_INFERENCE_PROFILE_ID"):
                data["model"] = f"bedrock/{inference_profile_id}"
            # Legacy: Direct model ID for older models that support on-demand throughput
            elif model_id := creds.get("AWS_MODEL_ID"):
                data["model"] = f"bedrock/{model_id}"
            else:
                raise ProxyException(
                    message="No Bedrock model configured. Set AWS_INFERENCE_PROFILE_ID (for newer models) or AWS_MODEL_ID (for legacy models) in your credentials.",
                    type="config_error",
                    param=None,
                    code=400,
                )

        case "custom-model-provider":
            # Custom provider for OpenAI-compatible endpoints (Ollama, vLLM, etc.)
            # OpenAI client requires an api_key - use dummy if not provided
            api_key = creds.get("CUSTOM_MODEL_PROVIDER_API_KEY") or "not-needed"
            base_url = creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL")
            model_name = creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME")

            logger.info(
                "Custom model provider credentials",
                api_key_set=bool(creds.get("CUSTOM_MODEL_PROVIDER_API_KEY")),
                base_url=base_url,
                model_name=model_name,
            )

            data["api_key"] = api_key
            if base_url:
                data["api_base"] = base_url
            if model_name:
                # Prefix with openai/ for LiteLLM routing
                data["model"] = f"openai/{model_name}"

            logger.info(
                "Injected custom model provider config",
                data_api_base=data.get("api_base"),
                data_model=data.get("model"),
            )

        case "azure_openai":
            # Azure OpenAI requires base URL, API version, and deployment name
            base = creds.get("AZURE_API_BASE")
            version = creds.get("AZURE_API_VERSION")
            deployment = creds.get("AZURE_DEPLOYMENT_NAME")

            if not base or not version or not deployment:
                logger.warning(
                    "Required Azure OpenAI config keys missing",
                    provider=provider,
                    has_base=bool(base),
                    has_version=bool(version),
                    has_deployment=bool(deployment),
                )
                raise ProxyException(
                    message="Azure OpenAI requires AZURE_API_BASE, AZURE_API_VERSION, and AZURE_DEPLOYMENT_NAME",
                    type="config_error",
                    param=None,
                    code=400,
                )

            # Auth: API key or Entra token (one required)
            api_key = creds.get("AZURE_API_KEY")
            ad_token = creds.get("AZURE_AD_TOKEN")

            if api_key:
                data["api_key"] = api_key
            elif ad_token:
                data["azure_ad_token"] = ad_token
            else:
                logger.warning(
                    "Azure OpenAI requires either AZURE_API_KEY or AZURE_AD_TOKEN",
                    provider=provider,
                )
                raise ProxyException(
                    message="Azure OpenAI requires either AZURE_API_KEY or AZURE_AD_TOKEN",
                    type="auth_error",
                    param=None,
                    code=401,
                )

            data["api_base"] = base.rstrip("/")
            data["api_version"] = version
            data["model"] = f"azure/{deployment}"

        case "azure_ai":
            # Azure AI requires base URL, API key, and model name
            base = creds.get("AZURE_API_BASE")
            api_key = creds.get("AZURE_API_KEY")
            model_name = creds.get("AZURE_AI_MODEL_NAME")

            if not base or not api_key or not model_name:
                logger.warning(
                    "Required Azure AI config keys missing",
                    provider=provider,
                    has_base=bool(base),
                    has_api_key=bool(api_key),
                    has_model_name=bool(model_name),
                )
                raise ProxyException(
                    message="Azure AI requires AZURE_API_BASE, AZURE_API_KEY, and AZURE_AI_MODEL_NAME",
                    type="config_error",
                    param=None,
                    code=400,
                )

            data["api_base"] = base.rstrip("/")
            data["api_key"] = api_key
            data["model"] = f"azure_ai/{model_name}"

        case _:
            logger.warning("Unsupported provider requested", provider=provider)
            raise ProxyException(
                message="Invalid provider configuration",
                type="invalid_request_error",
                param=None,
                code=400,
            )
