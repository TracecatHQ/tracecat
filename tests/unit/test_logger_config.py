import json
import os
import subprocess
import sys

import pytest

from tracecat.logger.config import LogFormat, log_format_from_env


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, LogFormat.CONSOLE),
        ("", LogFormat.CONSOLE),
        ("  ", LogFormat.CONSOLE),
        ("console", LogFormat.CONSOLE),
        (" JSON ", LogFormat.JSON),
    ],
)
def test_log_format_from_env(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str | None,
    expected: LogFormat,
) -> None:
    if raw_value is None:
        monkeypatch.delenv("TRACECAT__LOG_FORMAT", raising=False)
    else:
        monkeypatch.setenv("TRACECAT__LOG_FORMAT", raw_value)

    assert log_format_from_env() is expected


def test_log_format_from_env_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACECAT__LOG_FORMAT", "xml")

    with pytest.raises(ValueError, match="console, json"):
        log_format_from_env()


def _emit_log(log_format: LogFormat, *, spoof_process_context: bool = False) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "LOG_LEVEL": "INFO",
            "TRACECAT__APP_ENV": "staging",
            "TRACECAT__LOG_FORMAT": log_format.value,
            "TRACECAT__SERVICE_NAME": "worker",
        }
    )
    bindings = "event='workflow_terminal_failure', owner='platform'"
    if spoof_process_context:
        bindings += (
            ", process_service='spoofed-service', environment='spoofed-environment'"
        )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tracecat.logger import logger; "
                f"logger.bind({bindings}).info('Workflow failed')"
            ),
        ],
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    return result.stderr


def test_json_log_format_preserves_structured_fields() -> None:
    payload = json.loads(_emit_log(LogFormat.JSON))

    assert payload["record"]["level"]["name"] == "INFO"
    assert payload["record"]["message"] == "Workflow failed"
    assert payload["record"]["extra"] == {
        "environment": "staging",
        "event": "workflow_terminal_failure",
        "owner": "platform",
        "process_service": "worker",
    }


def test_process_context_cannot_be_overridden_by_log_bindings() -> None:
    payload = json.loads(_emit_log(LogFormat.JSON, spoof_process_context=True))

    assert payload["record"]["extra"]["environment"] == "staging"
    assert payload["record"]["extra"]["process_service"] == "worker"


def test_console_log_format_remains_human_readable() -> None:
    output = _emit_log(LogFormat.CONSOLE)

    with pytest.raises(json.JSONDecodeError):
        json.loads(output)
    assert "INFO" in output
    assert "Workflow failed" in output
    assert "'event': 'workflow_terminal_failure'" in output
    assert "'process_service': 'worker'" in output
