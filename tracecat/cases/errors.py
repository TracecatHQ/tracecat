"""HTTP mapping for case search and case view errors, shared by their routers."""

from fastapi import HTTPException
from starlette.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
)

from tracecat.cases.search_adapters import CurrentUserRequiredError
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

type CaseRequestError = ValueError | TracecatValidationError | TracecatNotFoundError

CASE_REQUEST_ERRORS = (ValueError, TracecatValidationError, TracecatNotFoundError)
"""Errors a case search or case view route turns into a 4xx response.

Authorization errors are left to the app's handler, which returns a fixed
403 body so denial messages never reach the client.
"""


def case_http_error(exc: CaseRequestError) -> HTTPException:
    """Map a rejected case search or view request to an HTTP error.

    A missing or hidden view is a 404. Other errors are 400s; typed ones keep
    a machine-readable ``code`` so clients need not parse the message.
    """
    if isinstance(exc, TracecatNotFoundError):
        return HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, CurrentUserRequiredError):
        return HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc))
