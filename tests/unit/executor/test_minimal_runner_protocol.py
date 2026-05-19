from __future__ import annotations

import asyncio
import io
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import orjson
import pytest
from pydantic import BaseModel

from tracecat.executor import minimal_runner

# --- Action Gateway compatibility regressions ---


def test_action_gateway_sdk_transport_patches_legacy_tracecat_client(
    monkeypatch,
) -> None:
    """Legacy SDK clients are patched to use the executor-local gateway.

    Cached registry artifacts can contain SDKs from before
    `_request_url_and_transport` existed. Those clients would otherwise keep
    calling `/internal` over `TRACECAT__API_URL`; the minimal runner shim must
    replace only their `request()` method and send traffic through the UDS.
    """
    captured: dict[str, Any] = {}
    sdk_client: Any = types.ModuleType("tracecat_registry.sdk.client")

    class LegacyTracecatClient:
        """Old SDK shape: request exists, gateway transport helper does not."""

        def __init__(self) -> None:
            self._timeout = 12.0

        def _get_headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer executor-token"}

        def _handle_error_response(self, response: httpx.Response) -> None:
            raise AssertionError(response.status_code)

        async def request(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("original request should be patched")

    class FakeTransport:
        def __init__(self, *, uds: str) -> None:
            captured["uds"] = uds

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            transport: FakeTransport,
            timeout: float,
        ) -> None:
            captured["transport"] = transport
            captured["timeout"] = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            params: dict[str, Any] | None,
            json: Any | None,
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json={"ok": True})

    def import_module(name: str) -> Any:
        if name == "tracecat_registry.sdk.client":
            return sdk_client
        raise ImportError(name)

    sdk_client.TracecatClient = LegacyTracecatClient
    # The gateway socket is the only signal the minimal runner uses to enable
    # legacy SDK patching.
    monkeypatch.setattr(
        minimal_runner,
        "_ACTION_GATEWAY_SOCKET",
        "/var/run/tracecat/action-gateway.sock",
    )
    monkeypatch.setattr(minimal_runner.importlib, "import_module", import_module)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    minimal_runner._install_action_gateway_sdk_transport()
    result = asyncio.run(
        LegacyTracecatClient().request(
            "GET",
            "/cases",
            params={"limit": 1},
        )
    )

    assert result == {"ok": True}
    assert captured["uds"] == "/var/run/tracecat/action-gateway.sock"
    # The hostname is intentionally synthetic: httpx ignores it when a UDS
    # transport is supplied, while FastAPI still receives the `/internal` path.
    assert captured["timeout"] == 12.0
    assert captured["method"] == "GET"
    assert captured["url"] == "http://tracecat-action-gateway/internal/cases"
    assert captured["params"] == {"limit": 1}
    assert captured["headers"]["Authorization"] == "Bearer executor-token"


def test_action_gateway_sdk_transport_leaves_current_tracecat_client_unpatched(
    monkeypatch,
) -> None:
    """Current SDK clients keep native request and transport behavior.

    Newer SDKs expose `_request_url_and_transport`, so the minimal runner should
    not monkeypatch them. This prevents the compatibility shim from overriding
    the current SDK's own env/keyword handling.
    """
    sdk_client: Any = types.ModuleType("tracecat_registry.sdk.client")

    class CurrentTracecatClient:
        """Current SDK shape: native gateway transport helper is present."""

        def _request_url_and_transport(self) -> None:
            return None

        async def request(self) -> str:
            return "original"

    def import_module(name: str) -> Any:
        if name == "tracecat_registry.sdk.client":
            return sdk_client
        raise ImportError(name)

    sdk_client.TracecatClient = CurrentTracecatClient
    monkeypatch.setattr(
        minimal_runner,
        "_ACTION_GATEWAY_SOCKET",
        "/var/run/tracecat/action-gateway.sock",
    )
    monkeypatch.setattr(minimal_runner.importlib, "import_module", import_module)

    minimal_runner._install_action_gateway_sdk_transport()

    assert asyncio.run(CurrentTracecatClient().request()) == "original"


