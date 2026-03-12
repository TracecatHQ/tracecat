"""Custom authentication for LiteLLM proxy.

This module implements multi-tenant credential resolution for LiteLLM.
The gateway receives requests from jailed agent runtimes that authenticate
via JWT tokens minted by the agent executor.
"""

from __future__ import annotations

import logging
import re
import uuid

import orjson
from fastapi import Request
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import LitellmUserRoles, ProxyException, UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral

from tracecat.agent.litellm_compat import apply_patch
from tracecat.agent.litellm_observability import get_load_tracker
from tracecat.agent.service import (
    SOURCE_RUNTIME_API_KEY,
    SOURCE_RUNTIME_API_KEY_HEADER,
    SOURCE_RUNTIME_API_VERSION,
    SOURCE_RUNTIME_BASE_URL,
    AgentManagementService,
)
from tracecat.agent.tokens import verify_llm_token
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers import OrganizationID, WorkspaceID

# Apply monkeypatches for LiteLLM adapter compatibility fixes
apply_patch()

# -----------------------------------------------------------------------------
# Credential Fetching via AgentManagementService
# -----------------------------------------------------------------------------

_UNAUTHENTICATED_HEALTH_ROUTES = {
    "/health/liveliness",
    "/health/liveness",
    "/health/readiness",
}
_gateway_load_tracker = get_load_tracker("litellm_gateway")
_hook_request_counters: dict[int, int] = {}
_SANITIZED_ERROR_MAX_LENGTH = 512
_TRACE_REQUEST_ID_HEADER = "x-request-id"
_gateway_logger = logging.getLogger(__name__)
_SENSITIVE_ERROR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"),
        r"\1 [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)(\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\b[^:]{0,40}:\s*)([^\s,;]+)"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret)=([^&\s]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)(authorization:\s*(?:basic|bearer)\s+)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)(://[^/\s:@]+:)([^@\s/]+)@"),
        r"\1[REDACTED]@",
    ),
)


def _load_fields() -> dict[str, int]:
    snapshot = _gateway_load_tracker.snapshot()
    return {
        "active_gateway_requests": snapshot.active_requests,
        "gateway_peak_active_requests": snapshot.peak_active_requests,
    }


def _remember_hook_request_counter(data: dict, request_counter: int) -> None:
    """Track hook request counters out-of-band so they never leak upstream."""
    _hook_request_counters[id(data)] = request_counter


def _pop_hook_request_counter(data: dict) -> int | None:
    """Return and clear the tracked request counter for a LiteLLM request payload."""
    return _hook_request_counters.pop(id(data), None)


def _sanitize_exception_message(exc: Exception) -> str:
    sanitized = str(exc).strip()
    for pattern, replacement in _SENSITIVE_ERROR_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    if len(sanitized) > _SANITIZED_ERROR_MAX_LENGTH:
        return f"{sanitized[:_SANITIZED_ERROR_MAX_LENGTH]}...[truncated]"
    return sanitized


def _gateway_log(level: int, message: str, **fields: object) -> None:
    if not _gateway_logger.isEnabledFor(level):
        return
    if not fields:
        _gateway_logger.log(level, message)
        return
    serialized_fields = orjson.dumps(fields, default=str).decode("utf-8")
    _gateway_logger.log(level, "%s | %s", message, serialized_fields)


async def get_runtime_credentials(
    *,
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    model_name: str,
    provider: str,
    source_id: str | None,
) -> dict[str, str]:
    """Fetch credentials for either direct providers or catalog-backed sources."""
    role = Role(
        type="service",
        user_id=None,
        service_id="tracecat-llm-gateway",
        workspace_id=workspace_id,
        organization_id=organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-llm-gateway"],
    )
    async with AgentManagementService.with_session(role=role) as service:
        return await service.get_runtime_credentials_for_selection(
            selection=service._model_selection_from_key(
                service._selection_key(
                    source_id=uuid.UUID(source_id) if source_id else None,
                    model_provider=provider,
                    model_name=model_name,
                )
            )
        )


