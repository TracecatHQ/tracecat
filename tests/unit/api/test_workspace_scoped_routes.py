from typing import get_type_hints

from fastapi.routing import APIRoute

from tracecat.api.app import app
from tracecat.auth.dependencies import (
    WorkspaceActorRouteRole,
    WorkspaceUserRouteRole,
    require_workspace_id_path,
)

WORKSPACE_ROUTE_PREFIXES = [
    "/actions",
    "/agent/channels/tokens",
    "/agent/presets",
    "/agent/sessions",
    "/agent/skills",
    "/agent/workspace/providers/status",
    "/approvals",
    "/case-dropdowns",
    "/case-durations",
    "/case-fields",
    "/case-tags",
    "/cases",
    "/editor",
    "/folders",
    "/inbox",
    "/integrations",
    "/mcp-integrations",
    "/providers",
    "/schedules",
    "/secrets",
    "/tables",
    "/tags",
    "/variables",
    "/workflow-executions",
    "/workflows",
]

WORKSPACE_ROUTE_ROLE_TYPES = (WorkspaceActorRouteRole, WorkspaceUserRouteRole)
WORKSPACE_ROUTE_AUTH_EXEMPTIONS = {
    ("/workspaces/{workspace_id}/editor/field-schema", frozenset({"GET"})),
}
OPENAPI_LEGACY_EXCEPTIONS = {
    "/integrations": {"/integrations/callback"},
}


def _is_workspace_scoped_public_route(path: str) -> bool:
    if not path.startswith("/workspaces/{workspace_id}"):
        return False
    suffix = path.removeprefix("/workspaces/{workspace_id}")
    return any(
        suffix == prefix or suffix.startswith(f"{prefix}/")
        for prefix in WORKSPACE_ROUTE_PREFIXES
    )


def _route_key(route: APIRoute) -> tuple[str, frozenset[str]]:
    return route.path, frozenset(route.methods or ())


def _legacy_route_key(route: APIRoute) -> tuple[str, frozenset[str]]:
    return (
        route.path.removeprefix("/workspaces/{workspace_id}"),
        frozenset(route.methods or ()),
    )


def _uses_route_workspace_role_dependency(route: APIRoute) -> bool:
    return any(
        getattr(dependency.call, "__name__", "").startswith(
            "role_dependency_req_ws_auto"
        )
        for dependency in route.dependant.dependencies
    )


def _has_workspace_path_dependency(route: APIRoute) -> bool:
    return any(
        dependency.call is require_workspace_id_path
        for dependency in route.dependant.dependencies
    )


def _workspace_role_hint(route: APIRoute):
    hints = get_type_hints(route.endpoint, include_extras=True)
    return hints.get("role") or hints.get("_role")


def _is_workspace_route_role(role_hint) -> bool:
    return any(role_hint == route_role for route_role in WORKSPACE_ROUTE_ROLE_TYPES)


def test_workspace_scoped_public_routes_are_canonical_in_openapi() -> None:
    app.openapi_schema = None
    paths = set(app.openapi()["paths"])

    for prefix in WORKSPACE_ROUTE_PREFIXES:
        legacy_paths = [
            path for path in paths if path == prefix or path.startswith(f"{prefix}/")
        ]
        canonical_prefix = f"/workspaces/{{workspace_id}}{prefix}"
        canonical_paths = [
            path
            for path in paths
            if path == canonical_prefix or path.startswith(f"{canonical_prefix}/")
        ]
        expected_legacy_paths = OPENAPI_LEGACY_EXCEPTIONS.get(prefix, set())
        assert set(legacy_paths) == expected_legacy_paths, (
            f"Unexpected legacy workspace routes in OpenAPI: {prefix}"
        )
        assert canonical_paths, f"Missing canonical workspace route: {canonical_prefix}"

    integration_legacy_paths = [
        path
        for path in paths
        if path == "/integrations" or path.startswith("/integrations/")
    ]
    assert integration_legacy_paths == ["/integrations/callback"]
    assert "/workspaces/{workspace_id}/integrations" in paths
    assert "/workspaces/{workspace_id}/mcp-integrations" in paths


def test_workspace_scoped_public_route_aliases_share_handlers_and_auth() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    routes_by_key = {_route_key(route): route for route in routes}
    canonical_routes = [
        route for route in routes if _is_workspace_scoped_public_route(route.path)
    ]

    assert canonical_routes

    for canonical_route in canonical_routes:
        legacy_route = routes_by_key.get(_legacy_route_key(canonical_route))

        assert legacy_route is not None, (
            f"Missing hidden legacy alias for {canonical_route.path}"
        )
        assert canonical_route.include_in_schema, (
            f"Canonical workspace route is hidden: {canonical_route.path}"
        )
        assert not legacy_route.include_in_schema, (
            f"Legacy workspace route is still visible: {legacy_route.path}"
        )
        assert canonical_route.endpoint is legacy_route.endpoint, (
            f"Route aliases use different handlers: {canonical_route.path}"
        )
        assert _has_workspace_path_dependency(canonical_route), (
            f"Canonical route does not validate workspace_id path: {canonical_route.path}"
        )
        assert not _has_workspace_path_dependency(legacy_route), (
            f"Legacy route unexpectedly validates workspace_id path: {legacy_route.path}"
        )

        role_hint = _workspace_role_hint(canonical_route)
        if role_hint is None:
            assert _route_key(canonical_route) in WORKSPACE_ROUTE_AUTH_EXEMPTIONS, (
                f"Workspace route missing role dependency: {canonical_route.path}"
            )
            continue

        assert _is_workspace_route_role(role_hint), (
            f"Workspace route is not using a route-aware role: {canonical_route.path}"
        )
        assert _uses_route_workspace_role_dependency(canonical_route), (
            f"Canonical route is not using path-or-query RoleACL: {canonical_route.path}"
        )
        assert _uses_route_workspace_role_dependency(legacy_route), (
            f"Legacy route is not using path-or-query RoleACL: {legacy_route.path}"
        )


def test_workspace_route_migration_does_not_prefix_excluded_routes() -> None:
    app.openapi_schema = None
    paths = set(app.openapi()["paths"])

    assert "/integrations/callback" in paths
    assert "/workspaces/{workspace_id}/integrations/callback" not in paths

    assert "/agent/channels/{channel_type}/{token}" in paths
    assert (
        "/workspaces/{workspace_id}/agent/channels/{channel_type}/{token}" not in paths
    )

    assert "/agent/channels/slack/oauth/callback" in paths
    assert "/workspaces/{workspace_id}/agent/channels/slack/oauth/callback" not in paths

    assert "/webhooks/{workflow_id}/{secret}" in paths
    assert "/workspaces/{workspace_id}/webhooks/{workflow_id}/{secret}" not in paths

    assert "/organization" in paths
    assert "/workspaces/{workspace_id}/organization" not in paths
