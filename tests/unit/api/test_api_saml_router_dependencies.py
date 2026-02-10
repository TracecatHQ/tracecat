"""Route-level dependency tests for SAML auth endpoints."""

from __future__ import annotations

from fastapi.routing import APIRoute

from tracecat.api.app import app


def _get_route(path: str, method: str) -> APIRoute:
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f"Route not found: {method} {path}")


def _has_auth_type_dependency(route: APIRoute) -> bool:
    return any(
        getattr(dependency.call, "__name__", "") == "_check_auth_type_enabled"
        for dependency in route.dependant.dependencies
    )


def test_saml_login_route_requires_saml_auth_type_check() -> None:
    route = _get_route("/auth/saml/login", "GET")
    assert _has_auth_type_dependency(route)


def test_saml_acs_route_skips_pre_auth_type_check_dependency() -> None:
    route = _get_route("/auth/saml/acs", "POST")
    assert not _has_auth_type_dependency(route)