def test_run_udf_supports_legacy_registry_context_and_sdk_gateway_transport(
    monkeypatch,
) -> None:
    """Old cached artifacts still run when the executor gateway socket is set.

    This mirrors the EU failure mode: old `RegistryContext` dataclasses do not
    accept `action_gateway_socket`, and old `TracecatClient` classes do not know
    how to select the gateway transport. `_run_udf` must keep the socket out of
    the context constructor while patching SDK requests through the UDS.
    """
    captured: dict[str, Any] = {}

    # Build a fake old tracecat_registry package tree so `_run_udf` exercises
    # the same import boundary it uses for cached registry artifacts.
    registry_pkg: Any = types.ModuleType("tracecat_registry")
    registry_pkg.__path__ = []
    context_module: Any = types.ModuleType("tracecat_registry.context")
    sdk_pkg: Any = types.ModuleType("tracecat_registry.sdk")
    sdk_pkg.__path__ = []
    sdk_client: Any = types.ModuleType("tracecat_registry.sdk.client")
    internal_pkg: Any = types.ModuleType("tracecat_registry._internal")
    internal_pkg.__path__ = []
    secrets_module: Any = types.ModuleType("tracecat_registry._internal.secrets")
    action_module: Any = types.ModuleType("legacy_action_module")

    @dataclass
    class LegacyRegistryContext:
        """Pre-gateway RegistryContext shape.

        This intentionally has the old dataclass fields and no
        `action_gateway_socket` field. Passing that keyword would raise the
        same TypeError seen in EU.
        """

        workspace_id: str
        workflow_id: str
        run_id: str
        wf_exec_id: str | None = None
        environment: str = "default"
        api_url: str = "http://api:8000"
        executor_url: str = "http://executor:8000"
        token: str = ""

    def set_context(ctx: LegacyRegistryContext) -> None:
        captured["registry_context"] = ctx

    class LegacyTracecatClient:
        """Old SDK shape used by pinned registry artifacts."""

        def __init__(self) -> None:
            self._timeout = 12.0

        def _get_headers(self) -> dict[str, str]:
            return {"Authorization": "Bearer executor-token"}

        def _handle_error_response(self, response: httpx.Response) -> None:
            raise AssertionError(response.status_code)

        async def request(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("legacy SDK request should be patched")

    class FakeTransport:
        def __init__(self, *, uds: str) -> None:
            captured["uds"] = uds

    class FakeAsyncClient:
        def __init__(
            self,
            *,
            transport: FakeTransport,
            timeout: float,
        ) -> None:
            captured["transport"] = transport
            captured["timeout"] = timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(
            self,
            method: str,
            url: str,
            *,
            params: dict[str, Any] | None,
            json: Any | None,
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json={"ok": True})

    def action() -> Any:
        # `_run_udf` installs the shim before importing/calling the action, so
        # this request should be routed through the gateway despite using the
        # legacy client class.
        return asyncio.run(
            LegacyTracecatClient().request(
                "GET",
                "/cases",
                params={"limit": 1},
            )
        )

    context_module.RegistryContext = LegacyRegistryContext
    context_module.set_context = set_context
    sdk_client.TracecatClient = LegacyTracecatClient
    secrets_module.set_context = lambda _secret_env: "secret-token"
    secrets_module.reset_context = lambda _token: None
    action_module.action = action

    # Make normal imports inside `_run_udf` resolve to the fake old package.
    for name, module in {
        "tracecat_registry": registry_pkg,
        "tracecat_registry.context": context_module,
        "tracecat_registry.sdk": sdk_pkg,
        "tracecat_registry.sdk.client": sdk_client,
        "tracecat_registry._internal": internal_pkg,
        "tracecat_registry._internal.secrets": secrets_module,
        "legacy_action_module": action_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    # Simulate the executor environment that originally triggered the failure.
    monkeypatch.setattr(minimal_runner, "_API_URL", "http://api:8000")
    monkeypatch.setattr(
        minimal_runner,
        "_ACTION_GATEWAY_SOCKET",
        "/var/run/tracecat/action-gateway.sock",
    )
    monkeypatch.setenv("TRACECAT__WORKSPACE_ID", "workspace-id")
    monkeypatch.setenv("TRACECAT__WORKFLOW_ID", "workflow-id")
    monkeypatch.setenv("TRACECAT__RUN_ID", "run-id")
    monkeypatch.setenv("TRACECAT__EXECUTOR_TOKEN", "executor-token")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = minimal_runner._run_udf(
        {"module": "legacy_action_module", "name": "action"},
        {},
        {},
    )

    # The result proves both halves of the compatibility path:
    # - RegistryContext accepted the constructor because no socket kw was passed.
    # - LegacyTracecatClient.request was patched onto the UDS transport.
    assert result == {"ok": True}
    registry_context = captured["registry_context"]
    assert registry_context == LegacyRegistryContext(
        workspace_id="workspace-id",
        workflow_id="workflow-id",
        run_id="run-id",
        api_url="http://api:8000",
        token="executor-token",
    )
    assert captured["uds"] == "/var/run/tracecat/action-gateway.sock"
    assert captured["url"] == "http://tracecat-action-gateway/internal/cases"
    assert captured["params"] == {"limit": 1}
    assert captured["headers"]["Authorization"] == "Bearer executor-token"


def test_main_minimal_suppresses_action_stdout_and_stderr(monkeypatch) -> None:
    test_module: Any = types.ModuleType("test_module")

    def noisy_action() -> dict[str, str]:
        print("noisy stdout line")
        print("noisy stderr line", file=sys.stderr)
        return {"ok": "yes"}

    test_module.noisy_action = noisy_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "noisy_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {},
        }
    )

    assert result == {"success": True, "result": {"ok": "yes"}}


