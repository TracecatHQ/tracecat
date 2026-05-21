from __future__ import annotations

import asyncio
import atexit
import base64
import fcntl
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, NoReturn

import httpx
import pytest

from tracecat.identifiers.workflow import WorkflowUUID

TERMINAL_WORKFLOW_STATUSES = {
    "COMPLETED",
    "FAILED",
    "CANCELED",
    "TERMINATED",
    "TIMED_OUT",
}
USAGE_FIELDS = ("requests", "tool_calls", "input_tokens", "output_tokens")
SMOKE_EMAIL = "dev@tracecat.com"
SMOKE_PASSWORD = "password1234"
SMOKE_PROVIDER_NAMES = ("openai", "anthropic", "custom")
PRIMARY_PROVIDER_NAME = "openai"
OPENAI_MODEL_NAME = "gpt-5.4-mini"
ANTHROPIC_MODEL_NAME = "claude-haiku-4-5-20251001"
CUSTOM_PROVIDER_MODEL_NAME = "claude-haiku-4-5-bedrock"
CUSTOM_PROVIDER_LITELLM_MODEL_NAME = (
    "bedrock/converse/us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
REQUEST_TIMEOUT_SECONDS = 180.0
WORKFLOW_TIMEOUT_SECONDS = 420.0
STREAM_TIMEOUT_SECONDS = 240.0
TEST_TIMEOUT_SECONDS = 900.0
POLL_INTERVAL_SECONDS = 3.0
DEFAULT_CUSTOM_REGISTRY_ACTION = "tools.agent_smoke.echo_marker"
DEFAULT_LOCAL_REGISTRY_ORIGIN = "local"
DEFAULT_WORKFLOW_MODEL_SETTINGS = {"max_tokens": 1024}
DEFAULT_HTTP_MCP_URL = "http://agent-smoke-http-mcp:8765/mcp"
HTTP_MCP_FIXTURE_CONTAINER_PREFIX = "agent-smoke-http-mcp"
HTTP_MCP_FIXTURE_PORT = "8765"
DEFAULT_CUSTOM_PROVIDER_BASE_URL = "http://agent-smoke-custom-litellm:4100/v1"
CUSTOM_PROVIDER_SIDECAR_CONTAINER_PREFIX = "agent-smoke-custom-litellm"
CUSTOM_PROVIDER_SIDECAR_PORT = "4100"
CUSTOM_PROVIDER_SIDECAR_ENV_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_REGION_NAME",
    "AWS_BEARER_TOKEN_BEDROCK",
)
_HTTP_MCP_FIXTURE_READY = False
_CUSTOM_PROVIDER_SIDECAR_READY = False
_STARTED_LOCAL_FIXTURE_CONTAINERS: set[str] = set()
_CLEANUP_REGISTERED = False


@dataclass(frozen=True)
class SmokeEnvironment:
    base_api_url: str
    email: str
    password: str
    workspace_id: str | None
    enabled: bool
    summary_path: Path | None
    request_timeout_seconds: float
    workflow_timeout_seconds: float
    stream_timeout_seconds: float
    test_timeout_seconds: float
    poll_interval_seconds: float

    @classmethod
    def from_env(cls) -> SmokeEnvironment:
        return cls(
            base_api_url=_normalize_api_url(
                os.environ.get("TRACECAT_TEST_EXTERNAL_API_URL")
                or os.environ.get("TRACECAT__PUBLIC_API_URL")
                or "http://localhost/api"
            ),
            email=SMOKE_EMAIL,
            password=SMOKE_PASSWORD,
            workspace_id=None,
            enabled=os.environ.get("RUN_AGENT_SMOKE") == "1",
            summary_path=Path("artifacts/agent-smoke-summary.json")
            if _truthy(os.environ.get("CI"))
            else None,
            request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            workflow_timeout_seconds=WORKFLOW_TIMEOUT_SECONDS,
            stream_timeout_seconds=STREAM_TIMEOUT_SECONDS,
            test_timeout_seconds=TEST_TIMEOUT_SECONDS,
            poll_interval_seconds=POLL_INTERVAL_SECONDS,
        )


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model_provider: str
    model_name: str
    catalog_id: str
    model_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class McpToolSpec:
    integration_id: str
    tool_name: str
    expected_prefix: str


@dataclass
class StreamResult:
    text: str
    payload_count: int
    saw_text_delta: bool
    saw_tool_activity: bool
    saw_approval_activity: bool


@dataclass(frozen=True)
class BasicStreamReplay:
    events: list[dict[str, Any]]
    last_event_id: str | None


