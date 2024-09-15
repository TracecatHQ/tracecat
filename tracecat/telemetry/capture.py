import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from tracecat.logger import logger
from tracecat.telemetry.events import ErrorEvent, FeatureUsageEvent
from tracecat.telemetry.posthog import telemetry_client

F = TypeVar("F", bound=Callable[..., Any])


def telemetry_event(event_name: str):
    def decorator(func: F) -> F:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            user_id = kwargs.get("user_id", "unknown_user")
            try:
                result = await func(*args, **kwargs)
                try:
                    telemetry_client.capture(
                        FeatureUsageEvent(user_id=user_id, feature=event_name)
                    )
                except Exception as e:
                    logger.error(f"Error in telemetry event logging: {str(e)}")
                return result
            except Exception as e:
                try:
                    telemetry_client.capture(
                        ErrorEvent(
                            user_id=user_id,
                            endpoint=event_name,
                            error_message=str(e),
                        )
                    )
                except Exception as e:
                    logger.error(f"Error in telemetry event logging: {str(e)}")

                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            loop = asyncio.get_event_loop()
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    async_wrapper(*args, **kwargs), loop
                )
                return future.result()
            else:
                return loop.run_until_complete(async_wrapper(*args, **kwargs))

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