def test_main_minimal_returns_structured_error_for_action_exceptions(
    monkeypatch,
) -> None:
    test_module: Any = types.ModuleType("test_module")

    def boom_action() -> None:
        raise ValueError("boom")

    test_module.boom_action = boom_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "boom_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {},
        }
    )

    assert result["success"] is False
    assert result["error"]["type"] == "ValueError"
    assert result["error"]["message"] == "boom"


def test_capped_text_buffer_limits_memory_growth() -> None:
    buf = minimal_runner._CappedTextBuffer(limit=5)

    written = buf.write("abcdefgh")

    assert written == 8
    assert buf.getvalue() == "abcde"
    assert buf.truncated is True


def test_main_minimal_masks_secrets_in_suppressed_output(monkeypatch) -> None:
    """Regression: secrets printed by actions must be masked in suppressed output notices."""
    test_module: Any = types.ModuleType("test_module")

    def leaky_action() -> dict[str, str]:
        print(
            "key=AKIAIOSFODNN7EXAMPLE token=IQoJb3JpZ2luX2VjEBAReallyLongSessionToken"
        )
        return {"ok": "yes"}

    test_module.leaky_action = leaky_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    warnings_emitted: list[str] = []
    monkeypatch.setattr(
        minimal_runner.warnings,
        "warn",
        lambda msg, *a, **kw: warnings_emitted.append(msg),
    )

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "leaky_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {
                "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
                "AWS_SESSION_TOKEN": "IQoJb3JpZ2luX2VjEBAReallyLongSessionToken",
            },
        }
    )

    assert result == {"success": True, "result": {"ok": "yes"}}
    assert len(warnings_emitted) == 1
    assert "AKIAIOSFODNN7EXAMPLE" not in warnings_emitted[0]
    assert "IQoJb3JpZ2luX2VjEBAReallyLongSessionToken" not in warnings_emitted[0]
    assert "***" in warnings_emitted[0]


def test_main_minimal_masks_non_string_secret_env_values(monkeypatch) -> None:
    test_module: Any = types.ModuleType("test_module")

    def leaky_action() -> dict[str, str]:
        print("port=443 enabled=True")
        return {"ok": "yes"}

    test_module.leaky_action = leaky_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    warnings_emitted: list[str] = []
    monkeypatch.setattr(
        minimal_runner.warnings,
        "warn",
        lambda msg, *a, **kw: warnings_emitted.append(msg),
    )

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "leaky_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {
                "PORT": 443,
                "ENABLED": True,
            },
        }
    )

    assert result == {"success": True, "result": {"ok": "yes"}}
    assert len(warnings_emitted) == 1
    assert "443" not in warnings_emitted[0]
    assert "True" not in warnings_emitted[0]
    assert warnings_emitted[0].count("***") == 2


def test_main_minimal_stringifies_secret_env_for_registry_context(monkeypatch) -> None:
    test_module: Any = types.ModuleType("test_module")

    def secret_action() -> dict[str, str]:
        from tracecat_registry._internal import secrets

        return {
            "port": secrets.get("PORT").strip(),
            "enabled": secrets.get("ENABLED").strip(),
        }

    test_module.secret_action = secret_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "secret_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {
                "PORT": 443,
                "ENABLED": True,
            },
        }
    )

    assert result == {
        "success": True,
        "result": {"port": "443", "enabled": "True"},
    }


