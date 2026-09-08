"""Shared model-selection helpers for AI registry interfaces."""

from typing import Any

LEGACY_MODEL_FIELD_DEPRECATION_MESSAGE = "Use `model` instead."
LEGACY_MODEL_FIELD_SCHEMA_EXTRA: dict[str, Any] = {
    "x-tracecat-deprecation-message": LEGACY_MODEL_FIELD_DEPRECATION_MESSAGE
}
MCP_MODEL_SELECTION_FIELD_DOC = (
    "Model to use. Pick from the list of models enabled for this workspace. "
    "Provide this field, or both deprecated `model_name` and `model_provider`."
)
MCP_MODEL_NAME_FIELD_DOC = (
    "Deprecated. Use `model` instead. If `model` is omitted, set this together "
    "with `model_provider`."
)
MCP_MODEL_PROVIDER_FIELD_DOC = (
    "Deprecated. Use `model` instead. If `model` is omitted, set this together "
    "with `model_name`."
)
