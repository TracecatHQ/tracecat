"""Tracecat HTTP clients."""

import os

import httpx

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.types.auth import Role


class AuthenticatedServiceClient(httpx.AsyncClient):
    """An authenticated service client. Typically used by internal services.

    Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    def __init__(
        self,
        role: Role | None = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # Precedence: role > ctx_role > default role. Role is always set.
        self.role = role or ctx_role.get(
            Role(type="service", service_id="tracecat-service")
        )
        self.headers["Service-Role"] = self.role.service_id
        self.headers["X-API-Key"] = os.environ["TRACECAT__SERVICE_KEY"]
        if self.role.workspace_id:
            self.headers["Service-User-ID"] = str(self.role.workspace_id)


class AuthenticatedAPIClient(AuthenticatedServiceClient):
    """An authenticated httpx client to hit main API endpoints.

     Role precedence
    ---------------
    1. Role passed to the client
    2. Role set in the session role context
    3. Default role Role(type="service", service_id="tracecat-service")
    """

    def __init__(self, role: Role | None = None, *args, **kwargs):
        kwargs["role"] = role
        kwargs["base_url"] = config.TRACECAT__API_URL
        super().__init__(*args, **kwargs)
