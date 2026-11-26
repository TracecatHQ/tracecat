"""Errors for the trigger API."""

from fastapi import Request, Response, status
from fastapi.responses import ORJSONResponse

from tracecat.exceptions import TracecatException
from tracecat.triggers.enums import TriggerErrorCode

_STATUS_BY_CODE: dict[TriggerErrorCode, int] = {
    TriggerErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    TriggerErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
    TriggerErrorCode.INVALID: status.HTTP_400_BAD_REQUEST,
}


class TriggerError(TracecatException):
    """A trigger request that cannot be served. ``code`` selects the response."""

    def __init__(self, code: TriggerErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


async def trigger_error_handler(request: Request, exc: Exception) -> Response:
    """Answer a ``TriggerError`` with its status, code and message."""
    if not isinstance(exc, TriggerError):
        raise exc
    return ORJSONResponse(
        status_code=_STATUS_BY_CODE[exc.code],
        content={"detail": {"code": exc.code.value, "message": str(exc)}},
    )
