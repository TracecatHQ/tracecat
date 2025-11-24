"""Utilities for working with workflow trigger input schemas."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from tracecat.expressions.expectations import (
    ExpectedField,
    create_expectation_model,
)


def _inline_schema_refs(node: Any, defs: dict[str, Any] | None) -> bool:
    """Inline ``$ref`` entries pointing to ``defs`` in-place.

    Returns ``True`` if at least one replacement was made. This helper walks the
    full JSON schema tree so nested definitions (e.g. inside ``items``) are also
    inlined.
    """

    if not defs:
        return False

    replacement_made = False

    def _walk(value: Any) -> None:
        nonlocal replacement_made

        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref and ref.startswith("#/$defs/"):
                def_name = ref.split("/")[-1]
                if def_name in defs:
                    # Merge the referenced definition into the current node.
                    referenced = deepcopy(defs[def_name])
                    value.pop("$ref")
                    for key, ref_value in referenced.items():
                        # Preserve explicit field-level overrides.
                        value.setdefault(key, ref_value)
                    replacement_made = True

            for child in list(value.values()):
                _walk(child)

        elif isinstance(value, list):
            for item in list(value):
                _walk(item)

    _walk(node)
    return replacement_made


def _schema_contains_refs(node: Any, *, skip_defs: bool = True) -> bool:
    """Detect remaining ``$ref`` entries.``skip_defs`` ignores ``$defs`` nodes."""

    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                return True
            if skip_defs and key == "$defs":
                continue
            if _schema_contains_refs(value, skip_defs=skip_defs):
                return True
        return False

    if isinstance(node, list):
        return any(_schema_contains_refs(item, skip_defs=skip_defs) for item in node)

    return False


def build_trigger_inputs_schema(
    expects: Mapping[str, ExpectedField | dict[str, Any]] | None,
    *,
    model_name: str = "WorkflowTriggerInputs",
) -> dict[str, Any] | None:
    """Generate a JSON schema for workflow trigger inputs.

    Parameters
    ----------
    expects:
        Mapping of field names to :class:`ExpectedField` definitions. The mapping
        can contain either ``ExpectedField`` instances or dictionaries that can
        be validated into an ``ExpectedField``.
    model_name:
        Optional model name used when constructing the underlying Pydantic
        model. This name surfaces as the ``title`` attribute in the generated
        JSON schema.

    Returns
    -------
    dict[str, Any] | None
        JSON schema describing the expected trigger inputs, or ``None`` if no
        expectations were defined.
    """

    if not expects:
        return None

    # Ensure we are working with validated ``ExpectedField`` instances so we
    # can safely generate the Pydantic model and downstream schema.
    validated_fields = {
        field_name: ExpectedField.model_validate(field_schema)
        for field_name, field_schema in expects.items()
    }

    if not validated_fields:
        return None

    expectation_model = create_expectation_model(
        validated_fields, model_name=model_name
    )
    schema = expectation_model.model_json_schema()

    # Inline enum definitions from $defs for simpler schema
    if schema and "$defs" in schema and isinstance(schema["$defs"], dict):
        schema_defs = schema["$defs"]

        # Keep inlining until no more replacements are made. This handles nested
        # references where a referenced definition itself contains another $ref.
        while _inline_schema_refs(schema, schema_defs):
            continue

        # Clean up $defs only if there are no remaining $ref entries outside of
        # the $defs block. Leaving $defs in place avoids breaking downstream
        # consumers when nested references are still present.
        if not _schema_contains_refs(schema):
            schema.pop("$defs", None)

    return schema
