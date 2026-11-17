from __future__ import annotations

import ast
import asyncio
import signal
from collections.abc import Callable
from pathlib import Path

import pytest

import tracecat.temporal.worker_lifecycle as worker_lifecycle
from tracecat.temporal.worker_lifecycle import (
    install_worker_shutdown_signal_handlers,
    run_worker_entrypoint,
)


class FakeSignalHandlingLoop:
    def __init__(self) -> None:
        self.handlers: dict[
            signal.Signals, tuple[Callable[..., object], tuple[object, ...]]
        ] = {}
        self.removed: list[signal.Signals] = []

    def add_signal_handler(
        self, sig: int, callback: Callable[..., object], *args: object
    ) -> None:
        self.handlers[signal.Signals(sig)] = (callback, args)

    def remove_signal_handler(self, sig: int) -> bool:
        self.removed.append(signal.Signals(sig))
        return True


@pytest.mark.anyio
async def test_worker_shutdown_signal_handler_wakes_event_from_loop_callback() -> None:
    shutdown_event = asyncio.Event()
    loop = FakeSignalHandlingLoop()

    with install_worker_shutdown_signal_handlers(shutdown_event, loop=loop):
        callback, args = loop.handlers[signal.SIGTERM]
        callback(*args)

        assert shutdown_event.is_set()

    assert loop.removed == [signal.SIGINT, signal.SIGTERM]


@pytest.mark.anyio
async def test_worker_shutdown_signal_handler_respects_custom_signals() -> None:
    shutdown_event = asyncio.Event()
    loop = FakeSignalHandlingLoop()

    with install_worker_shutdown_signal_handlers(
        shutdown_event, loop=loop, signals=(signal.SIGTERM,)
    ):
        assert list(loop.handlers) == [signal.SIGTERM]

    assert loop.removed == [signal.SIGTERM]


def test_run_worker_entrypoint_runs_main_with_shutdown_event() -> None:
    called = False

    async def main(shutdown_event: asyncio.Event) -> None:
        nonlocal called
        called = True
        shutdown_event.set()

    run_worker_entrypoint(main)

    assert called


def _is_uvicorn_server(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Server"
        and isinstance(node.value, ast.Name)
        and node.value.id == "uvicorn"
    )


def test_embedded_uvicorn_servers_leave_signals_to_the_worker() -> None:
    """A bare ``uvicorn.Server`` closes its socket on SIGTERM, mid worker drain."""
    repo_root = Path(__file__).resolve().parents[2]
    allowed = Path(worker_lifecycle.__file__).resolve()
    sources = [
        *(repo_root / "tracecat").rglob("*.py"),
        *(
            path
            for path in (repo_root / "packages").rglob("*.py")
            if "tests" not in path.parts
        ),
    ]

    violations: list[str] = []
    for path in sources:
        if path.resolve() == allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            match node:
                case ast.Call(func=func) if _is_uvicorn_server(func):
                    pass
                case ast.ClassDef(bases=bases) if any(map(_is_uvicorn_server, bases)):
                    pass
                case ast.ImportFrom(
                    module="uvicorn" | "uvicorn.server", names=names
                ) if any(alias.name == "Server" for alias in names):
                    pass
                case _:
                    continue
            violations.append(f"{path.relative_to(repo_root)}:{node.lineno}")

    assert not violations, (
        "Use WorkerOwnedUvicornServer for uvicorn servers embedded in workers: "
        + ", ".join(violations)
    )
