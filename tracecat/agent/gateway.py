"""Custom authentication for the managed LiteLLM service."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode

import boto3
from aiocache import Cache
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Request
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import LitellmUserRoles, ProxyException, UserAPIKeyAuth
from litellm.types.utils import CallTypesLiteral

from tracecat import config as app_config
from tracecat.agent.litellm_compat import apply_patch
from tracecat.agent.service import AgentManagementService
from tracecat.agent.tokens import verify_llm_token
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers import OrganizationID, WorkspaceID
from tracecat.logger import logger

apply_patch()

_UNAUTHENTICATED_HEALTH_ROUTES = frozenset(
    {
        "/health",
        "/health/liveliness",
        "/health/liveness",
        "/health/readiness",
    }
)


def _strip_non_anthropic_beta_request_metadata(request: Request) -> None:
    """Remove Anthropic beta request metadata for non-Anthropic providers."""
    raw_headers = request.scope.get("headers", ())
    filtered_headers = [
        (name, value)
        for name, value in raw_headers
        if name.lower() != b"anthropic-beta"
    ]
    if len(filtered_headers) != len(raw_headers):
        request.scope["headers"] = filtered_headers
        request.__dict__.pop("_headers", None)

    raw_query = request.scope.get("query_string", b"")
    if not raw_query:
        return

    params = parse_qsl(raw_query.decode("latin-1"), keep_blank_values=True)
    filtered_params = [(key, value) for key, value in params if key != "beta"]
    if filtered_params != params:
        request.scope["query_string"] = urlencode(filtered_params, doseq=True).encode(
            "latin-1"
        )
        request.__dict__.pop("_query_params", None)
        request.__dict__.pop("_url", None)


def _strip_non_anthropic_beta_payload_fields(data: dict) -> None:
    """Remove Anthropic/Claude SDK-only request fields for non-Anthropic providers."""
    data.pop("anthropic_beta", None)
    data.pop("context_management", None)
    data.pop("output_config", None)
    data.pop("output_format", None)


_credential_cache: Any = Cache(
    Cache.MEMORY,
    ttl=app_config.TRACECAT__LLM_GATEWAY_CREDENTIAL_CACHE_TTL_SECONDS,
)

_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY = "TRACECAT_AWS_EXTERNAL_ID"
_DEFAULT_AWS_ROLE_SESSION_NAME = "tracecat-session"


def _credential_cache_key(
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    provider: str,
    catalog_id: uuid.UUID | None,
) -> str:
    # When a catalog_id is set the credentials belong to that specific
    # catalog row (cloud or custom provider in v2). Without it, legacy action
    # executions use workspace-scoped provider credentials.
    if catalog_id is not None:
        scope = str(catalog_id)
    else:
        scope = "workspace"
    return f"creds:{workspace_id}:{organization_id}:{provider}:{scope}"


def _metadata_bool(value: Any) -> bool:
    """Parse a bool-like metadata value from LiteLLM user metadata."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _get_aws_role_session_name(credentials: dict[str, str]) -> str:
    """Get the AWS role session name, falling back to a stable default."""
    if session_name := credentials.get("AWS_ROLE_SESSION_NAME"):
        if session_name := session_name.strip():
            return session_name
    return _DEFAULT_AWS_ROLE_SESSION_NAME


def _assume_bedrock_role(
    role_arn: str,
    *,
    external_id: str,
    session_name: str,
) -> dict[str, str]:
    """Assume the configured AWS role and return temporary Bedrock credentials."""
    sts_client = boto3.Session().client("sts")
    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        ExternalId=external_id,
    )
    session_credentials = response["Credentials"]
    return {
        "AWS_ACCESS_KEY_ID": session_credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": session_credentials["SecretAccessKey"],
        "AWS_SESSION_TOKEN": session_credentials["SessionToken"],
    }


async def _resolve_bedrock_runtime_credentials(
    credentials: dict[str, str],
) -> dict[str, str]:
    """Resolve Bedrock credentials into an explicit LiteLLM-compatible auth shape.

    This mirrors the precedence used by the direct boto3 Bedrock path:
    ``AWS_ROLE_ARN`` first, then static credentials, then bearer token.
    """
    if role_arn := credentials.get("AWS_ROLE_ARN"):
        if not (
            external_id := credentials.get(_AWS_ASSUME_ROLE_EXTERNAL_ID_SECRET_KEY)
        ):
            raise ProxyException(
                message="Bedrock role credentials require a Tracecat-provided workspace External ID.",
                type="config_error",
                param=None,
                code=400,
            )

        try:
            assumed_credentials = await asyncio.to_thread(
                _assume_bedrock_role,
                role_arn,
                external_id=external_id,
                session_name=_get_aws_role_session_name(credentials),
            )
        except (BotoCoreError, ClientError, KeyError) as exc:
            raise ProxyException(
                message="Failed to assume configured AWS role for Bedrock.",
                type="auth_error",
                param=None,
                code=401,
            ) from exc

        return credentials | assumed_credentials

    access_key = credentials.get("AWS_ACCESS_KEY_ID")
    secret_key = credentials.get("AWS_SECRET_ACCESS_KEY")
    session_token = credentials.get("AWS_SESSION_TOKEN")
    if access_key and secret_key:
        return credentials
    if access_key or secret_key or session_token:
        raise ProxyException(
            message="Bedrock static credentials require AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.",
            type="auth_error",
            param=None,
            code=401,
        )

    if credentials.get("AWS_BEARER_TOKEN_BEDROCK"):
        return credentials

    raise ProxyException(
        message="Bedrock requires one of AWS_ROLE_ARN, AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or AWS_BEARER_TOKEN_BEDROCK.",
        type="auth_error",
        param=None,
        code=401,
    )


