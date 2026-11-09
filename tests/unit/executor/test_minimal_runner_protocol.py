from __future__ import annotations

import asyncio
import errno
import io
import re
import subprocess
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import orjson
import pytest
from pydantic import BaseModel, field_serializer

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


@pytest.mark.parametrize("outcome", ["quiet", "noisy", "error"])
def test_main_minimal_compiles_masks_only_for_diagnostics(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    original = re.compile
    compiled: list[str] = []
    notices: list[str] = []

    def counted(pattern: str) -> re.Pattern[str]:
        compiled.append(pattern)
        return original(pattern)

    def action(*_args: object, **_kwargs: object) -> str:
        if outcome == "error":
            raise ValueError("rejected synthetic-secret")
        if outcome == "noisy":
            print("stdout synthetic-secret")
            print("stderr synthetic-secret", file=sys.stderr)
        return "ok"

    monkeypatch.setattr(minimal_runner, "run_action_minimal", action)
    # Isolate the spy from regex use elsewhere in the process.
    monkeypatch.setattr(
        minimal_runner, "re", types.SimpleNamespace(compile=counted, escape=re.escape)
    )
    monkeypatch.setattr(
        minimal_runner.warnings,
        "warn",
        lambda message, *_args, **_kwargs: notices.append(message),
    )
    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {"type": "udf", "module": "synthetic", "name": "run"},
                "evaluated_args": {},
            },
            "secret_mask_values": ["synthetic-secret"],
        }
    )
    assert len(compiled) == (0 if outcome == "quiet" else 1)
    if outcome == "error":
        assert result["error"]["message"] == "rejected ***"
    else:
        assert result == {"success": True, "result": "ok"}
    assert len(notices) == (2 if outcome == "noisy" else 0)
    assert all(
        "synthetic-secret" not in notice and "***" in notice for notice in notices
    )


def test_lazy_mask_compilation_preserves_resource_limit_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def action(*_args: object, **_kwargs: object) -> None:
        raise ValueError("rejected synthetic-secret")

    def exhausted(pattern: str) -> re.Pattern[str]:
        raise MemoryError

    monkeypatch.setattr(minimal_runner, "run_action_minimal", action)
    monkeypatch.setattr(
        minimal_runner, "re", types.SimpleNamespace(compile=exhausted, escape=re.escape)
    )
    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {"type": "udf", "module": "synthetic", "name": "run"},
                "evaluated_args": {},
            },
            "secret_mask_values": ["synthetic-secret"],
        }
    )
    assert result["success"] is False
    assert result["error_code"] == "resource_limit_exceeded"
    assert "synthetic-secret" not in result["error"]["message"]


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
    # The error reports the invalid env entry without invoking its repr.
    assert result["error"]["type"] == "TypeError"
    assert "Failed to stringify secret env value" in result["error"]["message"]
    assert "BROKEN" in result["error"]["message"]


def test_main_minimal_keeps_error_message_when_withholding_disabled(
    monkeypatch,
) -> None:
    test_module: Any = types.ModuleType("test_module")

    def failing_action() -> None:
        raise ValueError("upstream rejected the request")

    test_module.failing_action = failing_action

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
                    "name": "failing_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {"API_KEY": "sk-test"},
            "unsafe_disable_secret_error_withholding": True,
        }
    )

    assert result["success"] is False
    assert result["error"]["type"] == "ValueError"
    assert result["error"]["message"] == "upstream rejected the request"


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


def test_main_minimal_reports_memory_error_as_resource_limit(
    monkeypatch,
) -> None:
    """Invariant: an action's MemoryError carries the resource-limit envelope code.

    The action path raises the typed sandbox exception from this code before
    it ever parses the structured error, so the code is what the host reads.
    """
    test_module: Any = types.ModuleType("test_module")

    def hungry_action() -> None:
        raise MemoryError()

    test_module.hungry_action = hungry_action

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
                    "name": "hungry_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {},
        }
    )

    assert result["success"] is False
    assert result["error_code"] == "resource_limit_exceeded"
    assert result["error"]["type"] == "MemoryError"
    assert result["error"]["action_name"] == "test_module.hungry_action"


def test_resource_limit_from_error_ignores_other_oserrors() -> None:
    """Unrelated syscall failures must retain their ordinary classification."""
    assert minimal_runner.resource_limit_from_error(MemoryError()) == "memory"
    assert (
        minimal_runner.resource_limit_from_error(OSError(errno.ENOMEM, "no memory"))
        == "memory"
    )
    assert (
        minimal_runner.resource_limit_from_error(OSError(errno.EFBIG, "too large"))
        == "file_size"
    )
    assert not minimal_runner.resource_limit_from_error(
        OSError(errno.EAGAIN, "would block")
    )
    assert not minimal_runner.resource_limit_from_error(
        OSError(errno.ENOENT, "missing")
    )
    assert not minimal_runner.resource_limit_from_error(ValueError("nope"))


class _HungryModelDump:
    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        raise MemoryError()


