import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any, TypeVar, override

import cloudpickle

from tracecat.logger import logger

T = TypeVar("T")


def apartial[T](coro: Callable[..., Awaitable[T]], /, *bind_args, **bind_kwargs):
    async def wrapped(*args, **kwargs):
        keywords = {**bind_kwargs, **kwargs}
        return await coro(*bind_args, *args, **keywords)

    return wrapped


class GatheringTaskGroup[T: Any](asyncio.TaskGroup):
    """Convenience class to gather results from tasks in a task group."""

    def __init__(self):
        super().__init__()
        self.__tasks: list[asyncio.Task[T]] = []

    def create_task(
        self, coro, *, name: str | None = None, context: Any | None = None
    ) -> asyncio.Task[T]:
        task = super().create_task(coro, name=name, context=context)
        self.__tasks.append(task)
        return task

    def results(self) -> list[T]:
        return [task.result() for task in self.__tasks]


def _run_serialized_fn(ser_fn: bytes, /, *args: Any, **kwargs: Any) -> Any:
    # NOTE: This is the raw function
    fn: Callable[..., Any] = cloudpickle.loads(ser_fn)
    udf_args, udf_ctx, *_ = args
    logger.debug(
        "Deserializing function",
        args=args,
        kwargs=kwargs,
        udf_args=udf_args,
        udf_ctx=udf_ctx,
    )

    res = fn(**udf_args)
    return res


class CloudpickleProcessPoolExecutor(ProcessPoolExecutor):
    @override
    def submit(
        self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
    ) -> Future[Any]:
        # We need to pass the role to the function running in the child process
        logger.info("Serializing function")
        ser_fn = cloudpickle.dumps(fn)
        return super().submit(_run_serialized_fn, ser_fn, *args, **kwargs)


async def cooperative[T](
    it: Iterable[T], *, delay: float = 0
) -> AsyncGenerator[T, None]:
    """Yield items from an iterable in a cooperative manner.

    This is useful for yielding back to the event loop to avoid Temporal deadlock.

    Args:
        it: The iterable to yield items from.
        duration: The duration to sleep between yielding items.
    """
    for item in it:
        yield item
        await asyncio.sleep(delay)
