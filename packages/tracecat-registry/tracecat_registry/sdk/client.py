"""Base HTTP client for Tracecat API."""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
from typing import TYPE_CHECKING, Any

import httpx

from tracecat_registry._internal.logger import logger
from tracecat_registry.sdk.exceptions import (
    TracecatAPIError,
    TracecatAuthError,
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)

if TYPE_CHECKING:
    from tracecat_registry.sdk.agents import AgentsClient
    from tracecat_registry.sdk.cases import CasesClient
    from tracecat_registry.sdk.deduplicate import DeduplicateClient
    from tracecat_registry.sdk.forms import FormsClient
    from tracecat_registry.sdk.tables import TablesClient
    from tracecat_registry.sdk.variables import VariablesClient
    from tracecat_registry.sdk.workflows import WorkflowsClient

TENANT_BUSY_ERROR_CODE = "tenant_db_busy"
"""The Action Gateway's pre-effect capacity refusal. The gateway sends it
before the operation runs, so retrying it cannot repeat an effect."""
TENANT_BUSY_MIN_RETRY_AFTER_SECONDS = 1.0
TENANT_BUSY_MAX_RETRY_AFTER_SECONDS = 10.0
TENANT_BUSY_FIRST_LOG_SECONDS = 30.0
"""Total wait after which a still-refused request logs a warning."""
TENANT_BUSY_LOG_INTERVAL_SECONDS = 60.0


def _is_tenant_busy(response: httpx.Response) -> bool:
    """Whether a response is the gateway's tenant-busy refusal.

    Matches only the dedicated code: any other 503 may come after an effect,
    and retrying it could repeat a mutation.
    """
    if response.status_code != 503:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    match body:
        case {"detail": {"code": str(code)}}:
            return code == TENANT_BUSY_ERROR_CODE
        case _:
            return False


def _retry_after_seconds(response: httpx.Response) -> float:
    """``Retry-After`` in seconds, bounded; 1 s when absent or unparseable."""
    try:
        seconds = float(response.headers.get("Retry-After", "1"))
    except ValueError:
        seconds = 1.0
    if not math.isfinite(seconds):
        seconds = 1.0
    return min(
        max(seconds, TENANT_BUSY_MIN_RETRY_AFTER_SECONDS),
        TENANT_BUSY_MAX_RETRY_AFTER_SECONDS,
    )


def _tenant_busy_delay(response: httpx.Response, retries: int) -> float:
    """Jittered backoff before retry ``retries``, from ``Retry-After`` (capped at 10 s) up.

    The ceiling doubles with each retry up to the cap, and the delay is drawn
    between ``Retry-After`` and that ceiling.
    """
    floor = _retry_after_seconds(response)
    ceiling = min(floor * 2 ** min(retries, 10), TENANT_BUSY_MAX_RETRY_AFTER_SECONDS)
    return random.uniform(floor, max(floor, ceiling))