# -----------------------------------------------------------------------------
# LiteLLM Auth Hooks
# -----------------------------------------------------------------------------


async def user_api_key_auth(request: Request, api_key: str | None) -> UserAPIKeyAuth:
    """Validate LLM token from jailed agent runtime.

    The api_key parameter is the Bearer token sent by the SDK, which is
    actually our JWT token minted by the agent executor.
    """
    if request.url.path in _UNAUTHENTICATED_HEALTH_ROUTES:
        _gateway_log(
            logging.DEBUG,
            "Allowing unauthenticated LiteLLM health probe",
            path=request.url.path,
            **_load_fields(),
        )
        return UserAPIKeyAuth(
            api_key="health-probe",
            user_role=LitellmUserRoles.INTERNAL_USER_VIEW_ONLY,
        )

    try:
        claims = verify_llm_token(api_key or "")
    except ValueError as e:
        _gateway_log(logging.WARNING, "LLM token validation failed", **_load_fields())
        raise ProxyException(
            message="Invalid or expired token",
            type="auth_error",
            param=None,
            code=401,
        ) from e

    return UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": str(claims.workspace_id),
            "organization_id": str(claims.organization_id),
            "session_id": str(claims.session_id),
            "trace_request_id": request.headers.get(_TRACE_REQUEST_ID_HEADER),
            "model": claims.model,
            "provider": claims.provider,
            "source_id": str(claims.source_id) if claims.source_id else None,
            "base_url": claims.base_url,
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

        # Use model and provider from token metadata (trusted, required claims)
        model = user_api_key_dict.metadata.get("model")
        provider = user_api_key_dict.metadata.get("provider")
        source_id = user_api_key_dict.metadata.get("source_id")
        if not model or not provider:
            _gateway_log(
                logging.WARNING,
                "Model or provider not found in token metadata",
                workspace_id=workspace_id,
                model=model,
                provider=provider,
                **_load_fields(),
            )
            raise ProxyException(
                message="Model not specified. Please select a model in the chat settings.",
                type="config_error",
                param=None,
                code=400,
            )
        data["model"] = model

        creds = await get_runtime_credentials(
            workspace_id=WorkspaceID(workspace_id),
            organization_id=OrganizationID(organization_id),
            model_name=model,
            provider=provider,
            source_id=source_id,
        )

        if not creds and provider not in {
            "default_sidecar",
            "openai_compatible_gateway",
            "manual_custom",
        }:
            _gateway_log(
                logging.WARNING,
                "No credentials configured for provider",
                workspace_id=workspace_id,
                provider=provider,
                **_load_fields(),
            )
            raise ProxyException(
                message=f"No {provider} API credentials configured. Add them in organization agent settings.",
                type="credential_error",
                param=None,
                code=401,
            )

        # Inject credentials based on provider type
        _inject_provider_credentials(data, provider, creds, source_id=source_id)
        if model_base_url := user_api_key_dict.metadata.get("base_url"):
            # Preset/config base_url should override provider credential base URL
            data["api_base"] = model_base_url

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

        request_counter, _ = _gateway_load_tracker.begin_request()
        _remember_hook_request_counter(data, request_counter)

        return data

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: UserAPIKeyAuth,
        response: object,
    ) -> object:
        _gateway_load_tracker.end_request()
        _pop_hook_request_counter(data)
        return response

    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict: UserAPIKeyAuth,
        traceback_str: str | None = None,
    ) -> None:
        load_snapshot = _gateway_load_tracker.end_request()
        request_counter = _pop_hook_request_counter(request_data)
        sanitized_error = _sanitize_exception_message(original_exception)
        _gateway_log(
            logging.ERROR,
            "LiteLLM call failed",
            request_counter=request_counter,
            trace_request_id=user_api_key_dict.metadata.get("trace_request_id"),
            session_id=user_api_key_dict.metadata.get("session_id"),
            workspace_id=user_api_key_dict.metadata.get("workspace_id"),
            provider=user_api_key_dict.metadata.get("provider"),
            model=user_api_key_dict.metadata.get("model"),
            error=sanitized_error,
            error_type=type(original_exception).__name__,
            has_traceback=bool(traceback_str),
            active_gateway_requests=load_snapshot.active_requests,
            gateway_peak_active_requests=load_snapshot.peak_active_requests,
        )
        return None


