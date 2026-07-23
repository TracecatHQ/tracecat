"""Enforce ``run_python`` provenance at the Action Gateway boundary.

Agent-authored ``core.script.run_python`` code is denied Action Gateway access
entirely. Signed executor-token provenance keeps registry-template scripts on
the trusted workflow path, while legacy executions and non-``run_python``
callers remain governed by the gateway routes' normal authentication and RBAC.
The health route is exempt so sandbox connectivity checks continue to work.

A selective route-to-registry-action capability map lived in this module
through PR-history commit ``d8752f707``. Recover it from history if a future
product mode introduces a budgeted Action Gateway capability model.
"""

from typing import Literal, NamedTuple

from fastapi import HTTPException, Request, status
from fastapi.routing import APIRoute

from tracecat.auth.executor_tokens import verify_executor_token
from tracecat.dsl.enums import PlatformAction

GatewayMethod = Literal["DELETE", "GET", "PATCH", "POST"]


class GatewayRouteKey(NamedTuple):
    """A matched Action Gateway HTTP operation."""

    method: GatewayMethod
    path: str


def gateway_route_key(method: str, path: str) -> GatewayRouteKey | None:
    """Build a typed route key, denying HTTP methods outside the gateway policy."""
    match method:
        case "DELETE" | "GET" | "PATCH" | "POST":
            return GatewayRouteKey(method, path)
        case _:
            return None


GATEWAY_EXEMPT_ROUTES: frozenset[GatewayRouteKey] = frozenset(
    {GatewayRouteKey("GET", "/internal/health")}
)


def _request_route_key(request: Request) -> GatewayRouteKey | None:
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return None
    return gateway_route_key(request.method, route.path_format)


def _action_not_allowed_error() -> HTTPException:
    """Build the 403 raised for Action Gateway calls from Agent scripts."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "agent_script_gateway_disabled",
                "message": (
                    "The Action Gateway is not available in agent-authored scripts. "
                    "Write plain Python; use your tools to run Tracecat actions."
                ),
                "required_scopes": [],
                "missing_scopes": [],
            }
        },
    )


async def enforce_agent_action_capability(request: Request) -> None:
    """Deny Action Gateway access to Agent-authored ``run_python`` scripts."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        # The normal route authentication dependency owns missing credentials.
        return

    try:
        claims = verify_executor_token(token)
    except ValueError:
        # The normal route authentication dependency owns invalid credentials.
        return

    if claims.action != PlatformAction.RUN_PYTHON or claims.allowed_actions is None:
        # ``None`` marks an execution created before the Agent-grant patch. Keep
        # those in-flight Temporal histories on their recorded legacy behavior,
        # and leave non-``run_python`` callers to the normal route auth.
        return

    if claims.execution_origin == "registry_template":
        # A run-python step inside a registry-locked template is trusted code,
        # not agent-authored Python. Exempt it from the Agent gateway deny as
        # ordinary workflow steps already are; the route's own caller-scope RBAC
        # still governs it. Provenance is signed and stamped only at the
        # template-step boundary, so it is unforgeable.
        return

    route_key = _request_route_key(request)
    if route_key in GATEWAY_EXEMPT_ROUTES:
        return

    raise _action_not_allowed_error()