async def get_provider_credentials(
    workspace_id: WorkspaceID,
    organization_id: OrganizationID,
    provider: str,
    catalog_id: uuid.UUID | None = None,
    use_workspace_credentials: bool = False,
) -> dict[str, str] | None:
    """Fetch provider credentials, with a process-local TTL cache.

    When ``catalog_id`` is set (v2 path), credentials are loaded from
    ``AgentManagementService.get_catalog_credentials``. When it's ``None``
    (legacy-replay tokens or direct-provider platform rows), use workspace-
    scoped provider secrets.
    """
    del use_workspace_credentials  # Kept for old token metadata/callers.
    cache_key = _credential_cache_key(
        workspace_id,
        organization_id,
        provider,
        catalog_id,
    )

    cached = await _credential_cache.get(key=cache_key)
    if cached is not None:
        return cached

    role = Role(
        type="service",
        user_id=None,
        service_id="tracecat-llm-gateway",
        workspace_id=workspace_id,
        organization_id=organization_id,
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-llm-gateway"],
    )
    async with AgentManagementService.with_session(role=role) as service:
        try:
            if catalog_id is not None:
                creds = await service.get_catalog_credentials(catalog_id)
            else:
                creds = await service.get_workspace_provider_credentials(provider)
                if creds is not None:
                    creds = await service._augment_runtime_provider_credentials(
                        provider,
                        creds,
                    )
        except ValueError as exc:
            raise ProxyException(
                message=str(exc),
                type="config_error",
                param=None,
                code=400,
            ) from exc

    if creds is not None and provider == "bedrock":
        creds = await _resolve_bedrock_runtime_credentials(creds)

    if creds is not None:
        await _credential_cache.set(key=cache_key, value=creds)

    return creds


async def user_api_key_auth(request: Request, api_key: str | None) -> UserAPIKeyAuth:
    """Validate the JWT token presented to LiteLLM."""
    if request.url.path in _UNAUTHENTICATED_HEALTH_ROUTES:
        logger.debug(
            "Allowing unauthenticated LiteLLM health probe", path=request.url.path
        )
        return UserAPIKeyAuth(
            api_key="health-probe",
            user_role=LitellmUserRoles.INTERNAL_USER_VIEW_ONLY,
        )

    try:
        claims = verify_llm_token(api_key or "")
    except ValueError as exc:
        logger.warning("LLM token validation failed")
        raise ProxyException(
            message="Invalid or expired token",
            type="auth_error",
            param=None,
            code=401,
        ) from exc

    if claims.provider != "anthropic":
        _strip_non_anthropic_beta_request_metadata(request)

    return UserAPIKeyAuth(
        api_key="llm-token",
        metadata={
            "workspace_id": str(claims.workspace_id),
            "organization_id": str(claims.organization_id),
            "session_id": str(claims.session_id),
            "catalog_id": str(claims.catalog_id) if claims.catalog_id else "",
            "use_workspace_credentials": claims.use_workspace_credentials,
            "model": claims.model,
            "provider": claims.provider,
            "base_url": claims.base_url,
            "model_settings": claims.model_settings,
        },
    )