def test_main_minimal_errors_when_secret_value_stringify_fails(monkeypatch) -> None:
    test_module: Any = types.ModuleType("test_module")

    def quiet_action() -> dict[str, str]:
        return {"ok": "yes"}

    class BrokenSecret:
        def __str__(self) -> str:
            raise RuntimeError("broken __str__")

    test_module.quiet_action = quiet_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "quiet_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {"BROKEN": BrokenSecret()},
        }
    )

    assert result["success"] is False
    assert result["error"]["type"] == "TypeError"
    assert "BROKEN" in result["error"]["message"]
    assert "BrokenSecret" in result["error"]["message"]


def test_main_minimal_still_succeeds_when_warnings_raise(monkeypatch) -> None:
    test_module: Any = types.ModuleType("test_module")

    def noisy_action() -> dict[str, str]:
        print("stdout noise")
        return {"ok": "yes"}

    test_module.noisy_action = noisy_action

    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )

    def raise_runtime_warning(*_args, **_kwargs) -> None:
        raise RuntimeWarning("warnings are treated as errors")

    monkeypatch.setattr(minimal_runner.warnings, "warn", raise_runtime_warning)
    fake_stderr = io.StringIO()
    monkeypatch.setattr(minimal_runner.sys, "stderr", fake_stderr)

    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "noisy_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {},
        }
    )

    assert result == {"success": True, "result": {"ok": "yes"}}
    assert "Action emitted stdout output that was suppressed" in fake_stderr.getvalue()


# --- Regression tests for Pydantic model serialization (PatronusCase bug) ---


class _SimpleModel(BaseModel):
    id: str = "case-123"
    title: str = "Test case"


class _NestedModel(BaseModel):
    inner: _SimpleModel = _SimpleModel()
    items: list[_SimpleModel] = [_SimpleModel(), _SimpleModel()]


class _ModelWithRichTypes(BaseModel):
    ts: datetime = datetime(2026, 1, 1, 12, 0, 0)
    uid: UUID = UUID("12345678-1234-5678-1234-567812345678")
    amount: Decimal = Decimal("99.99")
    tags: set[str] = {"a", "b"}


def test_json_dumps_serializes_pydantic_model():
    """Regression: actions returning Pydantic models must serialize without error."""
    result = {"success": True, "result": _SimpleModel()}
    data = orjson.loads(minimal_runner.json_dumps(result))
    assert data == {"success": True, "result": {"id": "case-123", "title": "Test case"}}


def test_json_dumps_serializes_nested_pydantic_model():
    result = {"success": True, "result": _NestedModel()}
    data = orjson.loads(minimal_runner.json_dumps(result))
    assert data["result"]["inner"] == {"id": "case-123", "title": "Test case"}
    assert len(data["result"]["items"]) == 2


def test_json_dumps_serializes_pydantic_model_with_rich_types():
    """model_dump(mode='json') converts datetime/UUID/Decimal/set to JSON primitives."""
    result = {"success": True, "result": _ModelWithRichTypes()}
    data = orjson.loads(minimal_runner.json_dumps(result))
    r = data["result"]
    assert isinstance(r["ts"], str)
    assert isinstance(r["uid"], str)
    assert isinstance(r["tags"], list)
    assert set(r["tags"]) == {"a", "b"}


def test_json_dumps_rejects_non_serializable_object():
    """Objects without model_dump must fail closed, not leak via __dict__."""

    class _OpaqueObject:
        def __init__(self):
            self._token = "secret-jwt"

    result = {"success": True, "result": _OpaqueObject()}
    with pytest.raises(TypeError, match="Type is not JSON serializable"):
        minimal_runner.json_dumps(result)


def test_json_dumps_rejects_broken_model_dump():
    """If model_dump exists but raises, fall through to TypeError."""

    class _FakeModel:
        def model_dump(self, **kwargs):
            raise RuntimeError("not a real model")

    result = {"success": True, "result": _FakeModel()}
    with pytest.raises(TypeError, match="Type is not JSON serializable"):
        minimal_runner.json_dumps(result)
