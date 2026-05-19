from __future__ import annotations

import asyncio
import io
import sys
import types
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import orjson
import pytest
from pydantic import BaseModel

from tracecat.executor import minimal_runner


def test_action_gateway_sdk_transport_patches_legacy_tracecat_client(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    sdk_client: Any = types.ModuleType("tracecat_registry.sdk.client")

    class LegacyTracecatClient:
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
    assert captured["timeout"] == 12.0
    assert captured["method"] == "GET"
    assert captured["url"] == "http://tracecat-action-gateway/internal/cases"
    assert captured["params"] == {"limit": 1}
    assert captured["headers"]["Authorization"] == "Bearer executor-token"


def test_action_gateway_sdk_transport_leaves_current_tracecat_client_unpatched(
    monkeypatch,
) -> None:
    sdk_client: Any = types.ModuleType("tracecat_registry.sdk.client")

    class CurrentTracecatClient:
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
