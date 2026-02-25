from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracecat.dsl import workflow_logging


def test_in_workflow_context_true_in_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow_logging.workflow.unsafe,
        "in_sandbox",
        lambda: True,
    )

    assert workflow_logging._in_workflow_context() is True


def test_in_workflow_context_true_with_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow_logging.workflow.unsafe,
        "in_sandbox",
        lambda: False,
    )
    monkeypatch.setattr(
        workflow_logging.workflow,
        "_Runtime",
        SimpleNamespace(maybe_current=lambda: object()),
    )

    assert workflow_logging._in_workflow_context() is True


def test_in_workflow_context_false_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_logging.workflow.unsafe,
        "in_sandbox",
        lambda: False,
    )
    monkeypatch.setattr(
        workflow_logging.workflow,
        "_Runtime",
        SimpleNamespace(maybe_current=lambda: None),
    )

    assert workflow_logging._in_workflow_context() is False


def test_in_workflow_context_false_on_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workflow_logging.workflow.unsafe,
        "in_sandbox",
        lambda: False,
    )

    def _raise() -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        workflow_logging.workflow,
        "_Runtime",
        SimpleNamespace(maybe_current=_raise),
    )

    assert workflow_logging._in_workflow_context() is False


def test_log_uses_process_logger_outside_workflow_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_logger = MagicMock()
    process_logger.opt.return_value = process_logger
    monkeypatch.setattr(workflow_logging, "_in_workflow_context", lambda: False)
    monkeypatch.setattr(workflow_logging, "process_logger", process_logger)

    logger = workflow_logging.get_workflow_logger(service="dsl")
    logger.info("Hello", run_id="abc")

    process_logger.opt.assert_called_once_with(lazy=True)
    process_logger.log.assert_called_once()
    [level, fmt, message_factory, suffix], _ = process_logger.log.call_args
    assert level == "INFO"
    assert fmt == "{}{}"
    assert callable(message_factory)
    assert message_factory() == "Hello"
    assert callable(suffix)
    formatted_suffix = suffix()
    assert isinstance(formatted_suffix, str)
    assert formatted_suffix.startswith(" | ")
    # Keys are sorted for deterministic output.
    assert "run_id='abc'" in formatted_suffix
    assert "service='dsl'" in formatted_suffix


def test_log_uses_temporal_logger_in_workflow_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporal_logger = MagicMock()
    temporal_logger.isEnabledFor.return_value = True
    monkeypatch.setattr(workflow_logging, "_in_workflow_context", lambda: True)
    monkeypatch.setattr(workflow_logging.workflow, "logger", temporal_logger)

    logger = workflow_logging.get_workflow_logger(workflow_id="wf-123")
    logger.warning("Failure", attempt=2)

    temporal_logger.warning.assert_called_once()
    [message], _ = temporal_logger.warning.call_args
    assert message.startswith("Failure | ")
    assert "attempt=2" in message
    assert "workflow_id='wf-123'" in message


def test_trace_maps_to_temporal_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    temporal_logger = MagicMock()
    temporal_logger.isEnabledFor.return_value = True
    monkeypatch.setattr(workflow_logging, "_in_workflow_context", lambda: True)
    monkeypatch.setattr(workflow_logging.workflow, "logger", temporal_logger)

    logger = workflow_logging.get_workflow_logger()
    logger.trace("Trace message")

    temporal_logger.debug.assert_called_once_with("Trace message")


def test_workflow_path_skips_formatting_when_level_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporal_logger = MagicMock()
    temporal_logger.isEnabledFor.return_value = False
    monkeypatch.setattr(workflow_logging, "_in_workflow_context", lambda: True)
    monkeypatch.setattr(workflow_logging.workflow, "logger", temporal_logger)

    def _raise(_fields: dict[str, object]) -> str:
        raise AssertionError(
            "fields should not be formatted when log level is disabled"
        )

    monkeypatch.setattr(workflow_logging, "_format_fields", _raise)

    logger = workflow_logging.get_workflow_logger()
    logger.debug("Suppressed message", huge_payload={"x": [1, 2, 3]})
    temporal_logger.debug.assert_not_called()


def test_safe_repr_handles_broken_repr() -> None:
    class BrokenRepr:
        def __repr__(self) -> str:
            raise ValueError("broken")

    output = workflow_logging._format_fields({"bad": BrokenRepr()})
    assert output.startswith(" | ")
    assert "bad=<unrepresentable BrokenRepr>" in output
