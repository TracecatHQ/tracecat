from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from temporalio.exceptions import ApplicationError


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
        return isinstance(error, ApplicationError) and error.type == cls.ERROR_TYPE
