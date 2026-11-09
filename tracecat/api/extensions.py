"""Discover installed API extensions that add routers and lifespan tasks."""

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, FastAPI
from starlette.routing import BaseRoute, Match, Route

from tracecat.api.lifespan import LifespanTaskSupervisor, StoppableTaskFactory

API_EXTENSION_ENTRY_POINT_GROUP = "tracecat.api_extensions"


@runtime_checkable
class ApiExtension(Protocol):
    """An installed package's API surface, mounted after the built-in routers."""

    def routers(self) -> Sequence[APIRouter]: ...

    def lifespan_tasks(self) -> Sequence[StoppableTaskFactory]: ...


@lru_cache(maxsize=1)
def load_api_extensions() -> Mapping[str, ApiExtension]:
    """Load installed factories once by entry-point name; a broken one fails startup."""
    extensions: dict[str, ApiExtension] = {}
    installed = sorted(
        entry_points(group=API_EXTENSION_ENTRY_POINT_GROUP), key=lambda ep: ep.name
    )
    for entry in installed:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", entry.name):
            raise ValueError(f"Invalid API extension identifier: {entry.name}")
        if entry.name in extensions:
            raise ValueError(f"Duplicate API extension: {entry.name}")
        factory = entry.load()
        if not callable(factory):
            raise TypeError(f"API extension {entry.name} must expose a factory")
        extension = factory()
        if not isinstance(extension, ApiExtension):
            raise TypeError(f"Invalid API extension contract: {entry.name}")
        extensions[entry.name] = extension
    return MappingProxyType(extensions)


def _probe_path(path: str) -> str:
    """Fill path params with a value their converter accepts."""
    return re.sub(
        r"{[^}:]+(?::([a-z]+))?}",
        lambda m: "0" if m.group(1) in ("int", "float") else "x",
        path,
    )


def _is_shadowed(earlier: Sequence[BaseRoute], route: Route) -> bool:
    """Whether an earlier route would answer first for a generic request to this route."""
    path = _probe_path(route.path)
    for method in route.methods or ("GET",):
        scope = {"type": "http", "path": path, "method": method, "root_path": ""}
        if any(r.matches(scope)[0] is Match.FULL for r in earlier):
            return True
    return False


def include_api_extension_routers(app: FastAPI) -> None:
    """Mount every installed extension's routers; refuse routes another route shadows."""
    for name, extension in load_api_extensions().items():
        for router in extension.routers():
            checked: list[BaseRoute] = []
            for route in router.routes:
                # Nested routers hide their routes from the shadow check.
                if not isinstance(route, Route):
                    raise TypeError(
                        f"API extension {name}: only HTTP routes are supported;"
                        " return each router from routers() instead of nesting"
                    )
                # Earlier routes of this router are not mounted yet, so check them too.
                if _is_shadowed([*app.router.routes, *checked], route):
                    raise ValueError(
                        f"API extension {name}: {route.path} is already served"
                    )
                checked.append(route)
            app.include_router(router)


def spawn_api_extension_tasks(supervisor: LifespanTaskSupervisor) -> None:
    """Spawn every installed extension's tasks under the lifespan supervisor."""
    for name, extension in load_api_extensions().items():
        for index, factory in enumerate(extension.lifespan_tasks()):
            supervisor.spawn_stoppable(factory, name=f"api_extension:{name}:{index}")
