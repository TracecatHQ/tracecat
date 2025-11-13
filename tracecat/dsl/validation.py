from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from tracecat.dsl.common import DSLInput
from tracecat.dsl.schemas import TriggerInputs
from tracecat.expressions.expectations import (
    ExpectedField,
    create_expectation_model,
)
from tracecat.logger import logger
from tracecat.validation.schemas import DSLValidationResult, ValidationDetail


def validate_trigger_inputs(
    dsl: DSLInput,
    payload: TriggerInputs | None = None,
    *,
    raise_exceptions: bool = False,
    model_name: str = "TriggerInputsValidator",
) -> DSLValidationResult:
    if not dsl.entrypoint.expects:
        # If there's no expected trigger input schema, we don't validate it
        # as its ignored anyways
        return DSLValidationResult(
            status="success", msg="No trigger input schema, skipping validation."
        )
    logger.trace(
        "DSL entrypoint expects", expects=dsl.entrypoint.expects, payload=payload
    )

    expects_schema = {
        field_name: ExpectedField.model_validate(field_schema)
        for field_name, field_schema in dsl.entrypoint.expects.items()
    }
    if isinstance(payload, dict):
        # NOTE: We only validate dict payloads for now
        validator = create_expectation_model(expects_schema, model_name=model_name)
        try:
            validator(**payload)
        except ValidationError as e:
            if raise_exceptions:
                raise
            return DSLValidationResult(
                status="error",
                msg=f"Validation error in trigger inputs ({e.title}). Please refer to the schema for more details.",
                detail=ValidationDetail.list_from_pydantic(e),
            )
    return DSLValidationResult(status="success", msg="Trigger inputs are valid.")


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


class ValidateTriggerInputsActivityInputs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    dsl: DSLInput
    trigger_inputs: TriggerInputs


@activity.defn
async def validate_trigger_inputs_activity(
    inputs: ValidateTriggerInputsActivityInputs,
) -> DSLValidationResult:
    res = validate_trigger_inputs(
        inputs.dsl, inputs.trigger_inputs, raise_exceptions=True
    )
    return res


class NormalizeTriggerInputsActivityInputs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    input_schema: dict[str, ExpectedField]
    trigger_inputs: TriggerInputs


@activity.defn
def normalize_trigger_inputs_activity(
    inputs: NormalizeTriggerInputsActivityInputs,
) -> TriggerInputs:
    """Return trigger inputs with defaults applied according to DSL expects."""
    try:
        return normalize_trigger_inputs(inputs.input_schema, inputs.trigger_inputs)
    except ValidationError as e:
        logger.info("Validation error when normalizing trigger inputs", error=e)
        raise ApplicationError(
            "Failed to validate trigger inputs",
            ValidationDetail.list_from_pydantic(e),
            non_retryable=True,
            type=e.__class__.__name__,
        ) from e
    except Exception as e:
        logger.warning(
            "Unexpected error cause when normalizing trigger inputs",
            error=e,
        )
        raise ApplicationError(
            "Unexpected error when normalizing trigger inputs",
            non_retryable=True,
            type=e.__class__.__name__,
        ) from e


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
    "pydantic.extra_forbidden": lambda loc: f"The attribute '{'.'.join(str(s) for s in loc)}' is not allowed in the input schema.",
    "pydantic.missing": lambda loc: f"Missing required field(s): '{'.'.join(str(s) for s in loc)}'.",
    "pydantic.invalid_type": lambda loc: f"Invalid type at '{'.'.join(str(s) for s in loc)}'.",
}
