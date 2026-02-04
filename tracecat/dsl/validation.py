from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel
from temporalio import activity

from tracecat.dsl.schemas import TriggerInputs
from tracecat.expressions.expectations import (
    ExpectedField,
    create_expectation_model,
)
from tracecat.validation.schemas import ValidationDetail
from tracecat.workflow.executions.enums import TriggerType


def normalize_trigger_inputs(
    input_schema: dict[str, ExpectedField],
    payload: TriggerInputs,
    *,
    model_name: str = "TriggerInputsNormalizer",
) -> TriggerInputs:
    """Apply defaults from the DSL `entrypoint.expects` to trigger inputs.

    Returns a new dict with defaults filled where not provided.
    If no expects schema is present, returns the original payload or `{}`.
    """
    # If there's no expects schema or the payload is not a dict, return the original payload
    if not isinstance(payload, dict) or not input_schema:
        return payload

    expects_schema = {
        field_name: ExpectedField.model_validate(field_schema)
        for field_name, field_schema in input_schema.items()
    }
    # Build a pydantic model from schema and dump with defaults applied
    validator = create_expectation_model(expects_schema, model_name=model_name)
    model = validator(**payload)
    return model.model_dump(mode="json")


def format_input_schema_validation_error(details: list[ValidationDetail]) -> str:
    lines = ["Failed to validate trigger inputs:\n"]
    for i, d in enumerate(details):
        loc = d.loc or ["<root>"]
        if handler := PYDANTIC_ERR_TYPE_HANDLER.get(d.type):
            message = handler(loc)
        else:
            message = f"The attribute '{'.'.join(str(s) for s in loc)}' does not match the input schema:\n{d.msg}.\n({d.type})"
        lines.append(f"{i + 1}. {message}")
    return "\n".join(lines)


PYDANTIC_ERR_TYPE_HANDLER: dict[str, Callable[[Sequence[int | str]], str]] = {
    "pydantic.extra_forbidden": lambda loc: (
        f"The attribute '{'.'.join(str(s) for s in loc)}' is not allowed in the input schema."
    ),
    "pydantic.missing": lambda loc: (
        f"Missing required field(s): '{'.'.join(str(s) for s in loc)}'."
    ),
    "pydantic.invalid_type": lambda loc: (
        f"Invalid type at '{'.'.join(str(s) for s in loc)}'."
    ),
}


class ResolveTimeAnchorActivityInputs(BaseModel):
    """Inputs for resolving the workflow time anchor."""

    trigger_type: TriggerType
    start_time: datetime
    scheduled_start_time: datetime | None = None


@activity.defn
def resolve_time_anchor_activity(
    inputs: ResolveTimeAnchorActivityInputs,
) -> datetime:
    """Resolve the time anchor based on trigger type.

    This activity is recorded in workflow history and replayed on reset,
    ensuring the same time anchor is used across workflow resets.

    For scheduled workflows, uses TemporalScheduledStartTime (the intended schedule time).
    For other triggers (webhook, manual, case), uses the workflow start time.
    """
    if inputs.trigger_type == TriggerType.SCHEDULED and inputs.scheduled_start_time:
        return inputs.scheduled_start_time
    return inputs.start_time
