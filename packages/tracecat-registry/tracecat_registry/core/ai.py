"""Shared model-selection helpers for AI registry interfaces."""

from typing import Any

from tracecat_registry.fields import ModelSelection

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


def resolve_model_selection(
    *,
    model: ModelSelection | dict[str, Any] | None,
    model_name: str | None,
    model_provider: str | None,
    default: ModelSelection | None = None,
) -> ModelSelection:
    if model is not None:
        return ModelSelection.model_validate(model)
    if model_name is not None or model_provider is not None:
        if not model_name or not model_provider:
            raise ValueError(
                "Both deprecated `model_name` and `model_provider` must be set "
                "when `model` is not provided."
            )
        return ModelSelection(model_name=model_name, model_provider=model_provider)
    if default is not None:
        return default
    raise ValueError(
        "Either `model` or deprecated `model_name` and `model_provider` must be set."
    )