class TracecatCallbackHandler(CustomLogger):
    """LiteLLM callback handler that injects provider credentials per request."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: CallTypesLiteral,
    ):
        del cache, call_type
        workspace_id = WorkspaceID(user_api_key_dict.metadata.get("workspace_id", ""))
        organization_id = OrganizationID(
            user_api_key_dict.metadata.get("organization_id", "")
        )
        model = user_api_key_dict.metadata.get("model")
        provider = user_api_key_dict.metadata.get("provider")
        catalog_id: uuid.UUID | None = None
        raw_catalog_id = user_api_key_dict.metadata.get("catalog_id")
        if raw_catalog_id:
            try:
                catalog_id = uuid.UUID(raw_catalog_id)
            except ValueError:
                raise ProxyException(
                    message="Invalid catalog_id in LLM token metadata",
                    type="config_error",
                    param=None,
                    code=400,
                ) from None
        use_workspace_credentials = (
            False
            if catalog_id is not None
            else _metadata_bool(
                user_api_key_dict.metadata.get("use_workspace_credentials")
            )
        )
        if not model or not provider:
            raise ProxyException(
                message="Model not specified. Please select a model in the chat settings.",
                type="config_error",
                param=None,
                code=400,
            )
        data["model"] = model

        creds = await get_provider_credentials(
            workspace_id=workspace_id,
            organization_id=organization_id,
            provider=provider,
            catalog_id=catalog_id,
            use_workspace_credentials=use_workspace_credentials,
        )
        if not creds:
            raise ProxyException(
                message=f"No {provider} API credentials configured. Add them in workspace settings.",
                type="credential_error",
                param=None,
                code=401,
            )

        _inject_provider_credentials(data, provider, creds)

        if (
            model_base_url := user_api_key_dict.metadata.get("base_url")
        ) and provider in {"openai", "anthropic", "custom-model-provider"}:
            data["api_base"] = model_base_url

        if provider != "anthropic":
            _strip_non_anthropic_beta_payload_fields(data)

        model_settings = _filter_allowed_model_settings(
            user_api_key_dict.metadata.get("model_settings", {}),
            provider=provider,
        )
        data.update(model_settings)

        # Strip after model_settings merge so they can't be re-added
        if provider == "bedrock":
            _strip_bedrock_unsupported_params(data)

        logger.info(
            "Injected credentials for LiteLLM call",
            workspace_id=str(workspace_id),
            provider=provider,
            model=model,
        )
        return data


callback_handler = TracecatCallbackHandler()

# ---------------------------------------------------------------------------
# Model settings filtering
# ---------------------------------------------------------------------------

_DEFAULT_ALLOWED_MODEL_SETTING_KEYS = {
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "reasoning_effort",
    "seed",
    "verbosity",
}

_PROVIDER_ALLOWED_MODEL_SETTING_KEYS: dict[str, frozenset[str]] = {
    "gemini": frozenset(
        {
            "top_k",
            "candidate_count",
            "response_mime_type",
            "response_schema",
            "response_json_schema",
        }
    ),
    "vertex_ai": frozenset(
        {
            "top_k",
            "candidate_count",
            "response_mime_type",
            "response_schema",
            "response_json_schema",
        }
    ),
    "bedrock": frozenset({"top_k"}),
}

_BEDROCK_DENIED_DEFAULT_KEYS = frozenset({"reasoning_effort"})

_TOKEN_LIMIT_KEYS = {"max_tokens", "max_completion_tokens"}


def _allowed_model_setting_keys(provider: str | None) -> set[str]:
    keys = set(_DEFAULT_ALLOWED_MODEL_SETTING_KEYS)
    if provider is not None:
        keys.update(_PROVIDER_ALLOWED_MODEL_SETTING_KEYS.get(provider, ()))
        if provider == "bedrock":
            keys -= _BEDROCK_DENIED_DEFAULT_KEYS
    return keys


def _clamp_max_tokens(payload: dict[str, Any]) -> None:
    """Clamp max_tokens / max_completion_tokens to at least 1 in-place."""
    for key in _TOKEN_LIMIT_KEYS:
        if (val := payload.get(key)) is not None and isinstance(val, (int, float)):
            if val < 1:
                payload[key] = 1


def _filter_allowed_model_settings(
    model_settings: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Keep only model settings supported by the selected provider family."""
    allowed_keys = _allowed_model_setting_keys(provider)
    filtered = {
        key: value for key, value in model_settings.items() if key in allowed_keys
    }
    _clamp_max_tokens(filtered)
    return filtered


def _strip_bedrock_unsupported_params(data: dict) -> None:
    """Strip params that Bedrock doesn't reliably support."""
    data.pop("thinking", None)
    data.pop("reasoning_effort", None)


