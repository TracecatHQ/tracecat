from tenacity import retry as tenacity_retry
from tenacity import stop_after_attempt, wait_exponential
from tracecat.config import (
    RETRY_EXPONENTIAL_MULTIPLIER,
    RETRY_MAX_WAIT_TIME,
    RETRY_MIN_WAIT_TIME,
    RETRY_STOP_AFTER_ATTEMPT,
)


def retry(
    max_attempts: int = RETRY_STOP_AFTER_ATTEMPT,
    exponential_multiplier: int = RETRY_EXPONENTIAL_MULTIPLIER,
    min_wait: int = RETRY_MIN_WAIT_TIME,
    max_wait: int = RETRY_MAX_WAIT_TIME,
    **kwargs,
):
    return tenacity_retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(
            multiplier=exponential_multiplier, min=min_wait, max=max_wait
        ),
        **kwargs,
    )
