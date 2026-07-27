from __future__ import annotations

import orjson
import pytest

from tracecat.sanitization import redact_sensitive_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"Authorization": "Basic synthetic-credential"}',
            '{"Authorization": "[redacted]"}',
        ),
        (
            "{'Cookie': 'session=synthetic-cookie'}",
            "{'Cookie': '[redacted]'}",
        ),
        (
            '{"Set-Cookie": "session=synthetic-cookie; HttpOnly"}',
            '{"Set-Cookie": "[redacted]"}',
        ),
    ],
)
def test_redact_sensitive_text_redacts_serialized_headers(
    text: str,
    expected: str,
) -> None:
    assert redact_sensitive_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        ("-----BEGIN PRIVATE KEY-----\nSYNTHETICBODY\n-----END PRIVATE KEY-----"),
        (
            "private_key=-----BEGIN PRIVATE KEY-----\n"
            "SYNTHETICBODY\n"
            "-----END PRIVATE KEY-----"
        ),
    ],
)
def test_redact_sensitive_text_redacts_complete_pem_blocks(text: str) -> None:
    sanitized = redact_sensitive_text(text)

    assert "SYNTHETICBODY" not in sanitized
    assert "BEGIN PRIVATE KEY" not in sanitized
    assert "END PRIVATE KEY" not in sanitized


def test_redact_sensitive_text_preserves_serialized_pem_shape() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nSYNTHETICBODY\n-----END PRIVATE KEY-----"
    serialized = orjson.dumps({"pem": pem}).decode()

    sanitized = redact_sensitive_text(serialized)

    assert orjson.loads(sanitized) == {"pem": "[redacted PEM block]"}
