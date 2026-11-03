"""Chat-facing model error codes shared by every agent backend."""

from enum import StrEnum
from typing import Literal


class ModelErrorCode(StrEnum):
    """Why a model request failed, phrased for the person in the chat.

    Backends map their own failures to one of these; the UI maps each one to
    an actionable message. ``UNKNOWN`` is the fallback for anything else.
    """

    CREDENTIALS_MISSING = "credentials_missing"
    PROVIDER_AUTH_FAILED = "provider_auth_failed"
    MODEL_NOT_ENABLED = "model_not_enabled"
    MODEL_UNAVAILABLE = "model_unavailable"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXCEEDED = "budget_exceeded"
    CONTEXT_TOO_LONG = "context_too_long"
    PROVIDER_UNREACHABLE = "provider_unreachable"
    REQUEST_REJECTED = "request_rejected"
    UNKNOWN = "unknown"


type ModelStatusState = Literal["ready", "unverified", "problem"]
"""``unverified``: credentials exist, but the provider has no cheap check."""
