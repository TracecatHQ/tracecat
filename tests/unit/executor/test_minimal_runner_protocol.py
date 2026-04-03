from __future__ import annotations

import io
import sys
import types
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import orjson
import pytest
from pydantic import BaseModel

from tracecat.executor import minimal_runner


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
                "secrets": {},
                "evaluated_args": {},
            }
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
                "secrets": {},
                "evaluated_args": {},
            }
        }
    )

    assert result["success"] is False
    assert result["error"]["type"] == "ValueError"
    assert result["error"]["message"] == "boom"


def test_main_minimal_preserves_proxy_metadata_in_action_kwargs(monkeypatch) -> None:
    test_module: Any = types.ModuleType("test_module")

    def create_case(summary: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "summary": summary,
            "metadata": kwargs["__tracecat"],
        }

    test_module.create_case = create_case

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
                    "name": "create_case",
                },
                "secrets": {},
                "evaluated_args": {
                    "summary": "hello",
                    "__tracecat": {"tool_call_id": "toolu_123"},
                },
            }
        }
    )

    assert result == {
        "success": True,
        "result": {
            "summary": "hello",
            "metadata": {"tool_call_id": "toolu_123"},
        },
    }


def test_capped_text_buffer_limits_memory_growth() -> None:
    buf = minimal_runner._CappedTextBuffer(limit=5)

    written = buf.write("abcdefgh")

    assert written == 8
    assert buf.getvalue() == "abcde"
    assert buf.truncated is True


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
                "secrets": {},
                "evaluated_args": {},
            }
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