# Callback handler instance for LiteLLM config
callback_handler = TracecatCallbackHandler()


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _inject_provider_credentials(
    data: dict,
    provider: str,
    creds: dict[str, str],
    *,
    source_id: str | None,
) -> None:
    """Inject provider-specific credentials into request."""
    if source_id is not None and provider in {
        "openai_compatible_gateway",
        "manual_custom",
        "direct_endpoint",
    }:
        api_key = creds.get(SOURCE_RUNTIME_API_KEY)
        api_key_header = creds.get(SOURCE_RUNTIME_API_KEY_HEADER)
        if api_key and api_key_header and api_key_header.lower() != "authorization":
            _set_extra_headers(data, {api_key_header: api_key})
            data["api_key"] = "not-needed"
        else:
            data["api_key"] = api_key or "not-needed"
        if base_url := creds.get(SOURCE_RUNTIME_BASE_URL) or creds.get(
            "OPENAI_BASE_URL"
        ):
            data["api_base"] = base_url
        if api_version := creds.get(SOURCE_RUNTIME_API_VERSION):
            data["api_version"] = api_version
        if not str(data.get("model", "")).startswith("openai/"):
            data["model"] = f"openai/{data['model']}"
        return

    match provider:
        case "openai":
            api_key = creds.get("OPENAI_API_KEY")
            if not api_key:
                _gateway_log(
                    logging.WARNING,
                    "Required credential key missing for provider",
                    provider=provider,
                )
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_key"] = api_key
            if base_url := creds.get("OPENAI_BASE_URL"):
                data["api_base"] = base_url
            if not str(data.get("model", "")).startswith("openai/"):
                data["model"] = f"openai/{data['model']}"

        case "anthropic":
            api_key = creds.get("ANTHROPIC_API_KEY")
            if not api_key:
                _gateway_log(
                    logging.WARNING,
                    "Required credential key missing for provider",
                    provider=provider,
                )
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_key"] = api_key
            if base_url := creds.get("ANTHROPIC_BASE_URL"):
                data["api_base"] = base_url
            if not str(data.get("model", "")).startswith("anthropic/"):
                data["model"] = f"anthropic/{data['model']}"

        case "gemini":
            api_key = creds.get("GEMINI_API_KEY")
            if not api_key:
                _gateway_log(
                    logging.WARNING,
                    "Required credential key missing for provider",
                    provider=provider,
                )
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_key"] = api_key
            # Prefix model name for LiteLLM routing (e.g. gemini-2.5-flash -> gemini/gemini-2.5-flash)
            if not data.get("model", "").startswith("gemini/"):
                data["model"] = f"gemini/{data['model']}"

        case "vertex_ai":
            credentials = creds.get("GOOGLE_API_CREDENTIALS")
            project = creds.get("GOOGLE_CLOUD_PROJECT")
            model_name = creds.get("VERTEX_AI_MODEL") or str(data.get("model") or "")
            if not credentials or not project or not model_name:
                _gateway_log(
                    logging.WARNING,
                    "Required Vertex AI config keys missing",
                    provider=provider,
                    has_credentials=bool(credentials),
                    has_project=bool(project),
                    has_model_name=bool(model_name),
                )
                raise ProxyException(
                    message="Vertex AI requires GOOGLE_API_CREDENTIALS and GOOGLE_CLOUD_PROJECT.",
                    type="config_error",
                    param=None,
                    code=400,
                )

            data["vertex_credentials"] = credentials
            data["vertex_project"] = project
            data["model"] = (
                model_name
                if model_name.startswith("vertex_ai/")
                else f"vertex_ai/{model_name}"
            )
            if location := creds.get("GOOGLE_CLOUD_LOCATION"):
                data["vertex_location"] = location

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
                    _gateway_log(
                        logging.WARNING,
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
                    _gateway_log(
                        logging.DEBUG,
                        "No static Bedrock AWS credentials configured; using ambient IAM role credentials",
                        provider=provider,
                    )
            if region := creds.get("AWS_REGION"):
                data["aws_region_name"] = region

            if inference_profile_id := creds.get("AWS_INFERENCE_PROFILE_ID"):
                data["model"] = f"bedrock/{inference_profile_id}"
            elif model_id := creds.get("AWS_MODEL_ID"):
                data["model"] = f"bedrock/{model_id}"
            elif model_name := str(data.get("model") or ""):
                data["model"] = (
                    model_name
                    if model_name.startswith("bedrock/")
                    else f"bedrock/{model_name}"
                )
            else:
                raise ProxyException(
                    message="No Bedrock model configured. Select a model or add a default Bedrock override in the provider settings.",
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

            _gateway_log(
                logging.DEBUG,
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

            _gateway_log(
                logging.DEBUG,
                "Injected custom model provider config",
                data_api_base=data.get("api_base"),
                data_model=data.get("model"),
            )

        case "azure_openai":
            base = creds.get("AZURE_API_BASE")
            version = creds.get("AZURE_API_VERSION")
            deployment = creds.get("AZURE_DEPLOYMENT_NAME") or str(
                data.get("model") or ""
            )

            if not base or not version or not deployment:
                _gateway_log(
                    logging.WARNING,
                    "Required Azure OpenAI config keys missing",
                    provider=provider,
                    has_base=bool(base),
                    has_version=bool(version),
                    has_deployment=bool(deployment),
                )
                raise ProxyException(
                    message="Azure OpenAI requires AZURE_API_BASE and AZURE_API_VERSION plus either a selected model or a default deployment override.",
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
                _gateway_log(
                    logging.WARNING,
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
            base = creds.get("AZURE_API_BASE")
            api_key = creds.get("AZURE_API_KEY")
            model_name = creds.get("AZURE_AI_MODEL_NAME") or str(
                data.get("model") or ""
            )

            if not base or not api_key or not model_name:
                _gateway_log(
                    logging.WARNING,
                    "Required Azure AI config keys missing",
                    provider=provider,
                    has_base=bool(base),
                    has_api_key=bool(api_key),
                    has_model_name=bool(model_name),
                )
                raise ProxyException(
                    message="Azure AI requires AZURE_API_BASE and AZURE_API_KEY plus either a selected model or a default model override.",
                    type="config_error",
                    param=None,
                    code=400,
                )

            data["api_base"] = base.rstrip("/")
            data["api_key"] = api_key
            data["model"] = (
                model_name
                if model_name.startswith("azure_ai/")
                else f"azure_ai/{model_name}"
            )

        case _:
            _gateway_log(
                logging.WARNING,
                "Unsupported provider requested",
                provider=provider,
            )
            raise ProxyException(
                message="Invalid provider configuration",
                type="invalid_request_error",
                param=None,
                code=400,
            )

    if source_id is not None:
        _apply_source_request_overrides(data, creds)


def _set_extra_headers(data: dict, extra_headers: dict[str, str]) -> None:
    current = data.get("extra_headers")
    headers = dict(current) if isinstance(current, dict) else {}
    headers.update(extra_headers)
    data["extra_headers"] = headers


def _apply_source_request_overrides(data: dict, creds: dict[str, str]) -> None:
    api_key = creds.get(SOURCE_RUNTIME_API_KEY)
    api_key_header = creds.get(SOURCE_RUNTIME_API_KEY_HEADER)
    if api_key and api_key_header and api_key_header.lower() != "authorization":
        _set_extra_headers(data, {api_key_header: api_key})
        data.pop("api_key", None)
    if base_url := creds.get(SOURCE_RUNTIME_BASE_URL):
        data["api_base"] = base_url
    if api_version := creds.get(SOURCE_RUNTIME_API_VERSION):
        data["api_version"] = api_version
