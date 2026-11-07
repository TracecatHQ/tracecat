"""API extension discovery, router mounting, and lifespan task supervision."""

import asyncio
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tracecat.api import extensions
from tracecat.api.lifespan import LifespanTaskSupervisor, StoppableTaskFactory


@pytest.fixture(autouse=True)
def clean_extension_cache() -> Iterator[None]:
    extensions.load_api_extensions.cache_clear()
    yield
    extensions.load_api_extensions.cache_clear()


def entry(name: str, obj: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=Mock(return_value=obj))


class FakeExtension:
    def __init__(self, *paths: str, method: str = "GET") -> None:
        self.stopped = asyncio.Event()
        self._router = APIRouter()
        for path in paths:
            self._router.add_api_route(path, lambda: {"ok": True}, methods=[method])

    def routers(self) -> Sequence[APIRouter]:
        return [self._router]

    def lifespan_tasks(self) -> Sequence[StoppableTaskFactory]:
        return [self._consume]

    async def _consume(self, stop: asyncio.Event) -> None:
        await stop.wait()
        self.stopped.set()


def installed(monkeypatch: pytest.MonkeyPatch, *entries: SimpleNamespace) -> None:
    monkeypatch.setattr(extensions, "entry_points", Mock(return_value=list(entries)))


@pytest.mark.anyio
async def test_no_entry_points_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    installed(monkeypatch)
    app = FastAPI()
    supervisor = LifespanTaskSupervisor(drain_timeout=1.0)

    extensions.include_api_extension_routers(app)
    extensions.spawn_api_extension_tasks(supervisor)

    assert dict(extensions.load_api_extensions()) == {}
    assert app.openapi()["paths"] == {}
    await supervisor.drain()


@pytest.mark.anyio
async def test_installed_extensions_mount_routers_and_spawn_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beta = FakeExtension("/beta")
    alpha = FakeExtension("/alpha")
    installed(monkeypatch, entry("beta", lambda: beta), entry("alpha", lambda: alpha))
    app = FastAPI()
    supervisor = LifespanTaskSupervisor(drain_timeout=1.0)

    extensions.include_api_extension_routers(app)
    extensions.spawn_api_extension_tasks(supervisor)

    assert list(extensions.load_api_extensions().items()) == [
        ("alpha", alpha),
        ("beta", beta),
    ]
    client = TestClient(app)
    assert client.get("/alpha").json() == {"ok": True}
    assert client.get("/beta").json() == {"ok": True}
    await supervisor.drain()
    assert alpha.stopped.is_set()
    assert beta.stopped.is_set()


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        ([entry("x", object())], TypeError),
        ([entry("x", lambda: object())], TypeError),
        ([entry("Bad-Name", lambda: FakeExtension("/x"))], ValueError),
        (
            [
                entry("dup", lambda: FakeExtension("/a")),
                entry("dup", lambda: FakeExtension("/b")),
            ],
            ValueError,
        ),
    ],
    ids=["not_a_factory", "not_an_extension", "invalid_name", "duplicate_name"],
)
def test_invalid_extensions_fail_startup(
    monkeypatch: pytest.MonkeyPatch,
    entries: list[SimpleNamespace],
    error: type[Exception],
) -> None:
    installed(monkeypatch, *entries)
    with pytest.raises(error):
        extensions.load_api_extensions()


def builtin_app() -> FastAPI:
    # Mirrors app.py: built-ins arrive through include_router, not flat routes.
    builtin = APIRouter(prefix="/agent/channels")
    builtin.add_api_route("/{channel_type}/{token}", lambda: None, methods=["POST"])
    app = FastAPI()
    app.include_router(builtin)
    return app


@pytest.mark.parametrize(
    "path",
    ["/agent/channels/slack/events", "/agent/channels/{provider}/events"],
    ids=["literal", "parameterized"],
)
def test_route_shadowed_by_builtin_fails_startup(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    app = builtin_app()
    installed(monkeypatch, entry("ee", lambda: FakeExtension(path, method="POST")))
    with pytest.raises(ValueError, match="is already served"):
        extensions.include_api_extension_routers(app)


def test_route_with_other_method_is_not_shadowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = builtin_app()
    installed(monkeypatch, entry("ee", lambda: FakeExtension("/agent/channels/a/b")))
    extensions.include_api_extension_routers(app)
    assert TestClient(app).get("/agent/channels/a/b").json() == {"ok": True}


def test_nested_extension_router_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extension = FakeExtension()
    inner = APIRouter(prefix="/ee")
    inner.add_api_route("/ping", lambda: None, methods=["GET"])
    extension.routers()[0].include_router(inner)
    installed(monkeypatch, entry("ee", lambda: extension))
    with pytest.raises(TypeError, match="only HTTP routes"):
        extensions.include_api_extension_routers(FastAPI())
