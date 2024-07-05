import asyncio
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


def apartial(coro: Coroutine[T], /, *bind_args, **bind_kwargs):
    async def wrapped(*args, **kwargs):
        keywords = {**bind_kwargs, **kwargs}
        return await coro(*bind_args, *args, **keywords)

    return wrapped


class GatheringTaskGroup[T](asyncio.TaskGroup):
    """Convenience class to gather results from tasks in a task group."""

    def __init__(self):
        super().__init__()
        self.__tasks: list[asyncio.Task[T]] = []

    def create_task(self, coro, *, name=None, context=None) -> asyncio.Task[T]:
        task = super().create_task(coro, name=name, context=context)
        self.__tasks.append(task)
        return task

    def results(self) -> list[T]:
        return [task.result() for task in self.__tasks]
