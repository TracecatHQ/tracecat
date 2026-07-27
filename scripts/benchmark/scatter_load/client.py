"""Minimal typed async client for the public Tracecat API.

Only the endpoints the scatter load test needs are wrapped. Everything goes
through the public, workspace-scoped HTTP API - the runner never talks to
Temporal or to PostgreSQL directly.
"""

from __future__ import annotations

from typing import Any, Final, Self, TypedDict

import httpx

DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)


class ApiError(RuntimeError):
    """A public API call returned an unexpected status code."""

    def __init__(self, method: str, url: str, status_code: int, body: str) -> None:
        super().__init__(f"{method} {url} -> {status_code}: {body[:500]}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body


class WorkspaceRef(TypedDict):
    """The subset of a workspace listing entry that we consume."""

    id: str
    name: str


class TableRef(TypedDict):
    """The subset of a table listing entry that we consume."""

    id: str
    name: str


class TableColumnRef(TypedDict):
    """The subset of a table column read that we consume."""

    id: str
    name: str
    is_index: bool


class WorkflowRef(TypedDict):
    """The subset of a workflow read that we consume."""

    id: str
    title: str


class CommitResult(TypedDict):
    """Outcome of POST /workflows/{id}/commit."""

    status: str
    errors: list[str]


class SubmitResult(TypedDict):
    """Outcome of POST /workflow-executions."""

    status_code: int
    wf_exec_id: str | None
    detail: str | None


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def _as_bool(value: object) -> bool:
    return value is True


class TracecatClient:
    """Async client scoped to one workspace of one local Tracecat cluster."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_connections: int = 200,
    ) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
                keepalive_expiry=30.0,
            ),
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- plumbing ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...],
        **kwargs: Any,
    ) -> httpx.Response:
        response = await self._client.request(method, url, **kwargs)
        if response.status_code not in expected:
            raise ApiError(method, url, response.status_code, response.text)
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, object]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ApiError(
                response.request.method,
                str(response.request.url),
                response.status_code,
                "expected a JSON object",
            )
        return payload

    @staticmethod
    def _json_array(response: httpx.Response) -> list[dict[str, object]]:
        payload = response.json()
        if not isinstance(payload, list):
            raise ApiError(
                response.request.method,
                str(response.request.url),
                response.status_code,
                "expected a JSON array",
            )
        return [item for item in payload if isinstance(item, dict)]

    # -- auth -------------------------------------------------------------

    async def login(self, email: str, password: str) -> None:
        """Authenticate as a synthetic local user.

        The API uses fastapi-users with a cookie transport and a database-backed
        token strategy, so `POST /auth/login` takes an OAuth2 password form and
        answers with a Set-Cookie header. httpx stores the cookie on the client
        for every subsequent request.
        """
        await self._request(
            "POST",
            "/auth/login",
            expected=(200, 204),
            data={"username": email, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    # -- workspaces -------------------------------------------------------

    async def list_workspaces(self) -> list[WorkspaceRef]:
        response = await self._request("GET", "/workspaces", expected=(200,))
        return [
            WorkspaceRef(id=_as_str(item.get("id")), name=_as_str(item.get("name")))
            for item in self._json_array(response)
        ]

    async def create_workspace(self, name: str) -> WorkspaceRef:
        response = await self._request(
            "POST", "/workspaces", expected=(200, 201), json={"name": name}
        )
        payload = self._json_object(response)
        return WorkspaceRef(
            id=_as_str(payload.get("id")), name=_as_str(payload.get("name"))
        )

    # -- tables -----------------------------------------------------------

    async def list_tables(self, workspace_id: str) -> list[TableRef]:
        response = await self._request(
            "GET", f"/workspaces/{workspace_id}/tables", expected=(200,)
        )
        return [
            TableRef(id=_as_str(item.get("id")), name=_as_str(item.get("name")))
            for item in self._json_array(response)
        ]

    async def create_table(
        self, workspace_id: str, name: str, columns: list[dict[str, object]]
    ) -> None:
        """Create a table. The endpoint answers 201 with no body."""
        await self._request(
            "POST",
            f"/workspaces/{workspace_id}/tables",
            expected=(200, 201),
            json={"name": name, "columns": columns},
        )

    async def get_table_columns(
        self, workspace_id: str, table_id: str
    ) -> list[TableColumnRef]:
        response = await self._request(
            "GET", f"/workspaces/{workspace_id}/tables/{table_id}", expected=(200,)
        )
        payload = self._json_object(response)
        raw_columns = payload.get("columns")
        if not isinstance(raw_columns, list):
            return []
        return [
            TableColumnRef(
                id=_as_str(column.get("id")),
                name=_as_str(column.get("name")),
                is_index=_as_bool(column.get("is_index")),
            )
            for column in raw_columns
            if isinstance(column, dict)
        ]

    async def set_column_unique_index(
        self, workspace_id: str, table_id: str, column_id: str
    ) -> None:
        await self._request(
            "PATCH",
            f"/workspaces/{workspace_id}/tables/{table_id}/columns/{column_id}",
            expected=(200, 204),
            json={"is_index": True},
        )

    # -- workflows --------------------------------------------------------

    async def list_workflows(self, workspace_id: str) -> list[WorkflowRef]:
        response = await self._request(
            "GET", f"/workspaces/{workspace_id}/workflows", expected=(200,)
        )
        return [
            WorkflowRef(id=_as_str(item.get("id")), title=_as_str(item.get("title")))
            for item in self._json_array(response)
        ]

    async def create_workflow_from_yaml(
        self, workspace_id: str, filename: str, content: bytes
    ) -> WorkflowRef:
        response = await self._request(
            "POST",
            f"/workspaces/{workspace_id}/workflows",
            expected=(200, 201),
            files={"file": (filename, content, "application/yaml")},
        )
        payload = self._json_object(response)
        return WorkflowRef(
            id=_as_str(payload.get("id")), title=_as_str(payload.get("title"))
        )

    async def commit_workflow(
        self, workspace_id: str, workflow_id: str
    ) -> CommitResult:
        """Commit a workflow. Note the endpoint returns 200 even on failure."""
        response = await self._request(
            "POST",
            f"/workspaces/{workspace_id}/workflows/{workflow_id}/commit",
            expected=(200, 201),
        )
        payload = self._json_object(response)
        raw_errors = payload.get("errors")
        errors = (
            [_as_str(item) for item in raw_errors]
            if isinstance(raw_errors, list)
            else []
        )
        return CommitResult(status=_as_str(payload.get("status")), errors=errors)

    # -- workflow executions ----------------------------------------------

    async def submit_execution(
        self,
        workspace_id: str,
        workflow_id: str,
        inputs: dict[str, object],
    ) -> SubmitResult:
        """Start a workflow through the public admission path.

        Returns the raw status code instead of raising, so the runner can
        classify admission rejections without inspecting exception text.
        """
        response = await self._client.post(
            f"/workspaces/{workspace_id}/workflow-executions",
            json={"workflow_id": workflow_id, "inputs": inputs},
        )
        if response.status_code not in (200, 201, 202):
            return SubmitResult(
                status_code=response.status_code,
                wf_exec_id=None,
                detail=response.text[:500],
            )
        payload = response.json()
        wf_exec_id = (
            _as_str(payload.get("wf_exec_id")) if isinstance(payload, dict) else None
        )
        return SubmitResult(
            status_code=response.status_code, wf_exec_id=wf_exec_id, detail=None
        )

    async def get_execution_status(
        self, workspace_id: str, wf_exec_id: str
    ) -> str | None:
        """Read one execution's status. Returns None if the API did not answer 200."""
        response = await self._client.get(
            f"/workspaces/{workspace_id}/workflow-executions/{wf_exec_id}"
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        status = payload.get("status")
        return _as_str(status) if status is not None else None
