from __future__ import annotations

from collections.abc import Mapping

import orjson
import pytest

from tracecat.agent.session.history import (
    decode_raw_session_line,
    prepare_session_history,
)


def _contains_nul(value: object) -> bool:
    match value:
        case str() as text:
            return "\x00" in text
        case list() as items:
            return any(_contains_nul(item) for item in items)
        case Mapping() as mapping:
            return any(
                _contains_nul(key) or _contains_nul(item)
                for key, item in mapping.items()
            )
        case _:
            return False


def test_prepare_session_history_preserves_exact_raw_nul_content() -> None:
    raw_line = (
        r'{"actual\u0000key":"left\u0000right",'
        r'"actual\\u0000key":"literal\\u0000text"}'
    )
    content = orjson.loads(raw_line)

    payload = prepare_session_history(content, raw_session_line=raw_line)

    assert payload.raw_session_line is not None
    assert payload.raw_session_line == raw_line.encode()
    assert _contains_nul(payload.content) is False
    assert len(payload.content) == 2

    decoded, decoded_line = decode_raw_session_line(payload.raw_session_line)
    assert decoded_line == raw_line
    assert decoded == content
    assert decoded["actual\x00key"] == "left\x00right"
    assert decoded[r"actual\u0000key"] == r"literal\u0000text"


def test_prepare_session_history_builds_raw_line_for_synthetic_content() -> None:
    content = {
        "type": "user",
        "message": {"content": "left\x00right"},
    }

    payload = prepare_session_history(content)

    assert payload.content["message"]["content"] == r"left\u0000right"
    assert payload.raw_session_line is not None
    assert orjson.loads(payload.raw_session_line) == content


def test_prepare_session_history_keeps_safe_rows_jsonb_only() -> None:
    content = {"type": "user", "message": {"content": "plain text"}}

    payload = prepare_session_history(content)

    assert payload.content == content
    assert payload.raw_session_line is None


def test_decode_raw_session_line_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        decode_raw_session_line(memoryview(b"[]"))