class TracecatClient:
    """HTTP client for the executor-local Action Gateway.

    This client is used by registry actions to communicate with the
    executor-local Action Gateway when running in a sandboxed environment.

    Configuration is read from environment variables:
    - TRACECAT__ACTION_GATEWAY_SOCKET: Local gateway Unix socket
    - TRACECAT__EXECUTOR_TOKEN: JWT token for authentication
    - TRACECAT__WORKSPACE_ID: Current workspace ID (added to requests)
    """

    def __init__(
        self,
        *,
        action_gateway_socket: str | None = None,
        token: str | None = None,
        workspace_id: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        """Initialize the client.

        Args:
            action_gateway_socket: Executor-local gateway socket. Defaults to
                TRACECAT__ACTION_GATEWAY_SOCKET and is required.
            token: JWT token for authentication. Defaults to TRACECAT__EXECUTOR_TOKEN env var.
            workspace_id: Workspace ID. Defaults to TRACECAT__WORKSPACE_ID env var.
            timeout: Request timeout in seconds.
        """
        self._action_gateway_socket = action_gateway_socket or os.environ.get(
            "TRACECAT__ACTION_GATEWAY_SOCKET"
        )
        if not self._action_gateway_socket:
            raise RuntimeError(
                "TRACECAT__ACTION_GATEWAY_SOCKET is required for Tracecat SDK calls"
            )

        self._token = token or os.environ.get("TRACECAT__EXECUTOR_TOKEN", "")
        self._workspace_id = workspace_id or os.environ.get(
            "TRACECAT__WORKSPACE_ID", ""
        )
        self._timeout = timeout

        # Lazily initialized sub-clients
        self._agents: AgentsClient | None = None
        self._cases: CasesClient | None = None
        self._deduplicate: DeduplicateClient | None = None
        self._forms: FormsClient | None = None
        self._tables: TablesClient | None = None
        self._variables: VariablesClient | None = None
        self._workflows: WorkflowsClient | None = None

    def _request_url_and_transport(
        self,
        path: str,
    ) -> tuple[str, httpx.AsyncHTTPTransport]:
        """Return the Action Gateway URL and Unix socket transport."""
        return (
            f"http://tracecat-action-gateway/internal{path}",
            httpx.AsyncHTTPTransport(uds=self._action_gateway_socket),
        )

    @property
    def workspace_id(self) -> str:
        """Current workspace ID."""
        return self._workspace_id

    @property
    def deduplicate(self) -> DeduplicateClient:
        """Deduplicate API client."""
        if self._deduplicate is None:
            from tracecat_registry.sdk.deduplicate import DeduplicateClient

            self._deduplicate = DeduplicateClient(self)
        return self._deduplicate

    @property
    def cases(self) -> CasesClient:
        """Cases API client."""
        if self._cases is None:
            from tracecat_registry.sdk.cases import CasesClient

            self._cases = CasesClient(self)
        return self._cases

    @property
    def agents(self) -> AgentsClient:
        """Agents API client."""
        if self._agents is None:
            from tracecat_registry.sdk.agents import AgentsClient

            self._agents = AgentsClient(self)
        return self._agents

    @property
    def forms(self) -> FormsClient:
        """Forms API client."""
        if self._forms is None:
            from tracecat_registry.sdk.forms import FormsClient

            self._forms = FormsClient(self)
        return self._forms

    @property
    def tables(self) -> TablesClient:
        """Tables API client."""
        if self._tables is None:
            from tracecat_registry.sdk.tables import TablesClient

            self._tables = TablesClient(self)
        return self._tables

    @property
    def variables(self) -> VariablesClient:
        """Variables API client."""
        if self._variables is None:
            from tracecat_registry.sdk.variables import VariablesClient

            self._variables = VariablesClient(self)
        return self._variables

    @property
    def workflows(self) -> WorkflowsClient:
        """Workflows API client."""
        if self._workflows is None:
            from tracecat_registry.sdk.workflows import WorkflowsClient

            self._workflows = WorkflowsClient(self)
        return self._workflows

    def _get_headers(self) -> dict[str, str]:
        """Get default headers for requests."""
        headers = {
            "Content-Type": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _extract_error_detail(self, response: httpx.Response) -> Any | None:
        try:
            data = response.json()
        except Exception:
            return response.text or None

        if isinstance(data, dict):
            detail = data.get("detail")
            if detail is not None:
                return detail
            if "message" in data:
                return data["message"]
            return None if "detail" in data else data
        return data

    def _handle_error_response(self, response: httpx.Response) -> None:
        """Convert HTTP error responses to SDK exceptions."""
        status_code = response.status_code
        detail = self._extract_error_detail(response)

        if status_code == 401:
            raise TracecatAuthError(detail=detail, status_code=401)
        elif status_code == 403:
            raise TracecatAuthError(detail=detail, status_code=403)
        elif status_code == 404:
            raise TracecatNotFoundError(detail=detail)
        elif status_code == 409:
            raise TracecatConflictError(detail=detail)
        elif status_code in (400, 422):
            raise TracecatValidationError(detail=detail, status_code=status_code)
        else:
            raise TracecatAPIError(
                message="API request failed",
                status_code=status_code,
                detail=detail,
            )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Make an authenticated HTTP request to the Action Gateway.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API path (e.g., "/cases")
            params: Query parameters
            json: JSON body data
            headers: Additional headers

        Returns:
            Parsed JSON response, or None if response has no content.

        Raises:
            TracecatAuthError: For 401/403 responses
            TracecatNotFoundError: For 404 responses
            TracecatValidationError: For 400/422 responses
            TracecatAPIError: For other error responses
        """
        url, transport = self._request_url_and_transport(path)
        request_headers = self._get_headers()
        if headers:
            request_headers.update(headers)

        async with httpx.AsyncClient(
            transport=transport,
            timeout=self._timeout,
        ) as client:
            # Retry only the gateway's pre-effect tenant-busy refusal, with no
            # count limit: capacity waits never fail accepted work. The
            # action's own timeout or cancellation ends the wait.
            retries = 0
            started = time.monotonic()
            next_log_at = TENANT_BUSY_FIRST_LOG_SECONDS
            while True:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=request_headers,
                )
                if not _is_tenant_busy(response):
                    break
                if (waited := time.monotonic() - started) >= next_log_at:
                    logger.warning(
                        "Action Gateway at the organization's database capacity; "
                        "still retrying %s %s after %.0f s (%d refusals)",
                        method,
                        path,
                        waited,
                        retries + 1,
                    )
                    next_log_at += TENANT_BUSY_LOG_INTERVAL_SECONDS
                delay = _tenant_busy_delay(response, retries)
                retries += 1
                await asyncio.sleep(delay)

        if not response.is_success:
            self._handle_error_response(response)

        # Return None for empty responses (204 No Content, etc.)
        if not response.content:
            return None

        return response.json()

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a GET request."""
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Make a POST request."""
        return await self.request("POST", path, params=params, json=json)

    async def put(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Make a PUT request."""
        return await self.request("PUT", path, params=params, json=json)

    async def patch(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Make a PATCH request."""
        return await self.request("PATCH", path, params=params, json=json)

    async def delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a DELETE request."""
        return await self.request("DELETE", path, params=params)
