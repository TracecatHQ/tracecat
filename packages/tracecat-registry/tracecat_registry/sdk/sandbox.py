"""HTTP client for Tracecat executor sandbox endpoints.

Registry actions run in isolated environments but may need to execute Python code
in Tracecat's sandbox runtime. This module provides a small client that calls the
executor service's sandbox API using the executor JWT (`TRACECAT__EXECUTOR_TOKEN`).
"""

from __future__ import annotations

from typing import Any


from tracecat_registry.sdk.client import TracecatClient


class SandboxClientError(Exception):
    """Base error for sandbox client failures."""


class SandboxTimeoutError(SandboxClientError):
    """Raised when the sandbox reports a timeout."""


class SandboxValidationError(SandboxClientError):
    """Raised when the sandbox reports a validation error."""


class SandboxExecutionError(SandboxClientError):
    """Raised when the sandbox reports an execution error."""


class SandboxClient:
    """Async HTTP client for executor sandbox endpoints (Bearer auth)."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def run_python(
        self,
        *,
        script: str,
        inputs: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        timeout_seconds: int = 300,
        allow_network: bool = False,
        env_vars: dict[str, str] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "script": script,
            "inputs": inputs,
            "dependencies": dependencies,
            "timeout_seconds": timeout_seconds,
            "allow_network": allow_network,
            "env_vars": env_vars,
        }

        response = await self._client.post(
            "/sandbox/python/execute",
            json=payload,
        )

        if response.is_success:
            if not response.content:
                return None
            return response.json()
        elif response.status_code == 504:
            raise SandboxTimeoutError(response.text)
        if response.status_code in (400, 422):
            raise SandboxValidationError(response.text)
        raise SandboxExecutionError(response.text)
