from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import ORJSONResponse
from pydantic import ValidationError

from tracecat import validation
from tracecat.auth.credentials import authenticate_user_for_workspace
from tracecat.dsl.common import DSLInput
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.logging import logger
from tracecat.types.api import UDFArgsValidationResponse
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError

router = APIRouter(prefix="/validate-workflow")


@router.post("", tags=["validation"])
async def validate_workflow(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    definition: UploadFile = File(...),
    payload: UploadFile = File(None),
) -> list[UDFArgsValidationResponse]:
    """Validate a workflow.

    This deploys the workflow and updates its version. If a YAML file is provided, it will override the workflow in the database."""

    # Committing from YAML (i.e. attaching yaml) will override the workflow definition in the database
    with logger.contextualize(role=role):
        # Perform Tiered Validation
        # Tier 1: DSLInput validation
        # Verify that the workflow DSL is structurally sound
        construction_errors = []
        try:
            # Uploaded YAML file overrides the workflow in the database
            dsl = DSLInput.from_yaml(definition.file)
        except* TracecatValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_dsl_validation_error(e).model_dump(
                    exclude_none=True
                )
                for e in eg.exceptions
            )
        except* ValidationError as eg:
            logger.error(eg.message, error=eg.exceptions)
            construction_errors.extend(
                UDFArgsValidationResponse.from_pydantic_validation_error(e).model_dump(
                    exclude_none=True
                )
                for e in eg.exceptions
            )

        if construction_errors:
            msg = f"Workflow definition construction failed with {len(construction_errors)} errors"
            logger.error(msg)
            return ORJSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "failure",
                    "message": msg,
                    "errors": construction_errors,
                    "metadata": {"filename": definition.filename}
                    if definition
                    else None,
                },
            )

        # When we're here, we've verified that the workflow DSL is structurally sound
        # Now, we have to ensure that the arguments are sound

        expr_errors = await validation.validate_dsl(dsl)
        if expr_errors:
            msg = f"{len(expr_errors)} validation error(s)"
            logger.error(msg)
            return ORJSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "failure",
                    "message": msg,
                    "errors": [
                        UDFArgsValidationResponse.from_validation_result(
                            val_res
                        ).model_dump(exclude_none=True)
                        for val_res in expr_errors
                    ],
                    "metadata": {"filename": definition.filename}
                    if definition
                    else None,
                },
            )

        # Check for input errors
        if payload:
            payload_data = orjson.loads(payload.file.read())
            payload_val_res = validate_trigger_inputs(dsl, payload_data)
            if payload_val_res.status == "error":
                msg = "Trigger input validation error"
                logger.error(msg)
                return ORJSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "status": "failure",
                        "message": msg,
                        "errors": [
                            UDFArgsValidationResponse.from_validation_result(
                                payload_val_res
                            ).model_dump(exclude_none=True)
                        ],
                        "metadata": {"filename": definition.filename},
                    },
                )
        return ORJSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "success", "message": "Workflow passed validation"},
        )
