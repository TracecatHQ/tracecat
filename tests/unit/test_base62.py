"""Unit tests for base62 encoding/decoding functionality."""

import pytest

from tracecat.base62 import b62decode, b62encode


@pytest.mark.parametrize(
    ("number", "encoded"),
    [
        (0, "0"),
        (5, "5"),
        (9, "9"),
        (10, "a"),
        (35, "z"),
        (36, "A"),
        (61, "Z"),
        (62, "10"),
        (100, "1C"),
        (1000, "g8"),
        (999999, "4c91"),
    ],
)
def test_b62_roundtrip(number: int, encoded: str) -> None:
    """Encoding pins the 0-9a-zA-Z alphabet and decoding inverts it."""
    assert b62encode(number) == encoded
    assert b62decode(encoded) == number


def test_b62encode_negative() -> None:
    with pytest.raises(ValueError, match="Number must be non-negative"):
        b62encode(-1)


def test_b62decode_invalid_chars() -> None:
    for invalid_input in ["!", "@", "#", "$", "hello!"]:
        with pytest.raises(ValueError):
            b62decode(invalid_input)
