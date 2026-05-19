"""Registry SDK facade for run_python scripts.

This module is intentionally shaped for inline scripts:

    from tracecat_registry import ctx

    def main():
        return ctx.cases.list_cases(limit=10)

    async def main():
        return await ctx.cases.aio.list_cases(limit=10)

The underlying SDK remains async. This facade resolves the current
RegistryContext at call time and runs awaitable SDK methods to completion when
called from synchronous code. Async callers should use the `.aio` client
namespace.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from tracecat_registry.context import get_context


T = TypeVar("T")

_CONTEXT_ATTRS = {
    "workspace_id",
    "workflow_id",
    "run_id",
    "wf_exec_id",
    "environment",
    "api_url",
    "executor_url",
    "token",
    "client",
}


async def _await_value(awaitable: Awaitable[T]) -> T:
    return await awaitable


def _is_in_running_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_awaitable(awaitable: Awaitable[T]) -> T:
    import asyncio

    return asyncio.run(_await_value(awaitable))


class _SyncCallable:
    def __init__(self, func: Callable[..., Any]) -> None:
        self._func = func

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self._func(*args, **kwargs)
        if inspect.isawaitable(result):
            if _is_in_running_loop():
                if inspect.iscoroutine(result):
                    result.close()
                raise RuntimeError(
                    "Cannot call a synchronous tracecat_registry.ctx SDK method "
                    "from async code. Use the client `.aio` namespace, for example "
                    "`await ctx.cases.aio.list_cases(...)`."
                )
            return _run_awaitable(result)
        return result


class _SyncClientProxy:
    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    @property
    def aio(self) -> _AsyncClientProxy:
        return _AsyncClientProxy(self._getter)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._getter(), name)
        if callable(attr):
            return _SyncCallable(attr)
        return attr


class _AsyncClientProxy:
    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)


cases = _SyncClientProxy(lambda: get_context().cases)
agents = _SyncClientProxy(lambda: get_context().agents)
deduplicate = _SyncClientProxy(lambda: get_context().deduplicate)
tables = _SyncClientProxy(lambda: get_context().tables)
variables = _SyncClientProxy(lambda: get_context().variables)
workflows = _SyncClientProxy(lambda: get_context().workflows)


def __getattr__(name: str) -> Any:
    if name in _CONTEXT_ATTRS:
        return getattr(get_context(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["agents", "cases", "deduplicate", "tables", "variables", "workflows"]
