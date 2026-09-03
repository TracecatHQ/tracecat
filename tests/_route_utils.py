"""Helpers for enumerating effective API routes on a mounted FastAPI app.

As of FastAPI 0.137 / Starlette 1.x, ``app.include_router`` no longer copies
child routes into the parent ``app.routes`` list. Instead it appends lazy
``fastapi.routing._IncludedRouter`` nodes, and the effective routes (with their
prefixed paths and merged dependencies) are computed on demand.

These helpers flatten an app's routes back into the per-endpoint view that
tests expect: one entry per ``APIRoute`` with its effective ``path``,
``methods``, ``name``, ``endpoint``, ``include_in_schema`` flag, and merged
``dependant``. Iterating ``app.routes`` directly only sees the top-level
Starlette routes and the opaque ``_IncludedRouter`` nodes, so tests must go
through here to observe routes contributed via ``include_router``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute, _iter_routes_with_context


@dataclass(frozen=True)
class EffectiveRoute:
    """An effective view of a single mounted ``APIRoute``.

    Attributes mirror the ``APIRoute`` fields tests rely on, but reflect the
    prefixing and dependency merging applied by ``include_router``.
    """

    path: str
    name: str
    methods: frozenset[str]
    endpoint: Callable[..., Any] | None
    include_in_schema: bool
    dependant: Any
    original_route: APIRoute


def iter_effective_api_routes(app: FastAPI) -> list[EffectiveRoute]:
    """Return the effective ``APIRoute`` entries mounted on ``app``.

    Routes mounted via ``include_router`` are expanded to their prefixed paths
    and merged dependencies. Non-``APIRoute`` routes (e.g. the Starlette routes
    backing ``/openapi.json`` or ``/docs``) are skipped, matching the previous
    ``isinstance(route, APIRoute)`` filter used by callers.
    """

    effective: list[EffectiveRoute] = []
    for route, context in _iter_routes_with_context(app.routes):
        if context is not None:
            if not isinstance(context.original_route, APIRoute):
                continue
            effective.append(
                EffectiveRoute(
                    path=context.path,
                    name=context.name,
                    methods=frozenset(context.methods or ()),
                    endpoint=context.endpoint,
                    include_in_schema=context.include_in_schema,
                    dependant=context.dependant,
                    original_route=context.original_route,
                )
            )
        elif isinstance(route, APIRoute):
            effective.append(
                EffectiveRoute(
                    path=route.path,
                    name=route.name,
                    methods=frozenset(route.methods or ()),
                    endpoint=route.endpoint,
                    include_in_schema=route.include_in_schema,
                    dependant=route.dependant,
                    original_route=route,
                )
            )
    return effective
