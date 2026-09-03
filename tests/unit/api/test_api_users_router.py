from tests._route_utils import iter_effective_api_routes
from tracecat.api.app import app


def test_fastapi_users_routes_set_authenticated_user_context() -> None:
    routes = [
        route
        for route in iter_effective_api_routes(app)
        if route.name.startswith("users:")
    ]

    route_names = {route.name for route in routes}
    assert route_names == {
        "users:current_user",
        "users:patch_current_user",
        "users:user",
        "users:patch_user",
        "users:delete_user",
    }

    for route in routes:
        dependency_names = {
            getattr(dependency.call, "__name__", None)
            for dependency in route.dependant.dependencies
        }
        assert "authenticated_user_only" in dependency_names
