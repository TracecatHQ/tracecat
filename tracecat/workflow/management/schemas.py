"""Utilities for working with workflow trigger input schemas."""

from __future__ import annotations

from typing import Any, Mapping

from tracecat.expressions.expectations import (
    ExpectedField,
    create_expectation_model,
)


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
    return schema

