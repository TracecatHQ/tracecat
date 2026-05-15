from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from temporalio.exceptions import ApplicationError

from tracecat.runtime.errors import RuntimeErrorEnvelope, RuntimeErrorKind
from tracecat.temporal.errors import extract_runtime_error


class UserError(ApplicationError):
    """Temporal application error for user-attributable workflow failures."""

    ERROR_TYPE: ClassVar[str] = "UserError"

    def __init__(
        self,
        message: str,
        *details: Any,
        non_retryable: bool = True,
        next_retry_delay: timedelta | None = None,
    ) -> None:
        super().__init__(
            message,
            *details,
            type=self.ERROR_TYPE,
            non_retryable=non_retryable,
            next_retry_delay=next_retry_delay,
        )

    def __str__(self) -> str:
        return self.message or super().__str__()

    @classmethod
    def matches(cls, error: BaseException) -> bool:
        match extract_runtime_error(error):
            case RuntimeErrorEnvelope(kind=RuntimeErrorKind.USER):
                return True
        match error:
            case ApplicationError(type=error_type) if error_type == cls.ERROR_TYPE:
                return True
            case _:
                return False
