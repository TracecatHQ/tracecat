"""Sanitize diagnostics before they leave an execution boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from tracecat.contexts import ctx_error_masking, ctx_secret_masks
from tracecat.secrets.error_masking import (
    WITHHELD_ERROR_MESSAGE,
    ErrorMaskingContext,
    ErrorMaskingMode,
)
from tracecat.secrets.masking import SecretMaskCollector


def sanitize_diagnostic(value: Any) -> Any:
    """Withhold unsafe diagnostics or mask known values before logging."""
    if (policy := ctx_error_masking.get()) is not None and policy.withhold:
        return WITHHELD_ERROR_MESSAGE
    if (masks := ctx_secret_masks.get()) is not None:
        return masks.redact(value)
    return value


@contextmanager
def error_masking_scope(mode: ErrorMaskingMode) -> Iterator[None]:
    """Isolate one activity's policy and diagnostic values from its caller."""
    policy_token = ctx_error_masking.set(ErrorMaskingContext(mode))
    masks_token = ctx_secret_masks.set(SecretMaskCollector())
    try:
        yield
    finally:
        ctx_secret_masks.reset(masks_token)
        ctx_error_masking.reset(policy_token)
