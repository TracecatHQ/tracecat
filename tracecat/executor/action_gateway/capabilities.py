"""Enforce code provenance at the Action Gateway boundary.

Agent-authored code is denied Action Gateway access entirely. The deny is based
only on signed executor-token provenance. Registry-template code and unattested
executions remain governed by the gateway routes' normal authentication and
RBAC. The health route is exempt so sandbox connectivity checks continue to
work.

A selective route-to-registry-action capability map lived in this module
through PR-history commit ``d8752f707``. Recover it from history if a future
product mode introduces a budgeted Action Gateway capability model.
"""

from typing import Literal, NamedTuple

from fastapi import HTTPException, Request, status
from fastapi.routing import APIRoute

from tracecat.auth.executor_tokens import verify_executor_token

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
    """Deny Action Gateway access when the token attests Agent-authored code."""
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

    if claims.execution_origin != "agent":
        # Unattested legacy tokens, workflow steps, and agent-invoked registry
        # actions remain on their normal route-auth path, as does explicitly
        # attested registry-template code.
        return

    route_key = _request_route_key(request)
    if route_key in GATEWAY_EXEMPT_ROUTES:
        return

    raise _action_not_allowed_error()
