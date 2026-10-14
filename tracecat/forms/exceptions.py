"""Typed errors for forms and their HTTP responses."""

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from tracecat.exceptions import TracecatException
from tracecat.forms.enums import FormErrorCode
from tracecat.forms.schemas import FormErrorDetail, FormFieldError, FormIssue

_STATUS_BY_CODE: dict[FormErrorCode, int] = {
    FormErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    FormErrorCode.ALREADY_EXISTS: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_CONFIG_INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormErrorCode.FORM_WORKFLOW_UNPUBLISHED: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_OFFLINE: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_UNAVAILABLE: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_CHANGED: status.HTTP_409_CONFLICT,
    FormErrorCode.FORM_VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormErrorCode.WORKFLOW_START_FAILED: status.HTTP_503_SERVICE_UNAVAILABLE,
    FormErrorCode.FILE_FIELD_INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    FormErrorCode.FILE_INVALID: status.HTTP_400_BAD_REQUEST,
    FormErrorCode.FILE_TYPE_NOT_ALLOWED: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FormErrorCode.FILE_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    FormErrorCode.FILE_LIMIT_REACHED: status.HTTP_409_CONFLICT,
    FormErrorCode.FILE_UPLOAD_FAILED: status.HTTP_503_SERVICE_UNAVAILABLE,
}


class FormError(TracecatException):
    """A forms request that cannot be served. ``code`` selects the response."""

    def __init__(
        self,
        code: FormErrorCode,
        message: str,
        *,
        errors: list[FormFieldError] | None = None,
        issues: list[FormIssue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.errors = errors
        self.issues = issues


async def form_error_handler(request: Request, exc: Exception) -> Response:
    """Answer a ``FormError`` with its status and a ``FormErrorDetail`` body."""
    if not isinstance(exc, FormError):
        raise exc
    detail = FormErrorDetail(
        code=exc.code, message=str(exc), errors=exc.errors, issues=exc.issues
    )
    return JSONResponse(
        status_code=_STATUS_BY_CODE[exc.code],
        content={"detail": detail.model_dump(mode="json")},
    )
