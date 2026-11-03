"""Map runtime classifications and HTTP outcomes to chat-facing model errors."""

import re

from tracecat.agent.model_status.schemas import ModelErrorDetail
from tracecat.agent.model_status.types import ModelErrorCode
from tracecat.runtime.errors import RuntimeErrorClassification, RuntimeErrorKind

# Provider error codes and types are lowercase words (invalid_api_key,
# context_length_exceeded). Digits, capitals and hyphens are refused: an echoed
# key, such as a hex Azure key, always has some.
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z_.]{0,63}$")
# Request IDs in the shapes providers use: OpenAI and Anthropic ``req_…``, and
# UUIDs (LiteLLM, Mistral). Anything else may be an echoed key and is dropped.
_REQUEST_ID_RE = re.compile(
    r"^(req_[A-Za-z0-9]{8,64}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)

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
    # Tracecat's own gateway token was refused: the provider was never asked,
    # so the user's provider key is not the cause.
    RuntimeErrorKind.AGENT_LLM_GATEWAY_AUTH_FAILED: ModelErrorCode.UNKNOWN,
}


def _safe_request_id(value: str | None) -> str | None:
    """A provider request ID in a known shape; never free text or an echoed key."""
    if value is not None and _REQUEST_ID_RE.fullmatch(value):
        return value
    return None


def _safe_error_code(value: str | None) -> str | None:
    """A provider error code or type that is a lowercase word, else None."""
    if value is not None and _ERROR_CODE_RE.fullmatch(value):
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
        request_id: The provider's request ID, for support.
        provider_error_code: The provider's machine-readable ``error.code``.
        provider_error_type: The provider's machine-readable ``error.type``.
        provider_unreachable: The request never reached the provider.

    Returns:
        Detail safe to put on the chat stream.
    """
    kind = classification.kind if classification is not None else None
    error_code = _safe_error_code(provider_error_code)
    error_type = _safe_error_code(provider_error_type)
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
        request_id=_safe_request_id(request_id),
        provider_error_code=error_code or error_type,
    )