def _inject_provider_credentials(
    data: dict,
    provider: str,
    creds: dict[str, str],
) -> None:
    """Inject provider-specific credentials into a LiteLLM request dict."""
    match provider:
        case "openai":
            api_key = creds.get("OPENAI_API_KEY")
            if not api_key:
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_key"] = api_key
            if not data.get("model", "").startswith("openai/"):
                data["model"] = f"openai/{data['model']}"
            if base_url := creds.get("OPENAI_BASE_URL"):
                data["api_base"] = base_url

        case "anthropic":
            api_key = creds.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_key"] = api_key
            if not data.get("model", "").startswith("anthropic/"):
                data["model"] = f"anthropic/{data['model']}"
            if base_url := creds.get("ANTHROPIC_BASE_URL"):
                data["api_base"] = base_url

        case "gemini":
            api_key = creds.get("GEMINI_API_KEY")
            if not api_key:
                raise ProxyException(
                    message="Provider credentials incomplete",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_key"] = api_key
            if not data.get("model", "").startswith("gemini/"):
                data["model"] = f"gemini/{data['model']}"

        case "vertex_ai":
            credentials = creds.get("GOOGLE_API_CREDENTIALS")
            project = creds.get("GOOGLE_CLOUD_PROJECT")
            model_name = creds.get("VERTEX_AI_MODEL")
            if not credentials or not project or not model_name:
                raise ProxyException(
                    message="Vertex AI requires GOOGLE_API_CREDENTIALS, GOOGLE_CLOUD_PROJECT, and VERTEX_AI_MODEL",
                    type="config_error",
                    param=None,
                    code=400,
                )
            data["vertex_credentials"] = credentials
            data["vertex_project"] = project
            data["model"] = f"vertex_ai/{model_name}"
            if location := creds.get("GOOGLE_CLOUD_LOCATION"):
                data["vertex_location"] = location

        case "bedrock":
            access_key = creds.get("AWS_ACCESS_KEY_ID")
            secret_key = creds.get("AWS_SECRET_ACCESS_KEY")
            session_token = creds.get("AWS_SESSION_TOKEN")
            if access_key and secret_key:
                data["aws_access_key_id"] = access_key
                data["aws_secret_access_key"] = secret_key
                if session_token:
                    data["aws_session_token"] = session_token
            elif bearer_token := creds.get("AWS_BEARER_TOKEN_BEDROCK"):
                data["api_key"] = bearer_token
            else:
                raise ProxyException(
                    message="Bedrock credentials must be resolved before request dispatch.",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            if region := creds.get("AWS_REGION"):
                data["aws_region_name"] = region
            if inference_profile_id := creds.get("AWS_INFERENCE_PROFILE_ID"):
                data["model"] = f"bedrock/{inference_profile_id}"
            elif model_id := creds.get("AWS_MODEL_ID"):
                data["model"] = f"bedrock/{model_id}"
            else:
                raise ProxyException(
                    message="No Bedrock model configured. Set AWS_INFERENCE_PROFILE_ID or AWS_MODEL_ID in your credentials.",
                    type="config_error",
                    param=None,
                    code=400,
                )

        case "custom-model-provider":
            if api_key := creds.get("CUSTOM_MODEL_PROVIDER_API_KEY"):
                data["api_key"] = api_key
            if base_url := creds.get("CUSTOM_MODEL_PROVIDER_BASE_URL"):
                data["api_base"] = base_url
            if model_name := creds.get("CUSTOM_MODEL_PROVIDER_MODEL_NAME"):
                data["model"] = model_name

        case "azure_openai":
            base = creds.get("AZURE_API_BASE")
            version = creds.get("AZURE_API_VERSION")
            deployment = creds.get("AZURE_DEPLOYMENT_NAME")
            if not base or not version or not deployment:
                raise ProxyException(
                    message="Azure OpenAI requires AZURE_API_BASE, AZURE_API_VERSION, and AZURE_DEPLOYMENT_NAME",
                    type="config_error",
                    param=None,
                    code=400,
                )
            if api_key := creds.get("AZURE_API_KEY"):
                data["api_key"] = api_key
            elif ad_token := creds.get("AZURE_AD_TOKEN"):
                data["azure_ad_token"] = ad_token
            else:
                raise ProxyException(
                    message="Azure OpenAI requires AZURE_API_KEY, AZURE_AD_TOKEN, or Azure Entra client credentials (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET).",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_base"] = base
            data["api_version"] = version
            data["model"] = f"azure/{deployment}"

        case "azure_ai":
            base = creds.get("AZURE_API_BASE")
            version = creds.get("AZURE_API_VERSION")
            model_name = creds.get("AZURE_AI_MODEL_NAME")
            if not base or not model_name:
                raise ProxyException(
                    message="Azure AI requires AZURE_API_BASE and AZURE_AI_MODEL_NAME",
                    type="config_error",
                    param=None,
                    code=400,
                )
            if api_key := creds.get("AZURE_API_KEY"):
                data["api_key"] = api_key
            elif ad_token := creds.get("AZURE_AD_TOKEN"):
                data["azure_ad_token"] = ad_token
            else:
                raise ProxyException(
                    message="Azure AI requires AZURE_API_KEY, AZURE_AD_TOKEN, or Azure Entra client credentials (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET).",
                    type="auth_error",
                    param=None,
                    code=401,
                )
            data["api_base"] = base
            if version:
                data["api_version"] = version
            data["model"] = f"azure_ai/{model_name}"

        case _:
            raise ProxyException(
                message=f"Unsupported provider: {provider}",
                type="config_error",
                param=None,
                code=400,
            )
