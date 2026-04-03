"""Tracecat HTTP clients."""

from typing import Any

import httpx

from tracecat import config
from tracecat.auth.secrets import get_service_key
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role


class AuthenticatedServiceClient(httpx.AsyncClient):
    """An authenticated service client. Typically used by internal services.

    Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    def __init__(self, role: Role | None = None, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Precedence: role > ctx_role > default role. Role is always set.
        resolved_role = role or ctx_role.get()
        if resolved_role is None:
            resolved_role = Role(
                type="service",
                service_id="tracecat-service",
            )
        self.role: Role = resolved_role
        self.headers["x-tracecat-service-key"] = get_service_key()
        self.headers.update(self.role.to_headers())


class AuthenticatedAPIClient(AuthenticatedServiceClient):
    """An authenticated httpx client to hit main API endpoints.

     Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    def __init__(self, role: Role | None = None, *args: Any, **kwargs: Any):
        kwargs["role"] = role
        kwargs["base_url"] = config.TRACECAT__API_URL
        super().__init__(*args, **kwargs)