@dataclass
class AgentSmokeClient:
    env: SmokeEnvironment
    client: httpx.AsyncClient = field(init=False)
    workspace_id: str = field(init=False)
    organization_id: str = field(init=False)
    provider_cache: dict[str, ProviderSpec] = field(default_factory=dict, init=False)
    mcp_cache: dict[str, McpToolSpec] = field(default_factory=dict, init=False)
    custom_registry_action: str | None = field(default=None, init=False)
    workflow_providers: dict[str, ProviderSpec] = field(
        default_factory=dict, init=False
    )
    recorded_usage_execution_ids: set[str] = field(default_factory=set, init=False)
    summary: list[dict[str, Any]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        timeout = httpx.Timeout(self.env.request_timeout_seconds)
        object.__setattr__(
            self,
            "client",
            httpx.AsyncClient(base_url=self.env.base_api_url, timeout=timeout),
        )

    async def __aenter__(self) -> AgentSmokeClient:
        if not self.env.enabled:
            _skip_or_fail("Set RUN_AGENT_SMOKE=1 to run live agent smoke tests")
        await self.login()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.client.aclose()
        if self.env.summary_path is not None:
            _append_summary(self.env.summary_path, self.summary)

    async def login(self) -> None:
        response = await self.client.post(
            "/auth/login",
            data={"username": self.env.email, "password": self.env.password},
        )
        _raise_for_status(response, expected={200, 204})

        workspaces = await self.get_json("/workspaces")
        if not isinstance(workspaces, list):
            raise AssertionError("/workspaces returned an unexpected payload")
        if not workspaces:
            created = await self.post_json(
                "/workspaces",
                {"name": f"agent-smoke-{uuid.uuid4().hex[:8]}"},
                expected={201},
            )
            workspaces = [created]

        selected_workspace = self.env.workspace_id or str(workspaces[0]["id"])
        workspace = await self.get_json(f"/workspaces/{selected_workspace}")
        if not isinstance(workspace, dict):
            raise AssertionError("/workspaces/{id} returned an unexpected payload")
        object.__setattr__(self, "workspace_id", str(workspace["id"]))
        object.__setattr__(self, "organization_id", str(workspace["organization_id"]))
        self.client.cookies.set("tracecat-org-id", self.organization_id)

    async def ensure_provider(self, provider_name: str) -> ProviderSpec:
        if provider_name in self.provider_cache:
            return self.provider_cache[provider_name]

        match provider_name:
            case "openai":
                spec = await self._ensure_builtin_provider(
                    provider_name="openai",
                    key_env="OPENAI_API_KEY",
                    credential_key="OPENAI_API_KEY",
                    model_name=OPENAI_MODEL_NAME,
                )
            case "anthropic":
                spec = await self._ensure_builtin_provider(
                    provider_name="anthropic",
                    key_env="ANTHROPIC_API_KEY",
                    credential_key="ANTHROPIC_API_KEY",
                    model_name=ANTHROPIC_MODEL_NAME,
                )
            case "custom":
                spec = await self._ensure_custom_provider()
            case _:
                _skip_or_fail(f"Unsupported smoke provider: {provider_name}")

        self.provider_cache[provider_name] = spec
        return spec

    async def _ensure_builtin_provider(
        self,
        *,
        provider_name: str,
        key_env: str,
        credential_key: str,
        model_name: str,
    ) -> ProviderSpec:
        api_key = os.environ.get(key_env)
        if not api_key:
            _skip_or_fail(f"Set {key_env} to run {provider_name} agent smoke tests")

        with _agent_smoke_lock(f"{self.organization_id}-{provider_name}-credentials"):
            await self._upsert_builtin_provider_credentials(
                provider_name=provider_name,
                credential_key=credential_key,
                api_key=api_key,
            )
            catalog_entry = await self.find_catalog_entry(
                provider=provider_name,
                model_name=model_name,
            )
        return ProviderSpec(
            name=provider_name,
            model_provider=provider_name,
            model_name=model_name,
            catalog_id=str(catalog_entry["id"]),
            model_metadata=_model_metadata(catalog_entry),
        )

    async def _upsert_builtin_provider_credentials(
        self,
        *,
        provider_name: str,
        credential_key: str,
        api_key: str,
    ) -> None:
        payload = {
            "provider": provider_name,
            "credentials": {credential_key: api_key},
        }
        response = await self.client.post("/agent/credentials", json=payload)
        if response.status_code == 201:
            return
        if response.status_code == 400 and _is_duplicate_provider_secret_response(
            response, provider_name
        ):
            update_response = await self.client.put(
                f"/agent/credentials/{provider_name}",
                json={"credentials": {credential_key: api_key}},
            )
            _raise_for_status(update_response, expected={200})
            return
        _raise_for_status(response, expected={201})

    async def ensure_default_model(self, provider: ProviderSpec) -> None:
        with _agent_smoke_lock(f"{self.organization_id}-default-model"):
            response = await self.client.get("/agent/default-model-selection")
            _raise_for_status(response, expected={200})
            current = response.json() if response.content else None
            if (
                isinstance(current, dict)
                and str(current.get("catalog_id")) == provider.catalog_id
            ):
                return
            await self.put_json(
                "/agent/default-model-selection",
                {"catalog_id": provider.catalog_id},
            )

    async def _ensure_custom_provider(self) -> ProviderSpec:
        explicit_base_url = os.environ.get("CUSTOM_PROVIDER_BASE_URL")
        base_url = explicit_base_url or _default_custom_provider_base_url()
        uses_local_sidecar = (
            explicit_base_url is None
            and not _truthy(os.environ.get("CI"))
            and _uses_local_custom_provider_sidecar(base_url)
        )
        requested_model_name = CUSTOM_PROVIDER_MODEL_NAME
        sidecar_model_name = CUSTOM_PROVIDER_LITELLM_MODEL_NAME

        if uses_local_sidecar:
            _ensure_local_custom_provider_sidecar(
                model_name=requested_model_name,
                sidecar_model=sidecar_model_name,
            )

        payload: dict[str, Any] = {
            "display_name": f"agent-smoke-{uuid.uuid4().hex[:8]}",
            "base_url": base_url,
            "passthrough": True,
        }
        if not uses_local_sidecar:
            if api_key := os.environ.get("CUSTOM_PROVIDER_API_KEY"):
                payload["api_key"] = api_key
            if api_key_header := os.environ.get("CUSTOM_PROVIDER_API_KEY_HEADER"):
                payload["api_key_header"] = api_key_header
            if headers := os.environ.get("CUSTOM_PROVIDER_HEADERS_JSON"):
                payload["custom_headers"] = json.loads(headers)

        provider = await self.post_json(
            "/organization/agent-custom-providers",
            payload,
        )
        provider_id = str(provider["id"])
        await self.post_json(
            f"/organization/agent-custom-providers/{provider_id}/refresh",
            {},
            expected={202},
        )
        catalog_entry = await self.find_custom_provider_catalog_entry(
            custom_provider_id=provider_id,
            preferred_model_names=tuple(
                name for name in (requested_model_name, sidecar_model_name) if name
            ),
        )
        model_name = str(catalog_entry["model_name"])
        return ProviderSpec(
            name="custom",
            model_provider="custom-model-provider",
            model_name=model_name,
            catalog_id=str(catalog_entry["id"]),
            model_metadata=_model_metadata(catalog_entry),
        )

    async def find_catalog_id(
        self,
        *,
        provider: str,
        model_name: str,
        custom_provider_id: str | None = None,
    ) -> str:
        return str(
            (
                await self.find_catalog_entry(
                    provider=provider,
                    model_name=model_name,
                    custom_provider_id=custom_provider_id,
                )
            )["id"]
        )

    async def find_catalog_entry(
        self,
        *,
        provider: str,
        model_name: str,
        custom_provider_id: str | None = None,
    ) -> dict[str, Any]:
        payload = await self.get_json(
            "/organization/agent-catalog",
            params={"provider": provider, "model_name": model_name, "limit": 100},
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            if item.get("model_provider") != provider:
                continue
            if item.get("model_name") != model_name:
                continue
            if (
                custom_provider_id
                and item.get("custom_provider_id") != custom_provider_id
            ):
                continue
            return item
        raise AssertionError(
            f"Catalog entry not found for provider={provider!r} model={model_name!r}"
        )

    async def find_custom_provider_catalog_entry(
        self,
        *,
        custom_provider_id: str,
        preferred_model_names: tuple[str, ...],
    ) -> dict[str, Any]:
        payload = await self.get_json(
            "/organization/agent-catalog",
            params={"provider": "custom-model-provider", "limit": 100},
        )
        items = payload.get("items", []) if isinstance(payload, dict) else []
        matches = [
            item
            for item in items
            if item.get("model_provider") == "custom-model-provider"
            and item.get("custom_provider_id") == custom_provider_id
        ]
        for model_name in preferred_model_names:
            for item in matches:
                if item.get("model_name") == model_name:
                    return item
        if len(matches) == 1:
            return matches[0]

        available = sorted(
            str(item.get("model_name")) for item in matches if item.get("model_name")
        )
        raise AssertionError(
            "Custom provider catalog did not include a usable model. "
            f"Preferred={list(preferred_model_names)!r}; available={available!r}"
        )

    async def create_agent_preset(
        self,
        provider: ProviderSpec,
        *,
        instructions: str | None = None,
        actions: list[str] | None = None,
        tool_approvals: dict[str, bool] | None = None,
        mcp_integrations: list[str] | None = None,
    ) -> dict[str, Any]:
        slug = f"agent-smoke-{uuid.uuid4().hex[:10]}"
        return await self.post_json(
            f"/workspaces/{self.workspace_id}/agent/presets",
            {
                "name": f"Agent smoke {slug}",
                "slug": slug,
                "description": "Automated live agent smoke preset",
                "instructions": instructions or _default_agent_instructions(),
                "model_name": provider.model_name,
                "model_provider": provider.model_provider,
                "catalog_id": provider.catalog_id,
                "actions": actions,
                "tool_approvals": tool_approvals,
                "mcp_integrations": mcp_integrations,
                "retries": 0,
                "enable_thinking": False,
                "enable_internet_access": False,
            },
            expected={201},
        )

    async def create_session(
        self,
        *,
        title: str,
        entity_type: str,
        entity_id: str,
        session_id: str | None = None,
        tools: list[str] | None = None,
        agent_preset_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "entity_type": entity_type,
            "entity_id": entity_id,
        }
        if session_id is not None:
            payload["id"] = session_id
        if tools is not None:
            payload["tools"] = tools
        if agent_preset_id is not None:
            payload["agent_preset_id"] = agent_preset_id
        return await self.post_json(
            f"/workspaces/{self.workspace_id}/agent/sessions",
            payload,
        )

    async def send_chat_turn(
        self,
        session_id: str,
        provider: ProviderSpec,
        prompt: str,
    ) -> StreamResult:
        body = {
            "kind": "vercel",
            "message": {
                "id": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"type": "text", "text": prompt}],
            },
            "model": provider.model_name,
            "model_provider": provider.model_provider,
        }
        return await self._stream_post(
            f"/workspaces/{self.workspace_id}/agent/sessions/{session_id}/messages",
            body,
        )

    async def continue_with_approval(
        self,
        session_id: str,
        *,
        tool_call_id: str,
    ) -> StreamResult:
        return await self._stream_post(
            f"/workspaces/{self.workspace_id}/agent/sessions/{session_id}/messages",
            {
                "kind": "continue",
                "source": "inbox",
                "decisions": [{"tool_call_id": tool_call_id, "action": "approve"}],
            },
        )

    async def _stream_post(self, path: str, body: dict[str, Any]) -> StreamResult:
        text_parts: list[str] = []
        payload_count = 0
        saw_tool_activity = False
        saw_approval_activity = False
        try:
            async with asyncio.timeout(self.env.stream_timeout_seconds):
                async with self.client.stream(
                    "POST",
                    path,
                    json=body,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    if response.status_code != 200:
                        response_body = (await response.aread()).decode(
                            response.encoding or "utf-8",
                            errors="replace",
                        )
                        raise AssertionError(
                            f"{response.request.method} {response.request.url} "
                            f"returned {response.status_code}, expected [200]: "
                            f"{response_body[:2000]}"
                        )
                    async for line in response.aiter_lines():
                        payload = _parse_sse_data_line(line)
                        if payload is None:
                            continue
                        payload_count += 1
                        payload_type = str(
                            payload.get("type") or payload.get("kind") or ""
                        )
                        if payload_type in {"error", "error-delta"}:
                            raise AssertionError(
                                f"Agent stream error: {_compact_json(payload)}"
                            )
                        if payload_type == "text-delta":
                            delta = payload.get("delta")
                            if isinstance(delta, str):
                                text_parts.append(delta)
                        if "tool" in payload_type.lower() or "tool" in _compact_json(
                            payload
                        ):
                            saw_tool_activity = True
                        if (
                            "approval" in payload_type.lower()
                            or "approval" in _compact_json(payload)
                        ):
                            saw_approval_activity = True
        except TimeoutError as exc:
            raise AssertionError(
                f"Timed out waiting for agent stream from {path!r} after "
                f"{self.env.stream_timeout_seconds}s"
            ) from exc

        text = "".join(text_parts)
        return StreamResult(
            text=text,
            payload_count=payload_count,
            saw_text_delta=bool(text_parts),
            saw_tool_activity=saw_tool_activity,
            saw_approval_activity=saw_approval_activity,
        )

    async def read_basic_stream_replay(
        self,
        session_id: str,
        *,
        last_event_id: str = "0-0",
        stop_after_indexed_events: int | None = None,
        timeout_seconds: float = 45,
    ) -> BasicStreamReplay:
        events: list[dict[str, Any]] = []
        latest_event_id: str | None = None
        current: dict[str, Any] = {}
        indexed_events_seen = 0

        async def flush_current() -> bool:
            nonlocal current, indexed_events_seen, latest_event_id
            if not current:
                return False
            event = current
            current = {}
            events.append(event)
            if event_id := event.get("id"):
                latest_event_id = str(event_id)
                if event.get("event") != "connected":
                    indexed_events_seen += 1
            return (
                bool(
                    stop_after_indexed_events is not None
                    and indexed_events_seen >= stop_after_indexed_events
                )
                or event.get("event") == "end"
            )

        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.client.stream(
                    "GET",
                    f"/workspaces/{self.workspace_id}/agent/sessions/{session_id}/stream",
                    params={"format": "basic"},
                    headers={"Last-Event-ID": last_event_id},
                ) as response:
                    _raise_for_status(response, expected={200})
                    async for line in response.aiter_lines():
                        if line == "":
                            if await flush_current():
                                break
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("id:"):
                            current["id"] = line[3:].strip()
                        elif line.startswith("event:"):
                            current["event"] = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()
                            current.setdefault("data_raw_parts", []).append(data)
                await flush_current()
        except TimeoutError as exc:
            raise AssertionError(
                f"Timed out replaying stream for session {session_id}"
            ) from exc

        for event in events:
            raw_parts = event.pop("data_raw_parts", [])
            raw_data = "\n".join(raw_parts)
            event["data_raw"] = raw_data
            if raw_data:
                try:
                    event["data"] = json.loads(raw_data)
                except json.JSONDecodeError:
                    event["data"] = raw_data

        return BasicStreamReplay(events=events, last_event_id=latest_event_id)

    async def assert_basic_stream_resume(self, session_id: str) -> None:
        first = await self.read_basic_stream_replay(
            session_id,
            last_event_id="0-0",
            stop_after_indexed_events=1,
        )
        resume_event_id = first.last_event_id
        if resume_event_id is None or resume_event_id == "0-0":
            raise AssertionError(
                f"Could not capture a resumable stream event: {first.events}"
            )

        resumed = await self.read_basic_stream_replay(
            session_id,
            last_event_id=resume_event_id,
        )
        if not resumed.events:
            raise AssertionError("Stream resume returned no events")
        if not any(event.get("event") == "end" for event in resumed.events):
            raise AssertionError(f"Stream resume did not reach end: {resumed.events}")

    async def read_session(self, session_id: str) -> dict[str, Any]:
        payload = await self.get_json(
            f"/workspaces/{self.workspace_id}/agent/sessions/{session_id}"
        )
        if not isinstance(payload, dict):
            raise AssertionError("Agent session endpoint returned a non-object payload")
        return payload

    async def wait_for_approval_request(
        self,
        session_id: str,
        *,
        timeout_seconds: float = 180,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_session: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last_session = await self.read_session(session_id)
            for message in last_session.get("messages", []):
                if message.get("kind") == "approval-request" and message.get(
                    "approval"
                ):
                    return message["approval"]
            await asyncio.sleep(self.env.poll_interval_seconds)
        raise AssertionError(
            "Timed out waiting for approval request. Last session: "
            f"{_compact_json(last_session)}"
        )

    async def ensure_mcp_tool(self, kind: Literal["http", "stdio"]) -> McpToolSpec:
        if kind in self.mcp_cache:
            return self.mcp_cache[kind]

        if kind == "http":
            server_uri = DEFAULT_HTTP_MCP_URL
            if not _truthy(os.environ.get("CI")):
                server_uri = _default_http_mcp_url()
                _ensure_local_http_mcp_fixture()
            name = "tcsmokehttp"
            payload: dict[str, Any] = {
                "name": name,
                "description": "Agent smoke HTTP MCP fixture",
                "server_type": "http",
                "server_uri": server_uri,
                "auth_type": "NONE",
                "timeout": 30,
            }
            expected_prefix = "http:"
        else:
            name = "tcsmokestdio"
            payload = {
                "name": name,
                "description": "Agent smoke stdio MCP fixture",
                "server_type": "stdio",
                "stdio_command": "python",
                "stdio_args": [
                    "-c",
                    _python_exec_arg(
                        _mcp_fixture_source(prefix="stdio:", transport="stdio")
                    ),
                ],
                "timeout": 30,
            }
            expected_prefix = "stdio:"

        integration = await self.post_json(
            f"/workspaces/{self.workspace_id}/mcp-integrations",
            payload,
            expected={201},
        )
        server_name = (
            str(integration["name"]) if kind == "http" else str(integration["slug"])
        )
        spec = McpToolSpec(
            integration_id=str(integration["id"]),
            tool_name=f"mcp__{server_name}__tc_smoke_echo",
            expected_prefix=expected_prefix,
        )
        self.mcp_cache[kind] = spec
        return spec

    async def ensure_custom_registry_action(self) -> str:
        if self.custom_registry_action is not None:
            return self.custom_registry_action

        repositories = await self.get_json("/registry/repos")
        if not isinstance(repositories, list):
            raise AssertionError("/registry/repos returned an unexpected payload")
        local_repository = next(
            (
                repository
                for repository in repositories
                if repository.get("origin") == DEFAULT_LOCAL_REGISTRY_ORIGIN
            ),
            None,
        )
        if local_repository is None:
            _skip_or_fail("Enable the local agent smoke registry fixture")

        repository_id = str(local_repository["id"])
        repository = await self.get_json(f"/registry/repos/{repository_id}")
        if _repository_has_action(repository, DEFAULT_CUSTOM_REGISTRY_ACTION):
            object.__setattr__(
                self, "custom_registry_action", DEFAULT_CUSTOM_REGISTRY_ACTION
            )
            return DEFAULT_CUSTOM_REGISTRY_ACTION

        sync_result = await self.post_json(
            f"/registry/repos/{repository_id}/sync",
            {},
        )
        if sync_result.get("success") is not True:
            raise AssertionError(
                f"Custom registry sync failed: {_compact_json(sync_result)}"
            )

        repository = await self.get_json(f"/registry/repos/{repository_id}")
        if not _repository_has_action(repository, DEFAULT_CUSTOM_REGISTRY_ACTION):
            raise AssertionError(
                f"Custom registry fixture action {DEFAULT_CUSTOM_REGISTRY_ACTION!r} "
                f"not found in repository {repository_id}"
            )

        object.__setattr__(
            self, "custom_registry_action", DEFAULT_CUSTOM_REGISTRY_ACTION
        )
        return DEFAULT_CUSTOM_REGISTRY_ACTION

    async def create_workflow_agent_run(
        self,
        provider: ProviderSpec,
        *,
        prompt: str,
        session_id: str | None = None,
        actions: list[str] | None = None,
        tool_approvals: dict[str, bool] | None = None,
        mcp_integrations: list[str] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        workflow = await self.create_workflow_definition(
            provider,
            prompt=prompt,
            session_id=session_id,
            actions=actions,
            tool_approvals=tool_approvals,
            mcp_integrations=mcp_integrations,
        )
        workflow_id = str(workflow["id"])
        workflow_entity_id = str(WorkflowUUID.new(workflow_id))
        if session_id is not None:
            existing_session = await self.client.get(
                f"/workspaces/{self.workspace_id}/agent/sessions/{session_id}"
            )
            if existing_session.status_code == 404:
                await self.create_session(
                    title="Agent smoke workflow session",
                    entity_type="workflow",
                    entity_id=workflow_entity_id,
                    session_id=session_id,
                    tools=actions,
                )
            else:
                _raise_for_status(existing_session, expected={200})
        execution = await self.post_json(
            f"/workspaces/{self.workspace_id}/workflow-executions",
            {"workflow_id": workflow_id, "inputs": None},
        )
        execution_id = str(execution["wf_exec_id"])
        self.workflow_providers[execution_id] = provider
        return workflow_id, execution_id, execution

    async def create_workflow_preset_agent_run(
        self,
        provider: ProviderSpec,
        preset: dict[str, Any],
        *,
        prompt: str,
        session_id: str | None = None,
        actions: list[str] | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        workflow = await self.create_workflow_preset_agent_definition(
            preset,
            prompt=prompt,
            session_id=session_id,
            actions=actions,
        )
        workflow_id = str(workflow["id"])
        workflow_entity_id = str(WorkflowUUID.new(workflow_id))
        if session_id is not None:
            existing_session = await self.client.get(
                f"/workspaces/{self.workspace_id}/agent/sessions/{session_id}"
            )
            if existing_session.status_code == 404:
                await self.create_session(
                    title="Agent smoke preset workflow session",
                    entity_type="workflow",
                    entity_id=workflow_entity_id,
                    session_id=session_id,
                    tools=actions,
                    agent_preset_id=str(preset["id"]),
                )
            else:
                _raise_for_status(existing_session, expected={200})
        execution = await self.post_json(
            f"/workspaces/{self.workspace_id}/workflow-executions",
            {"workflow_id": workflow_id, "inputs": None},
        )
        execution_id = str(execution["wf_exec_id"])
        self.workflow_providers[execution_id] = provider
        return workflow_id, execution_id, execution

    async def create_workflow_definition(
        self,
        provider: ProviderSpec,
        *,
        prompt: str,
        session_id: str | None = None,
        actions: list[str] | None = None,
        tool_approvals: dict[str, bool] | None = None,
        mcp_integrations: list[str] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "user_prompt": prompt,
            "model": {
                "model_name": provider.model_name,
                "model_provider": provider.model_provider,
                "catalog_id": provider.catalog_id,
            },
            "max_requests": 8,
            "max_tool_calls": 3,
            "retries": 0,
            "enable_thinking": False,
            "model_settings": _workflow_model_settings(),
        }
        if session_id is not None:
            args["session_id"] = session_id
        if actions is not None:
            args["actions"] = actions
        if tool_approvals is not None:
            args["tool_approvals"] = tool_approvals
        if mcp_integrations is not None:
            args["mcp_integrations"] = mcp_integrations

        dsl = {
            "title": f"Agent smoke {uuid.uuid4().hex[:8]}",
            "description": "Automated live agent smoke workflow",
            "entrypoint": {"ref": "agent"},
            "actions": [{"ref": "agent", "action": "ai.agent", "args": args}],
            "returns": "${{ ACTIONS.agent.result }}",
        }
        files = {
            "file": (
                "agent-smoke.json",
                json.dumps({"definition": dsl}).encode(),
                "application/json",
            )
        }
        response = await self.client.post(
            f"/workspaces/{self.workspace_id}/workflows",
            files=files,
            data={"use_workflow_id": "false"},
        )
        _raise_for_status(response, expected={201})
        workflow = response.json()
        commit = await self.post_json(
            f"/workspaces/{self.workspace_id}/workflows/{workflow['id']}/commit",
            {},
        )
        if commit.get("status") != "success":
            raise AssertionError(f"Workflow commit failed: {_compact_json(commit)}")
        return workflow

    async def create_workflow_preset_agent_definition(
        self,
        preset: dict[str, Any],
        *,
        prompt: str,
        session_id: str | None = None,
        actions: list[str] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "preset": preset["slug"],
            "user_prompt": prompt,
            "max_requests": 8,
            "max_tool_calls": 3,
        }
        if session_id is not None:
            args["session_id"] = session_id
        if actions is not None:
            args["actions"] = actions

        dsl = {
            "title": f"Preset agent smoke {uuid.uuid4().hex[:8]}",
            "description": "Automated live preset agent workflow smoke",
            "entrypoint": {"ref": "preset_agent"},
            "actions": [
                {"ref": "preset_agent", "action": "ai.preset_agent", "args": args}
            ],
            "returns": "${{ ACTIONS.preset_agent.result }}",
        }
        files = {
            "file": (
                "preset-agent-smoke.json",
                json.dumps({"definition": dsl}).encode(),
                "application/json",
            )
        }
        response = await self.client.post(
            f"/workspaces/{self.workspace_id}/workflows",
            files=files,
            data={"use_workflow_id": "false"},
        )
        _raise_for_status(response, expected={201})
        workflow = response.json()
        commit = await self.post_json(
            f"/workspaces/{self.workspace_id}/workflows/{workflow['id']}/commit",
            {},
        )
        if commit.get("status") != "success":
            raise AssertionError(f"Workflow commit failed: {_compact_json(commit)}")
        return workflow

    async def wait_for_workflow(
        self,
        execution_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_seconds or self.env.workflow_timeout_seconds
        deadline = time.monotonic() + timeout
        last_payload: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            response = await self.client.get(
                f"/workspaces/{self.workspace_id}/workflow-executions/{execution_id}/compact"
            )
            if response.status_code == 404:
                await asyncio.sleep(self.env.poll_interval_seconds)
                continue
            _raise_for_status(response, expected={200})
            payload = response.json()
            if not isinstance(payload, dict):
                raise AssertionError(
                    "Workflow compact endpoint returned a non-object payload"
                )
            last_payload = payload
            status = str(last_payload.get("status"))
            if status in TERMINAL_WORKFLOW_STATUSES:
                if status != "COMPLETED":
                    raise AssertionError(
                        f"Workflow {execution_id} finished with {status}: "
                        f"{_compact_json(last_payload)}"
                    )
                self._record_workflow_usage(execution_id, last_payload)
                return last_payload
            await asyncio.sleep(self.env.poll_interval_seconds)
        raise AssertionError(
            f"Timed out waiting for workflow {execution_id}. "
            f"Last payload: {_compact_json(last_payload)}"
        )

    async def create_case(self, *, summary: str, description: str) -> dict[str, Any]:
        await self.post_json(
            f"/workspaces/{self.workspace_id}/cases",
            {
                "summary": summary,
                "description": description,
                "status": "new",
                "priority": "medium",
                "severity": "medium",
                "payload": {},
            },
            expected={201},
        )
        search = await self.get_json(
            f"/workspaces/{self.workspace_id}/cases/search",
            params={"search_term": summary, "limit": 10},
        )
        items = search.get("items", []) if isinstance(search, dict) else []
        for item in items:
            if item.get("summary") == summary:
                return item
        raise AssertionError(f"Created case not found for summary {summary!r}")

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        expected: set[int] | None = None,
    ) -> dict[str, Any] | list[Any]:
        response = await self.client.get(path, params=params)
        _raise_for_status(response, expected=expected or {200})
        return response.json()

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        expected: set[int] | None = None,
    ) -> dict[str, Any]:
        response = await self.client.post(path, json=payload)
        _raise_for_status(response, expected=expected or {200})
        if not response.content:
            return {}
        return response.json()

    async def put_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        expected: set[int] | None = None,
    ) -> dict[str, Any]:
        response = await self.client.put(path, json=payload)
        _raise_for_status(response, expected=expected or {200})
        if not response.content:
            return {}
        return response.json()

    def record(self, name: str, **data: Any) -> None:
        self.summary.append({"name": name, **data})

    def _record_workflow_usage(
        self, execution_id: str, compact_payload: dict[str, Any]
    ) -> None:
        if execution_id in self.recorded_usage_execution_ids:
            return
        usage = _collect_usage(compact_payload)
        if not usage:
            return
        provider = self.workflow_providers.get(execution_id)
        estimated_cost_usd = _estimate_cost_usd(usage, provider)
        self.record(
            "workflow_usage",
            execution_id=execution_id,
            provider=provider.name if provider else None,
            model=provider.model_name if provider else None,
            usage=usage,
            estimated_cost_usd=estimated_cost_usd,
        )
        self.recorded_usage_execution_ids.add(execution_id)


def smoke_provider_names() -> tuple[str, ...]:
    return SMOKE_PROVIDER_NAMES


def primary_provider_name() -> str:
    return PRIMARY_PROVIDER_NAME


def new_sentinel(prefix: str = "TC_SMOKE") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def assert_json_contains(payload: Any, value: str) -> None:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    assert value in serialized, f"{value!r} not found in payload: {serialized[:2000]}"


def latest_approval_tool_call_id(session: dict[str, Any]) -> str:
    for message in reversed(session.get("messages", [])):
        approval = message.get("approval")
        if message.get("kind") == "approval-request" and approval:
            return str(approval["tool_call_id"])
    raise AssertionError(
        f"No approval request found in session: {_compact_json(session)}"
    )


def assert_has_messages(session: dict[str, Any], minimum: int = 2) -> None:
    messages = session.get("messages")
    assert isinstance(messages, list), f"Session has no message list: {session}"
    assert len(messages) >= minimum, (
        f"Expected at least {minimum} persisted messages, got {len(messages)}"
    )


def _normalize_api_url(raw_url: str) -> str:
    url = raw_url.rstrip("/")
    return url if url.endswith("/api") else f"{url}/api"


def _xdist_worker_id() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "master")


def _worker_scoped_name(prefix: str) -> str:
    worker_id = _xdist_worker_id()
    return prefix if worker_id == "master" else f"{prefix}-{worker_id}"


def _default_http_mcp_url() -> str:
    return f"http://{_worker_scoped_name(HTTP_MCP_FIXTURE_CONTAINER_PREFIX)}:{HTTP_MCP_FIXTURE_PORT}/mcp"


def _default_custom_provider_base_url() -> str:
    return f"http://{_worker_scoped_name(CUSTOM_PROVIDER_SIDECAR_CONTAINER_PREFIX)}:{CUSTOM_PROVIDER_SIDECAR_PORT}/v1"


def _uses_local_custom_provider_sidecar(base_url: str) -> bool:
    normalized = base_url.rstrip("/")
    return normalized in {
        DEFAULT_CUSTOM_PROVIDER_BASE_URL.rstrip("/"),
        _default_custom_provider_base_url().rstrip("/"),
    }


def _mcp_fixture_source(
    *, prefix: Literal["http:", "stdio:"], transport: Literal["http", "stdio"]
) -> str:
    run_call = (
        'mcp.run(transport="http", host="0.0.0.0", port=8765)'
        if transport == "http"
        else "mcp.run()"
    )
    return f"""from fastmcp import FastMCP

mcp = FastMCP("Tracecat agent smoke MCP")

@mcp.tool
def tc_smoke_echo(marker: str) -> str:
    return {prefix!r} + marker

if __name__ == "__main__":
    {run_call}
"""


def _python_exec_arg(source: str) -> str:
    encoded = base64.b64encode(source.encode()).decode()
    return f"import base64\nexec(base64.b64decode({encoded!r}).decode())"


def _ensure_local_http_mcp_fixture() -> None:
    global _HTTP_MCP_FIXTURE_READY

    container_name = _worker_scoped_name(HTTP_MCP_FIXTURE_CONTAINER_PREFIX)
    if _HTTP_MCP_FIXTURE_READY and _http_mcp_fixture_ready(container_name):
        return
    if _http_mcp_fixture_ready(container_name):
        _register_local_fixture_container(container_name)
        _HTTP_MCP_FIXTURE_READY = True
        return

    if _container_exists(container_name):
        _remove_container(
            container_name,
            "Failed to remove stale HTTP MCP smoke fixture",
        )

    command = [
        str(_repo_root() / "scripts" / "cluster"),
        "run",
        "-d",
        "--name",
        container_name,
        "--no-deps",
        "api",
        "python",
        "-c",
        _mcp_fixture_source(prefix="http:", transport="http"),
    ]
    result = subprocess.run(
        command,
        cwd=_repo_root(),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        _skip_or_fail(
            f"Failed to start the HTTP MCP smoke fixture: {_subprocess_output(result)}"
        )
    _register_local_fixture_container(container_name)

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _http_mcp_fixture_ready(container_name):
            _HTTP_MCP_FIXTURE_READY = True
            return
        time.sleep(1)

    _skip_or_fail(
        f"Timed out waiting for {container_name} to listen on "
        f"port {HTTP_MCP_FIXTURE_PORT}"
    )


def _http_mcp_fixture_ready(container_name: str) -> bool:
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "python",
            "-c",
            (
                "import socket; "
                f"socket.create_connection(('127.0.0.1', {HTTP_MCP_FIXTURE_PORT}), timeout=2).close()"
            ),
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    return result.returncode == 0


def _ensure_local_custom_provider_sidecar(
    *,
    model_name: str,
    sidecar_model: str,
) -> None:
    global _CUSTOM_PROVIDER_SIDECAR_READY

    container_name = _worker_scoped_name(CUSTOM_PROVIDER_SIDECAR_CONTAINER_PREFIX)
    if _CUSTOM_PROVIDER_SIDECAR_READY and _custom_provider_sidecar_ready(
        container_name
    ):
        return

    if _container_exists(container_name):
        _remove_container(
            container_name,
            "Failed to remove stale custom provider LiteLLM sidecar",
        )

    sidecar_env = _custom_provider_sidecar_env()
    command = [
        str(_repo_root() / "scripts" / "cluster"),
        "run",
        "-d",
        "--name",
        container_name,
        "--no-deps",
    ]
    for env_name in CUSTOM_PROVIDER_SIDECAR_ENV_NAMES:
        if sidecar_env.get(env_name):
            command.extend(["-e", env_name])
    command.extend(
        [
            "api",
            "litellm",
            "--host",
            "0.0.0.0",
            "--port",
            CUSTOM_PROVIDER_SIDECAR_PORT,
            "--model",
            sidecar_model,
            "--alias",
            model_name,
        ]
    )

    result = subprocess.run(
        command,
        cwd=_repo_root(),
        env=sidecar_env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        _skip_or_fail(
            "Failed to start the custom provider LiteLLM sidecar: "
            f"{_subprocess_output(result)}"
        )
    _register_local_fixture_container(container_name)

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if _custom_provider_sidecar_ready(container_name):
            _CUSTOM_PROVIDER_SIDECAR_READY = True
            return
        time.sleep(2)

    _skip_or_fail(f"Timed out waiting for {container_name} to serve /v1/models")


def _custom_provider_sidecar_ready(container_name: str) -> bool:
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "python",
            "-c",
            (
                "import urllib.request; "
                f"urllib.request.urlopen('http://127.0.0.1:{CUSTOM_PROVIDER_SIDECAR_PORT}/v1/models', timeout=2).read()"
            ),
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    return result.returncode == 0


def _custom_provider_sidecar_env() -> dict[str, str]:
    return os.environ.copy()


def _container_exists(container_name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", container_name],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    return result.returncode == 0


def _remove_container(container_name: str, error_message: str) -> None:
    result = subprocess.run(
        ["docker", "rm", "-f", container_name],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        _skip_or_fail(f"{error_message}: {_subprocess_output(result)}")


def cleanup_local_agent_smoke_fixtures() -> None:
    """Best-effort cleanup for containers this pytest process started."""
    for container_name in tuple(sorted(_STARTED_LOCAL_FIXTURE_CONTAINERS)):
        result = subprocess.run(
            ["docker", "rm", "-f", container_name],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            _STARTED_LOCAL_FIXTURE_CONTAINERS.discard(container_name)


def _register_local_fixture_container(container_name: str) -> None:
    global _CLEANUP_REGISTERED

    _STARTED_LOCAL_FIXTURE_CONTAINERS.add(container_name)
    if not _CLEANUP_REGISTERED:
        atexit.register(cleanup_local_agent_smoke_fixtures)
        _CLEANUP_REGISTERED = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _subprocess_output(result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return output[-2000:] if output else f"exit code {result.returncode}"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _agent_smoke_lock(name: str) -> Iterator[None]:
    lock_dir = Path("/tmp/tracecat-agent-smoke-locks")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in name
    )
    lock_path = lock_dir / f"{lock_name}.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _is_duplicate_provider_secret_response(
    response: httpx.Response, provider_name: str
) -> bool:
    body = response.text
    return (
        "duplicate key value violates unique constraint" in body
        and f"agent-{provider_name}-credentials" in body
    )


def _skip_or_fail(message: str) -> NoReturn:
    if _truthy(os.environ.get("CI")):
        raise AssertionError(message)
    pytest.skip(message)


def _raise_for_status(response: httpx.Response, *, expected: Iterable[int]) -> None:
    expected_statuses = set(expected)
    if response.status_code in expected_statuses:
        return
    body = response.text[:2000]
    raise AssertionError(
        f"{response.request.method} {response.request.url} returned "
        f"{response.status_code}, expected {sorted(expected_statuses)}: {body}"
    )


def _parse_sse_data_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _compact_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)[:2000]


def _model_metadata(catalog_entry: dict[str, Any]) -> dict[str, Any] | None:
    metadata = catalog_entry.get("model_metadata")
    return metadata if isinstance(metadata, dict) else None


def _repository_has_action(repository: Any, action: str) -> bool:
    actions = repository.get("actions", []) if isinstance(repository, dict) else []
    return any(
        action_item.get("action") == action
        for action_item in actions
        if isinstance(action_item, dict)
    )


def _workflow_model_settings() -> dict[str, Any]:
    return dict(DEFAULT_WORKFLOW_MODEL_SETTINGS)


def _collect_usage(payload: Any) -> dict[str, int]:
    totals: dict[str, int] = dict.fromkeys(USAGE_FIELDS, 0)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if _looks_like_agent_output(value):
                if isinstance(usage := value.get("usage"), dict):
                    _add_usage(totals, usage)
                return
            if isinstance(usage := value.get("usage"), dict):
                _add_usage(totals, usage)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return {key: value for key, value in totals.items() if value}


def _append_summary(path: Path, runs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        existing_runs: list[dict[str, Any]] = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
            if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                existing_runs = [
                    item for item in existing["runs"] if isinstance(item, dict)
                ]

        merged_runs = [*existing_runs, *runs]
        path.write_text(
            json.dumps(
                {
                    "runs": merged_runs,
                    "totals": _summary_totals(merged_runs),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        fcntl.flock(lock_file, fcntl.LOCK_UN)


def _looks_like_agent_output(value: dict[str, Any]) -> bool:
    return {"output", "duration", "usage", "session_id"}.issubset(value)


def _add_usage(totals: dict[str, int], usage: dict[str, Any]) -> None:
    for field_name in USAGE_FIELDS:
        field_value = usage.get(field_name)
        if isinstance(field_value, int):
            totals[field_name] += field_value


def _summary_totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    usage_totals: dict[str, int] = dict.fromkeys(USAGE_FIELDS, 0)
    estimated_cost_usd = 0.0
    saw_estimated_cost = False
    for run in runs:
        if isinstance(usage := run.get("usage"), dict):
            for field_name in USAGE_FIELDS:
                field_value = usage.get(field_name)
                if isinstance(field_value, int):
                    usage_totals[field_name] += field_value
        cost_value = run.get("estimated_cost_usd")
        if isinstance(cost_value, int | float):
            estimated_cost_usd += float(cost_value)
            saw_estimated_cost = True

    return {
        "usage": {key: value for key, value in usage_totals.items() if value},
        "estimated_cost_usd": round(estimated_cost_usd, 6)
        if saw_estimated_cost
        else None,
    }


def _estimate_cost_usd(
    usage: dict[str, int], provider: ProviderSpec | None
) -> float | None:
    if provider is None or provider.model_metadata is None:
        return None
    input_cost = _float_or_none(provider.model_metadata.get("input_cost_per_token"))
    output_cost = _float_or_none(provider.model_metadata.get("output_cost_per_token"))
    if input_cost is None and output_cost is None:
        return None
    estimated = 0.0
    if input_cost is not None:
        estimated += usage.get("input_tokens", 0) * input_cost
    if output_cost is not None:
        estimated += usage.get("output_tokens", 0) * output_cost
    return round(estimated, 6)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _default_agent_instructions() -> str:
    return (
        "You are running an automated Tracecat smoke test. Follow the user's "
        "instruction exactly. Prefer the named tool when the user asks you to "
        "use a tool. Keep final answers short and include requested sentinel "
        "strings verbatim."
    )
