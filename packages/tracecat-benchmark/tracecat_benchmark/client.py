"""Minimal typed async client for the public Tracecat API.

Only the endpoints the current load types need are wrapped. Everything goes
through the public, workspace-scoped HTTP API - the runner never talks to
Temporal or to PostgreSQL directly.
"""

from __future__ import annotations

import asyncio
import math
import ssl
import time
from typing import Any, Final, Self, TypedDict

import httpx

DEFAULT_TIMEOUT: Final = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)
EXECUTION_STATUS_SNAPSHOT_LIMIT: Final = 1000


class ApiError(RuntimeError):
    """A public API call returned an unexpected status code."""

    def __init__(self, method: str, url: str, status_code: int, body: str) -> None:
        super().__init__(f"{method} {url} -> {status_code}: {body[:500]}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body


class ExecutionStatusRefreshError(RuntimeError):
    """The shared execution-status snapshot could not be refreshed."""


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
    type: str
    nullable: bool
    is_index: bool


class WorkflowRef(TypedDict):
    """The subset of a workflow read that we consume."""

    id: str
    title: str
    alias: str | None


class CommitResult(TypedDict):
    """Outcome of POST /workflows/{id}/commit."""

    status: str
    errors: list[str]


class SubmitResult(TypedDict):
    """Outcome of POST /workflow-executions."""

    status_code: int
    wf_exec_id: str | None


class ExecutionSnapshot(TypedDict):
    """Status plus API-provided event count for one execution."""

    status: str
    history_length: int


class ExecutionFailureDiagnostic(TypedDict):
    """One structured failed or timed-out event from a terminal execution."""

    action_ref: str
    action_name: str
    event_type: str
    status: str
    child_wf_exec_id: str | None
    loop_index: int | None


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
        execution_poll_interval_seconds: float = 1.0,
        verify: ssl.SSLContext | bool = True,
    ) -> None:
        if (
            not math.isfinite(execution_poll_interval_seconds)
            or execution_poll_interval_seconds <= 0
        ):
            raise ValueError("execution poll interval must be finite and positive")
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
            verify=verify,
        )
        # Shared execution-status snapshot so N concurrent pollers cost one
        # paginated search per interval instead of N per-execution reads. The
        # /compact endpoint rebuilds the compacted event history per call,
        # which at burst width dominates API CPU (observer-induced load).
        self._status_cache: dict[str, ExecutionSnapshot] = {}
        self._status_cache_at: float = float("-inf")
        self._status_refresh_failed = False
        self._status_refresh_lock = asyncio.Lock()
        self._status_refresh_interval_seconds = execution_poll_interval_seconds

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

    async def delete_table(self, workspace_id: str, table_id: str) -> None:
        """Delete the exact synthetic fixture table selected by the reset path."""
        await self._request(
            "DELETE",
            f"/workspaces/{workspace_id}/tables/{table_id}",
            expected=(204,),
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
                type=_as_str(column.get("type")),
                nullable=_as_bool(column.get("nullable")),
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
        # The endpoint returns CursorPaginatedResponse, not a bare array.
        refs: list[WorkflowRef] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else None
            response = await self._request(
                "GET",
                f"/workspaces/{workspace_id}/workflows",
                expected=(200,),
                params=params,
            )
            page = self._json_object(response)
            items = page.get("items")
            if not isinstance(items, list):
                raise ApiError(
                    "GET",
                    f"/workspaces/{workspace_id}/workflows",
                    response.status_code,
                    "expected 'items' array in paginated response",
                )
            refs.extend(
                WorkflowRef(
                    id=_as_str(item.get("id")),
                    title=_as_str(item.get("title")),
                    alias=(
                        item["alias"] if isinstance(item.get("alias"), str) else None
                    ),
                )
                for item in items
            )
            next_cursor = page.get("next_cursor")
            cursor = next_cursor if isinstance(next_cursor, str) else None
            if not cursor:
                return refs

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
        alias = payload.get("alias")
        return WorkflowRef(
            id=_as_str(payload.get("id")),
            title=_as_str(payload.get("title")),
            alias=alias if isinstance(alias, str) else None,
        )

    async def delete_workflow(self, workspace_id: str, workflow_id: str) -> None:
        """Delete an existing fixture workflow before replacing its definition."""
        await self._request(
            "DELETE",
            f"/workspaces/{workspace_id}/workflows/{workflow_id}",
            expected=(204,),
        )

    async def set_workflow_alias(
        self, workspace_id: str, workflow_id: str, alias: str
    ) -> None:
        """Assign a workflow alias so subflows can reference it by name.

        Must be called before commit: `core.workflow.execute` resolves a
        published alias against the committed WorkflowDefinition, and commit
        snapshots the alias off the draft workflow.
        """
        await self._request(
            "PATCH",
            f"/workspaces/{workspace_id}/workflows/{workflow_id}",
            expected=(200, 204),
            json={"alias": alias},
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

    async def has_running_executions(self, workspace_id: str, workflow_id: str) -> bool:
        """Return whether one workflow currently has any running executions."""
        response = await self._request(
            "GET",
            f"/workspaces/{workspace_id}/workflow-executions/search",
            expected=(200,),
            params={
                "workflow_id": workflow_id,
                "status": "RUNNING",
                "limit": 1,
            },
        )
        payload = self._json_object(response)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ApiError(
                "GET",
                f"/workspaces/{workspace_id}/workflow-executions/search",
                response.status_code,
                "expected 'items' array in paginated response",
            )
        return bool(items)

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
            )
        payload = response.json()
        raw_wf_exec_id = (
            payload.get("wf_exec_id") if isinstance(payload, dict) else None
        )
        wf_exec_id = (
            raw_wf_exec_id
            if isinstance(raw_wf_exec_id, str) and raw_wf_exec_id.strip()
            else None
        )
        return SubmitResult(status_code=response.status_code, wf_exec_id=wf_exec_id)

    async def get_execution_status(
        self, workspace_id: str, workflow_id: str, wf_exec_id: str
    ) -> ExecutionSnapshot | None:
        """Read one execution's status and event count from a shared snapshot.

        Returns None when the execution is not in the snapshot yet (treated by
        the caller as "not terminal"), or when the API did not answer 200 on
        the last refresh (the stale snapshot is retained).
        """
        if (
            time.monotonic() - self._status_cache_at
            >= self._status_refresh_interval_seconds
        ):
            async with self._status_refresh_lock:
                if (
                    time.monotonic() - self._status_cache_at
                    >= self._status_refresh_interval_seconds
                ):
                    await self._refresh_status_cache(workspace_id, workflow_id)
        if self._status_refresh_failed:
            raise ExecutionStatusRefreshError(
                "execution status snapshot refresh failed"
            )
        cached = self._status_cache.get(wf_exec_id)
        if cached is not None:
            return cached
        # Fallback: executions can fall off the list page (e.g. subflow
        # children outrank their parents by start time). Fetch individually
        # via /compact — acceptable at comparison-cell widths.
        response = await self._client.get(
            f"/workspaces/{workspace_id}/workflow-executions/{wf_exec_id}/compact"
        )
        if response.status_code != 200:
            raise ExecutionStatusRefreshError(
                f"execution status lookup failed: status {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExecutionStatusRefreshError(
                "execution status lookup returned a non-object"
            )
        snapshot = self._parse_execution_snapshot(payload)
        if snapshot is None:
            raise ExecutionStatusRefreshError(
                "execution status lookup omitted status or history metrics"
            )
        return snapshot

    async def get_execution_failure_diagnostics(
        self,
        workspace_id: str,
        wf_exec_id: str,
    ) -> list[ExecutionFailureDiagnostic]:
        """Fetch non-message structured diagnostics for one failed run."""
        response = await self._client.get(
            f"/workspaces/{workspace_id}/workflow-executions/{wf_exec_id}/compact"
        )
        if response.status_code != 200:
            raise ExecutionStatusRefreshError(
                "execution failure diagnostics lookup failed: "
                f"status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExecutionStatusRefreshError(
                "execution failure diagnostics lookup returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ExecutionStatusRefreshError(
                "execution failure diagnostics lookup returned a non-object"
            )
        events = payload.get("events")
        if not isinstance(events, list):
            raise ExecutionStatusRefreshError(
                "execution failure diagnostics lookup omitted compact events"
            )
        return self._parse_execution_failure_diagnostics(events)

    @staticmethod
    def _parse_execution_failure_diagnostics(
        events: list[object],
    ) -> list[ExecutionFailureDiagnostic]:
        diagnostics: list[ExecutionFailureDiagnostic] = []
        failed_statuses = {"FAILED", "TIMED_OUT", "CANCELED", "TERMINATED"}
        for raw_event in events:
            if not isinstance(raw_event, dict):
                continue
            status = raw_event.get("status")
            action_ref = raw_event.get("action_ref")
            action_name = raw_event.get("action_name")
            event_type = raw_event.get("curr_event_type")
            if (
                status not in failed_statuses
                or not isinstance(status, str)
                or not isinstance(action_ref, str)
                or not isinstance(action_name, str)
                or not isinstance(event_type, str)
            ):
                continue

            raw_child_id = raw_event.get("child_wf_exec_id")
            raw_loop_index = raw_event.get("loop_index")
            diagnostics.append(
                ExecutionFailureDiagnostic(
                    action_ref=action_ref,
                    action_name=action_name,
                    event_type=event_type,
                    status=status,
                    child_wf_exec_id=(
                        raw_child_id if isinstance(raw_child_id, str) else None
                    ),
                    loop_index=(
                        raw_loop_index
                        if isinstance(raw_loop_index, int)
                        and not isinstance(raw_loop_index, bool)
                        else None
                    ),
                )
            )
        return diagnostics

    @staticmethod
    def _parse_execution_snapshot(
        payload: dict[str, object],
    ) -> ExecutionSnapshot | None:
        status = payload.get("status")
        history_length = payload.get("history_length")
        if (
            not isinstance(status, str)
            or not isinstance(history_length, int)
            or isinstance(history_length, bool)
            or history_length < 0
        ):
            return None
        return ExecutionSnapshot(
            status=status,
            history_length=history_length,
        )

    async def _refresh_status_cache(self, workspace_id: str, workflow_id: str) -> None:
        """Page through every execution the runner is polling."""
        try:
            snapshot: dict[str, ExecutionSnapshot] = {}
            cursor: str | None = None
            seen_cursors: set[str] = set()
            while True:
                params = {
                    "workflow_id": workflow_id,
                    "limit": EXECUTION_STATUS_SNAPSHOT_LIMIT,
                }
                if cursor is not None:
                    params["cursor"] = cursor
                try:
                    response = await self._client.get(
                        f"/workspaces/{workspace_id}/workflow-executions/search",
                        params=params,
                    )
                except httpx.HTTPError:
                    self._status_refresh_failed = True
                    raise
                if response.status_code != 200:
                    self._status_refresh_failed = True
                    return
                try:
                    payload = response.json()
                except ValueError:
                    self._status_refresh_failed = True
                    return
                if not isinstance(payload, dict):
                    self._status_refresh_failed = True
                    return
                items = payload.get("items")
                if not isinstance(items, list):
                    self._status_refresh_failed = True
                    return
                for item in items:
                    if isinstance(item, dict):
                        exec_id = item.get("id")
                        execution_snapshot = self._parse_execution_snapshot(item)
                        if isinstance(exec_id, str) and execution_snapshot is not None:
                            snapshot[exec_id] = execution_snapshot
                next_cursor = payload.get("next_cursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    break
                if next_cursor in seen_cursors:
                    self._status_refresh_failed = True
                    return
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            self._status_cache = snapshot
            self._status_refresh_failed = False
        finally:
            # Retain a stale successful snapshot on failure, but throttle every
            # refresh attempt so lock waiters cannot stampede a degraded API.
            self._status_cache_at = time.monotonic()
