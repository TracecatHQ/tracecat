from __future__ import annotations

import io
import sys
import types
from typing import Any

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
