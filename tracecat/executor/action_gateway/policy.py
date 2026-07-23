"""Action Gateway policy for Agent-authored Python."""

from fastapi import HTTPException, Request, status

from tracecat.auth.executor_tokens import verify_executor_token

ACTION_GATEWAY_HEALTH_PATH = "/internal/health"


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


async def enforce_agent_script_gateway_access(request: Request) -> None:
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
        return

    if request.url.path == ACTION_GATEWAY_HEALTH_PATH:
        return

    raise _action_not_allowed_error()