def _hungry_pydantic_model(error: Exception) -> BaseModel:
    class HungryModel(BaseModel):
        value: int = 1

        @field_serializer("value")
        def serialize_value(self, value: int) -> int:
            raise error

    return HungryModel()


@pytest.mark.parametrize(
    "make_result",
    [
        pytest.param(_HungryModelDump, id="model_dump_hook"),
        pytest.param(
            lambda: _hungry_pydantic_model(MemoryError()), id="pydantic_memory_error"
        ),
        pytest.param(
            lambda: _hungry_pydantic_model(OSError(errno.ENOMEM, "no memory")),
            id="pydantic_enomem",
        ),
    ],
)
def test_serialize_result_preserves_memory_exhaustion_in_serializers(
    make_result: Callable[[], object],
) -> None:
    """Invariant: serializer hooks must not swallow memory exhaustion.

    ``_orjson_default`` treats a failing ``model_dump()`` as an unserializable
    type and raises ``TypeError``. A ``MemoryError`` raised in there is the
    address-space cap, not a type problem, so swallowing it would strip the
    resource-limit code off a failure that really did hit the cap.
    """
    encoded = minimal_runner.serialize_result(
        {"success": True, "result": make_result()},
        {"resolved_context": {"action_impl": {"module": "m", "name": "n"}}},
    )
    decoded = orjson.loads(encoded)

    assert decoded["success"] is False
    assert decoded["error_code"] == "resource_limit_exceeded"


def test_serialize_result_degrades_to_resource_limit_envelope_on_memory_error(
    monkeypatch,
) -> None:
    """Invariant: a MemoryError while serializing still reports the limit code.

    The allocation that dies can be the serialization itself, long after the
    action returned. Without this fallback the process exits before writing
    result.json and the host sees only a generic workload failure.
    """
    calls: list[dict[str, Any]] = []
    real_json_dumps = minimal_runner.json_dumps

    def flaky_json_dumps(obj: dict[str, Any]) -> bytes:
        calls.append(obj)
        if len(calls) == 1:
            raise MemoryError()
        return real_json_dumps(obj)

    monkeypatch.setattr(minimal_runner, "json_dumps", flaky_json_dumps)

    payload = minimal_runner.serialize_result(
        {"success": True, "result": "an oversized value"},
        {
            "resolved_context": {
                "action_impl": {"module": "test_module", "name": "hungry_action"}
            }
        },
    )

    decoded = orjson.loads(payload)
    assert decoded["success"] is False
    assert decoded["error_code"] == "resource_limit_exceeded"
    assert decoded["error"]["type"] == "MemoryError"
    assert decoded["error"]["action_name"] == "test_module.hungry_action"


def test_main_minimal_masks_error_when_secrets_in_scope(monkeypatch) -> None:
    """Known secrets are masked without discarding the diagnostic."""
    test_module: Any = types.ModuleType("test_module")

    def boom_action() -> None:
        raise ValueError("invalid literal: 'sk_live_CANARY\nMULTILINE'")

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
            "secret_env": {"API_KEY": "sk_live_CANARY\nMULTILINE"},
        }
    )

    assert result["success"] is False
    assert result["error"]["type"] == "ValueError"
    assert "CANARY" not in result["error"]["message"]
    assert result["error"]["message"] == "invalid literal: '***'"


@pytest.mark.parametrize("escaped", [False, True])
def test_main_minimal_masks_observed_derived_values_without_env_secrets(
    monkeypatch, escaped: bool
) -> None:
    """Host-observed values cross the subprocess protocol even without env secrets."""
    derived = "encoded-derived-value\nsecond-line"
    test_module = types.ModuleType("test_module")

    def fail() -> None:
        value = repr(derived) if escaped else derived
        raise ValueError(f"upstream rejected {value}")

    monkeypatch.setattr(test_module, "fail", fail, raising=False)
    monkeypatch.setattr(
        minimal_runner.importlib, "import_module", lambda *_args: test_module
    )
    result = minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {"type": "udf", "module": "test_module", "name": "fail"},
                "evaluated_args": {},
            },
            "secret_env": {},
            "secret_mask_values": [derived, repr(derived)[1:-1]],
        }
    )
    assert result["success"] is False
    assert "upstream rejected" in result["error"]["message"]
    assert "encoded-derived-value" not in result["error"]["message"]
    assert "***" in result["error"]["message"]


# --- Action function timeout ---


def _run_timed_action(
    monkeypatch: pytest.MonkeyPatch,
    fn: Any,
    *,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    test_module: Any = types.ModuleType("test_module")
    test_module.timed_action = fn
    monkeypatch.setattr(
        minimal_runner.importlib,
        "import_module",
        lambda _p, *args, **kwargs: test_module,
    )
    return minimal_runner.main_minimal(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "test_module",
                    "name": "timed_action",
                },
                "evaluated_args": {},
            },
            "secret_env": {},
            "timeout_seconds": timeout_seconds,
        }
    )


