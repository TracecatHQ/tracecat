import asyncio
import contextlib
import contextvars
import os
from collections.abc import Callable, Coroutine, MutableMapping
from concurrent.futures import Future, ProcessPoolExecutor
from copy import deepcopy
from typing import Any, TypeVar, override

import cloudpickle

from tracecat.logger import logger

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


F = TypeVar("F", bound=Callable[..., Any])


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
    T = TypeVar("T")

    @override
    def submit(self, fn: F, /, *args: Any, **kwargs: Any) -> Future[T]:
        # We need to pass the role to the function running in the child process
        logger.info("Serializing function")
        ser_fn = cloudpickle.dumps(fn)
        return super().submit(_run_serialized_fn, ser_fn, *args, **kwargs)


class AsyncAwareEnviron(MutableMapping):
    """An environment variable interceptor that is aware of async context."""

    @contextlib.contextmanager
    @staticmethod
    def sandbox():
        """Isolate environment variables created in coroutines for the duration of the context manager.

        Warning
        -------
        - This is an experimental feature (unstable)
        - It appears to be threadsafe, but for safety we highly recommended that you do only
        execute code that only reads/writes to the environment variable in the context manager.
        """
        prev = deepcopy(os.environ)
        try:
            os.environ = AsyncAwareEnviron(os.environ)  # noqa: B003
            yield
        except Exception:
            raise
        finally:
            os.environ = prev  # noqa: B003

    def __init__(self, global_env: os._Environ):
        self._env: os._Environ = global_env
        self._local_env = contextvars.ContextVar("local_env", default={})

    def __repr__(self):
        local_formatted_items = ", ".join(
            f"{key!r}: {value!r}" for key, value in self._local_env.get().items()
        )
        return f"{self._env!r}\nlocal_environ({{{local_formatted_items}}})"

    def __getitem__(self, key: str) -> str:
        local_env = self._local_env.get()
        return local_env.get(key, self._env.get(key))

    def __setitem__(self, key: str, value: str) -> None:
        local_env = self._local_env.get().copy()
        local_env[key] = value
        self._local_env.set(local_env)

    def __delitem__(self, key: str) -> None:
        local_env = self._local_env.get().copy()
        if key in local_env:
            del local_env[key]
            self._local_env.set(local_env)
        elif key in self._env:
            del self._env[key]

    def __contains__(self, key: str) -> bool:
        local_env = self._local_env.get()
        return key in local_env or key in self._env

    def __iter__(self):
        local_env = self._local_env.get()
        return iter({**self._env, **local_env})

    def __len__(self) -> int:
        local_env = self._local_env.get()
        return len({**self._env, **local_env})

    @property
    def local(self) -> MutableMapping[str, str]:
        return self._local_env.get()

    def get(self, key: str, default=None) -> str:
        local_env = self._local_env.get()
        return local_env.get(key, self._env.get(key, default))

    def setdefault(self, key: str, default: str = None) -> str:
        """Set an environment variable if it's not already set, like os.environ."""
        local_env = self._local_env.get().copy()
        if key not in local_env:
            local_env[key] = default
            self._local_env.set(local_env)
        return local_env[key]

    def update(self, other=None, **kwargs) -> None:
        """Update the environment with another dictionary or keyword arguments."""
        local_env = self._local_env.get().copy()
        if other:
            local_env.update(other)
        if kwargs:
            local_env.update(kwargs)
        self._local_env.set(local_env)

    def pop(self, key: str, default=None) -> str:
        """Remove a key and return its value, like os.environ.pop()."""
        local_env = self._local_env.get().copy()
        value = local_env.pop(key, default)
        self._local_env.set(local_env)
        return value

    def clear(self) -> None:
        """Clear all environment variables in the local context, like os.environ.clear()."""
        self._local_env.set({})

    def keys(self):
        """Return a list of all keys in the environment, like os.environ.keys()."""
        local_env = self._local_env.get()
        return {**self._env, **local_env}.keys()

    def values(self):
        """Return a list of all values in the environment, like os.environ.values()."""
        local_env = self._local_env.get()
        return {**self._env, **local_env}.values()

    def items(self):
        """Return a list of all key-value pairs in the environment, like os.environ.items()."""
        local_env = self._local_env.get()
        return {**self._env, **local_env}.items()

    def copy(self):
        """Return a shallow copy of the environment, like os.environ.copy()."""
        local_env = self._local_env.get()
        return {**self._env, **local_env}.copy()
