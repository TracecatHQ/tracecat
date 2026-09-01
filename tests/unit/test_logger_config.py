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


def _emit_log(
    log_format: LogFormat,
    *,
    bindings: str = "event='workflow_terminal_failure', owner='platform'",
    statement: str = "logger.info('Workflow failed')",
    spoof_process_context: bool = False,
) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "LOG_LEVEL": "INFO",
            "TRACECAT__APP_ENV": "staging",
            "TRACECAT__LOG_FORMAT": log_format.value,
            "TRACECAT__SERVICE_NAME": "worker",
        }
    )
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
                f"logger = logger.bind({bindings}); {statement}"
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

    assert payload == {
        "schema": "tracecat.log.v1",
        "timestamp": payload["timestamp"],
        "level": "INFO",
        "message": "Workflow failed",
        "service": "worker",
        "environment": "staging",
        "module": "__main__",
        "function": "<module>",
        "line": 1,
        "attributes": {
            "event": "workflow_terminal_failure",
            "owner": "platform",
        },
    }
    assert payload["timestamp"].endswith("Z")
    assert "text" not in payload
    assert "record" not in payload


def test_process_context_cannot_be_overridden_by_log_bindings() -> None:
    payload = json.loads(_emit_log(LogFormat.JSON, spoof_process_context=True))

    assert payload["environment"] == "staging"
    assert payload["service"] == "worker"


def test_json_log_format_preserves_only_intentional_event_attributes() -> None:
    output = _emit_log(
        LogFormat.JSON,
        bindings=(
            "event='collection_manifest_stored', count=0, num_chunks=1, "
            "prefix='resource-path', details={'backend': 's3'}"
        ),
        statement="logger.info('Stored collection manifest')",
    )
    payload = json.loads(output)

    assert payload["attributes"] == {
        "count": 0,
        "details": {"backend": "s3"},
        "event": "collection_manifest_stored",
        "num_chunks": 1,
        "prefix": "resource-path",
    }


def test_json_log_format_preserves_rejected_reserved_attributes() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings="event={'unexpected': 'shape'}, trace_sampled='unknown'",
        )
    )

    assert "event" not in payload
    assert "trace_sampled" not in payload
    assert payload["attributes"] == {
        "event": {"unexpected": "shape"},
        "trace_sampled": "unknown",
    }


def test_json_log_format_preserves_arbitrary_size_integers() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings="large_identifier=1 << 128",
        )
    )

    assert payload["attributes"]["large_identifier"] == 1 << 128


def test_json_log_format_preserves_surrogate_escaped_strings() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings="path='bad' + chr(0xDCFF)",
            statement="logger.info('scan {}', 'bad' + chr(0xDCFF))",
        )
    )

    assert payload["message"] == "scan bad\udcff"
    assert payload["attributes"]["path"] == "bad\udcff"


def test_json_log_format_normalizes_non_finite_floats() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings="reading=float('nan'), ceiling=float('inf')",
        )
    )

    assert payload["attributes"] == {"ceiling": None, "reading": None}


def test_json_log_format_preserves_unusual_mapping_keys() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings="details={('a', 'b'): 1}",
        )
    )

    assert payload["attributes"]["details"] == {"('a', 'b')": 1}


def test_json_log_format_keeps_mapping_keys_stable_on_fallback() -> None:
    mapping_binding = "details={None: 'missing', 'None': 'literal'}"
    normal = json.loads(_emit_log(LogFormat.JSON, bindings=mapping_binding))
    fallback = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings=f"{mapping_binding}, path='bad' + chr(0xDCFF)",
        )
    )

    expected = {"None": "literal", "null": "missing"}
    assert normal["attributes"]["details"] == expected
    assert fallback["attributes"]["details"] == expected


def test_json_log_format_replaces_recursive_attributes() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            statement=(
                "items = []; items.append(items); "
                "logger.bind(items=items).info('Workflow failed')"
            ),
        )
    )

    assert payload["attributes"]["items"] == ["<recursive>"]


def test_json_log_format_keeps_datetime_shape_on_fallback() -> None:
    datetime_binding = "created_at=__import__('datetime').datetime(2024, 1, 2, 3, 4, 5)"
    normal = json.loads(_emit_log(LogFormat.JSON, bindings=datetime_binding))
    fallback = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings=f"{datetime_binding}, path='bad' + chr(0xDCFF)",
        )
    )

    assert normal["attributes"]["created_at"] == "2024-01-02 03:04:05"
    assert fallback["attributes"]["created_at"] == "2024-01-02 03:04:05"


def test_json_log_format_preserves_correlation_fields() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            bindings=(
                "wf_id='wf-123', wf_exec_id='exec-456', task_ref='parse', "
                "trace_id='0' * 32, span_id='1' * 16, trace_sampled=True"
            ),
        )
    )

    assert payload["attributes"] == {
        "wf_id": "wf-123",
        "wf_exec_id": "exec-456",
        "task_ref": "parse",
        "trace_id": "0" * 32,
        "span_id": "1" * 16,
        "trace_sampled": True,
    }


def test_json_log_format_preserves_loguru_extended_backtrace() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            statement=(
                "\ndef outer():\n    inner()\n\n"
                "def inner():\n    try:\n        raise ValueError('invalid input')\n"
                "    except ValueError:\n        logger.exception('Workflow failed')\n\n"
                "outer()"
            ),
        )
    )

    assert payload["exception"]["type"] == "ValueError"
    assert payload["exception"]["message"] == "invalid input"
    assert "ValueError: invalid input" in payload["exception"]["stack"]
    assert "in outer" in payload["exception"]["stack"]
    assert "in inner" in payload["exception"]["stack"]


def test_json_log_format_preserves_exception_for_raw_log() -> None:
    payload = json.loads(
        _emit_log(
            LogFormat.JSON,
            statement=(
                "\ntry:\n    raise ValueError('invalid input')\n"
                "except ValueError:\n"
                "    logger.opt(raw=True).exception('Workflow failed')"
            ),
        )
    )

    assert payload["exception"]["type"] == "ValueError"
    assert payload["exception"]["message"] == "invalid input"
    assert "ValueError: invalid input" in payload["exception"]["stack"]


def test_console_log_format_remains_human_readable() -> None:
    output = _emit_log(LogFormat.CONSOLE)

    with pytest.raises(json.JSONDecodeError):
        json.loads(output)
    assert "INFO" in output
    assert "Workflow failed" in output
    assert "'event': 'workflow_terminal_failure'" in output
    assert "'process_service': 'worker'" in output
