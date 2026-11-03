"""Map runtime classifications and HTTP outcomes to chat-facing model errors."""

import re

from tracecat.agent.model_status.schemas import ModelErrorDetail
from tracecat.agent.model_status.types import ModelErrorCode
from tracecat.runtime.errors import RuntimeErrorClassification, RuntimeErrorKind

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")

# Machine-readable provider or gateway error codes that name a cause outright.
_PROVIDER_ERROR_CODES: dict[str, ModelErrorCode] = {
    "context_length_exceeded": ModelErrorCode.CONTEXT_TOO_LONG,
    "model_not_found": ModelErrorCode.MODEL_UNAVAILABLE,
    "insufficient_quota": ModelErrorCode.BUDGET_EXCEEDED,
    "budget_exceeded": ModelErrorCode.BUDGET_EXCEEDED,
    "tracecat_llm_model_not_enabled": ModelErrorCode.MODEL_NOT_ENABLED,
    "tracecat_llm_provider_subscription_required": ModelErrorCode.MODEL_UNAVAILABLE,
    "tracecat_llm_provider_auth_failed": ModelErrorCode.PROVIDER_AUTH_FAILED,
}

_KIND_CODES: dict[RuntimeErrorKind, ModelErrorCode] = {
    RuntimeErrorKind.AGENT_LLM_PROVIDER_AUTH_FAILED: ModelErrorCode.PROVIDER_AUTH_FAILED,
    RuntimeErrorKind.AGENT_LLM_RATE_LIMITED: ModelErrorCode.RATE_LIMITED,
    RuntimeErrorKind.AGENT_LLM_BUDGET_EXCEEDED: ModelErrorCode.BUDGET_EXCEEDED,
    RuntimeErrorKind.AGENT_LLM_READ_TIMEOUT: ModelErrorCode.PROVIDER_UNREACHABLE,
    RuntimeErrorKind.AGENT_LLM_STREAM_INTERRUPTED: ModelErrorCode.PROVIDER_UNREACHABLE,
    RuntimeErrorKind.AGENT_EXECUTOR_TIMED_OUT: ModelErrorCode.PROVIDER_UNREACHABLE,
}


def safe_token(value: str | None) -> str | None:
    """Return ``value`` when it is a short identifier-like token, else None.

    Provider-controlled strings pass through this before they reach the chat,
    so a free-text message can never leak through an ID or code field.
    """
    if value is not None and _SAFE_TOKEN_RE.fullmatch(value):
        return value
    return None


def model_error_code_for_status(status_code: int | None) -> ModelErrorCode:
    """Map an upstream HTTP status to a chat-facing code."""
    match status_code:
        case None:
            return ModelErrorCode.UNKNOWN
        case 401 | 403:
            return ModelErrorCode.PROVIDER_AUTH_FAILED
        case 404:
            return ModelErrorCode.MODEL_UNAVAILABLE
        case 429:
            return ModelErrorCode.RATE_LIMITED
        case 408 | 504:
            return ModelErrorCode.PROVIDER_UNREACHABLE
        case code if code >= 500:
            return ModelErrorCode.PROVIDER_UNREACHABLE
        case code if code >= 400:
            return ModelErrorCode.REQUEST_REJECTED
        case _:
            return ModelErrorCode.UNKNOWN


def model_error_code(
    kind: RuntimeErrorKind | None,
    *,
    status_code: int | None = None,
    provider_error_code: str | None = None,
    provider_error_type: str | None = None,
) -> ModelErrorCode:
    """Pick the most specific chat-facing code for a failed model request.

    An explicit provider or gateway error code or type wins, then the runtime
    classification, then the HTTP status.
    """
    for token in (provider_error_code, provider_error_type):
        if token and (code := _PROVIDER_ERROR_CODES.get(token)):
            return code
    if kind is not None and (code := _KIND_CODES.get(kind)):
        return code
    return model_error_code_for_status(status_code)


def model_error_detail(
    classification: RuntimeErrorClassification | None,
    *,
    provider: str | None,
    model: str | None,
    status_code: int | None = None,
    request_id: str | None = None,
    provider_error_code: str | None = None,
    provider_error_type: str | None = None,
    provider_unreachable: bool = False,
) -> ModelErrorDetail:
    """Build the stream error detail for a failed model request.

    Args:
        classification: The runtime classification of the failure, if any.
        provider: Model provider slug, such as ``openai``.
        model: Model name as configured in Tracecat.
        status_code: Upstream HTTP status, when the provider answered.
        request_id: Provider or Tracecat request ID for support.
        provider_error_code: The provider's machine-readable ``error.code``.
        provider_error_type: The provider's machine-readable ``error.type``.
        provider_unreachable: The request never reached the provider.

    Returns:
        Detail safe to put on the chat stream.
    """
    kind = classification.kind if classification is not None else None
    error_code = safe_token(provider_error_code)
    error_type = safe_token(provider_error_type)
    code = model_error_code(
        kind,
        status_code=status_code,
        provider_error_code=error_code,
        provider_error_type=error_type,
    )
    if code is ModelErrorCode.UNKNOWN and provider_unreachable:
        code = ModelErrorCode.PROVIDER_UNREACHABLE
    return ModelErrorDetail(
        code=code,
        kind=kind,
        provider=provider,
        model=model,
        status_code=status_code
        if status_code is not None and 100 <= status_code <= 599
        else None,
        request_id=safe_token(request_id),
        provider_error_code=error_code or error_type,
    )
