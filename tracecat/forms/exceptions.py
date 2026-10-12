"""Typed errors for forms."""

from tracecat.exceptions import TracecatException
from tracecat.forms.enums import FormErrorCode
from tracecat.forms.schemas import FormFieldError, FormIssue


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
