"""SDK exceptions for Tracecat API errors."""

from __future__ import annotations


class TracecatSDKError(Exception):
    """Base exception for all Tracecat SDK errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class TracecatAPIError(TracecatSDKError):
    """Exception raised when the API returns an error response."""

    def __init__(
        self,
        message: str,
        status_code: int,
        detail: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message} (status={self.status_code}): {self.detail}"
        return f"{self.message} (status={self.status_code})"


class TracecatAuthError(TracecatAPIError):
    """Exception raised for authentication/authorization errors (401, 403)."""

    def __init__(self, detail: str | None = None, status_code: int = 401) -> None:
        super().__init__(
            message="Authentication failed",
            status_code=status_code,
            detail=detail,
        )


class TracecatNotFoundError(TracecatAPIError):
    """Exception raised when a resource is not found (404)."""

    def __init__(self, resource: str, identifier: str | None = None) -> None:
        detail = f"{resource} not found"
        if identifier:
            detail = f"{resource} '{identifier}' not found"
        super().__init__(
            message="Resource not found",
            status_code=404,
            detail=detail,
        )


class TracecatValidationError(TracecatAPIError):
    """Exception raised for validation errors (400, 422)."""

    def __init__(self, detail: str | None = None, status_code: int = 400) -> None:
        super().__init__(
            message="Validation error",
            status_code=status_code,
            detail=detail,
        )


class TracecatConflictError(TracecatAPIError):
    """Exception raised for conflict errors (409)."""

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            message="Conflict",
            status_code=409,
            detail=detail,
        )