def test_main_minimal_times_out_async_action(monkeypatch) -> None:
    async def slow_action() -> str:
        await asyncio.sleep(10)
        return "late"

    result = _run_timed_action(monkeypatch, slow_action, timeout_seconds=0.05)

    assert result["success"] is False
    assert result["error"]["type"] == "ActionTimeoutError"
    assert result["error"]["message"] == "Action execution timed out after 0.05s"


def test_main_minimal_times_out_blocking_sync_action(monkeypatch) -> None:
    import time

    def slow_action() -> str:
        time.sleep(10)
        return "late"

    result = _run_timed_action(monkeypatch, slow_action, timeout_seconds=0.05)

    assert result["success"] is False
    assert result["error"]["type"] == "ActionTimeoutError"


def test_main_minimal_times_out_async_action_that_blocks_the_loop(
    monkeypatch,
) -> None:
    """An async action that blocks past its deadline fails instead of succeeding late."""
    import time

    async def blocking_action() -> str:
        time.sleep(0.1)
        return "late"

    result = _run_timed_action(monkeypatch, blocking_action, timeout_seconds=0.02)

    assert result["success"] is False
    assert result["error"]["type"] == "ActionTimeoutError"
    assert result["error"]["message"] == "Action execution timed out after 0.02s"


def test_main_minimal_times_out_sync_action_that_swallows_the_alarm(
    monkeypatch,
) -> None:
    """A sync action that catches the timeout and returns late still fails."""
    import time

    def swallowing_action() -> str:
        try:
            time.sleep(10)
        except Exception:
            time.sleep(0.05)
        return "late"

    result = _run_timed_action(monkeypatch, swallowing_action, timeout_seconds=0.02)

    assert result["success"] is False
    assert result["error"]["type"] == "ActionTimeoutError"
    assert result["error"]["message"] == "Action execution timed out after 0.02s"


def test_main_minimal_keeps_action_raised_timeout_error(monkeypatch) -> None:
    async def action_with_own_timeout() -> None:
        async with asyncio.timeout(0.01):
            await asyncio.sleep(10)

    result = _run_timed_action(monkeypatch, action_with_own_timeout, timeout_seconds=5)

    assert result["success"] is False
    assert result["error"]["type"] == "TimeoutError"


def test_main_minimal_timeout_disarms_after_sync_action(monkeypatch) -> None:
    import signal

    previous = signal.getsignal(signal.SIGALRM)

    result = _run_timed_action(monkeypatch, lambda: "ok", timeout_seconds=5)

    assert result == {"success": True, "result": "ok"}
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) is previous


def test_main_minimal_without_timeout_runs_to_completion(monkeypatch) -> None:
    async def action() -> str:
        await asyncio.sleep(0)
        return "done"

    result = _run_timed_action(monkeypatch, action, timeout_seconds=None)

    assert result == {"success": True, "result": "done"}


def test_sandbox_runner_skips_heavy_imports_for_plain_builtin_action() -> None:
    """A builtin action without secrets starts without pydantic or httpx's CLI.

    Every sandboxed call pays its imports in a fresh interpreter. The secret
    models (pydantic) load on first access and the runner blocks httpx's
    optional CLI module (rich, click, pygments), which together cost roughly
    100 ms per call. The gateway socket is set so the SDK client, and with it
    httpx, is imported as it is in production.
    """
    payload = orjson.dumps(
        {
            "resolved_context": {
                "action_impl": {
                    "type": "udf",
                    "module": "tracecat_registry.core.transform",
                    "name": "deduplicate",
                },
                "evaluated_args": {
                    "items": [{"a": 3}, {"a": 3}, {"b": 3}],
                    "keys": [],
                    "persist": False,
                },
            },
        }
    )
    # The runner ends with os._exit, so report from inside that call.
    probe = (
        "import os, runpy, sys\n"
        "heavy = (\n"
        "    'pydantic', 'rich', 'click', 'pygments',\n"
        "    'tracecat_registry._internal.models',\n"
        ")\n"
        "_exit = os._exit\n"
        "def report_then_exit(code):\n"
        "    sys.stderr.write(repr(sorted(m for m in heavy if m in sys.modules)))\n"
        "    sys.stderr.flush()\n"
        "    _exit(code)\n"
        "os._exit = report_then_exit\n"
        f"runpy.run_path({minimal_runner.__file__!r}, run_name='__main__')\n"
    )

    proc = subprocess.run(
        [sys.executable, "-c", probe],
        input=payload,
        capture_output=True,
        env={"TRACECAT__ACTION_GATEWAY_SOCKET": "/nonexistent/gateway.sock"},
        check=True,
        timeout=60,
    )

    assert orjson.loads(proc.stdout) == {"success": True, "result": [{"a": 3, "b": 3}]}
    assert proc.stderr.decode().strip() == "[]"


def test_runner_done_marker_matches_the_sandbox_host() -> None:
    """The runner cannot import tracecat, so it keeps its own copy of the marker."""
    from tracecat.sandbox import executor as sandbox_executor

    assert minimal_runner._WORKLOAD_DONE_MARKER == (
        sandbox_executor._WORKLOAD_DONE_MARKER
    )
