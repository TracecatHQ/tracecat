"""Auth helpers for the Tracecat LLM proxy."""

from __future__ import annotations

from collections.abc import Mapping

from tracecat.agent.tokens import LLMTokenClaims, verify_llm_token

_TRACE_REQUEST_ID_HEADER = "x-request-id"


def _get_header(headers: Mapping[str, str], key: str) -> str | None:
    for header_name, value in headers.items():
        if header_name.lower() == key.lower():
            return value
    return None


def _get_bearer_token(headers: Mapping[str, str]) -> str:
    authorization = _get_header(headers, "authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def verify_claims_from_headers(headers: Mapping[str, str]) -> LLMTokenClaims:
    """Verify the bearer token and return the decoded claims."""
    return verify_llm_token(_get_bearer_token(headers))


def get_trace_request_id(headers: Mapping[str, str]) -> str | None:
    """Extract the trace request ID header, if present."""
    return _get_header(headers, _TRACE_REQUEST_ID_HEADER)
