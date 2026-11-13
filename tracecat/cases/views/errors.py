"""HTTP mapping for case view errors, shared by the public and internal routers."""

from typing import NoReturn

from fastapi import HTTPException, status

from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)

CASE_VIEW_ERRORS = (
    TracecatNotFoundError,
    TracecatAuthorizationError,
    TracecatValidationError,
)
"""Errors a case view route turns into a 4xx response."""


def raise_case_view_error(
    exc: TracecatNotFoundError | TracecatAuthorizationError | TracecatValidationError,
) -> NoReturn:
    """Raise the HTTP error for a case view service error."""
    match exc:
        case TracecatNotFoundError():
            code = status.HTTP_404_NOT_FOUND
        case TracecatAuthorizationError():
            code = status.HTTP_403_FORBIDDEN
        case TracecatValidationError():
            code = status.HTTP_400_BAD_REQUEST
    raise HTTPException(code, detail=str(exc)) from exc
