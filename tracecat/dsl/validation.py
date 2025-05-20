from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError
from temporalio import activity

from tracecat.dsl.common import DSLInput
from tracecat.dsl.models import TriggerInputs
from tracecat.expressions.expectations import ExpectedField, create_expectation_model
from tracecat.logger import logger
from tracecat.validation.models import (
    DSLValidationResult,
    ValidationDetail,
    ValidationResult,
)


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


class ValidateTriggerInputsActivityInputs(BaseModel):
    model_config: ConfigDict = ConfigDict(arbitrary_types_allowed=True)
    dsl: DSLInput
    trigger_inputs: TriggerInputs


@activity.defn
async def validate_trigger_inputs_activity(
    inputs: ValidateTriggerInputsActivityInputs,
) -> ValidationResult:
    res = validate_trigger_inputs(
        inputs.dsl, inputs.trigger_inputs, raise_exceptions=True
    )
    return ValidationResult.new(res)
