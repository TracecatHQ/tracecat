"""Unit tests for base62 encoding/decoding functionality."""

import pytest

from tracecat.base62 import b62decode, b62encode


def test_b62encode_zero() -> None:
    """Test encoding zero returns '0'."""
    assert b62encode(0) == "0"


def test_b62encode_single_digit() -> None:
    """Test encoding single-digit numbers."""
    assert b62encode(5) == "5"
    assert b62encode(9) == "9"


def test_b62encode_double_digit() -> None:
    """Test encoding double-digit numbers."""
    assert b62encode(10) == "a"
    assert b62encode(35) == "z"
    assert b62encode(36) == "A"
    assert b62encode(61) == "Z"


def test_b62encode_large_numbers() -> None:
    """Test encoding larger numbers."""
    assert b62encode(100) == "1C"
    assert b62encode(1000) == "g8"
    assert b62encode(999999) == "4c91"


def test_b62encode_negative() -> None:
    """Test encoding negative numbers raises ValueError."""
    with pytest.raises(ValueError, match="Number must be non-negative"):
        b62encode(-1)


def test_b62decode_zero() -> None:
    """Test decoding '0' returns 0."""
    assert b62decode("0") == 0


def test_b62decode_single_char() -> None:
    """Test decoding single characters."""
    assert b62decode("5") == 5
    assert b62decode("a") == 10
    assert b62decode("z") == 35
    assert b62decode("A") == 36
    assert b62decode("Z") == 61


def test_b62decode_multiple_chars() -> None:
    """Test decoding multiple characters."""
    assert b62decode("1C") == 100
    assert b62decode("g8") == 1000
    assert b62decode("4c91") == 999999


def test_b62decode_invalid_chars() -> None:
    """Test decoding invalid characters raises ValueError."""
    invalid_inputs = ["!", "@", "#", "$", "hello!"]

    for invalid_input in invalid_inputs:
        with pytest.raises(ValueError) as e:
            b62decode(invalid_input)
            assert f"Invalid base62 character: {invalid_input}" in str(e.value)


def test_encode_decode_roundtrip() -> None:
    """Test encoding followed by decoding returns original number."""
    test_numbers = [0, 1, 10, 62, 100, 1000, 999999]
    for num in test_numbers:
        assert b62decode(b62encode(num)) == num


def test_decode_encode_roundtrip() -> None:
    """Test decoding followed by encoding returns original string."""
    test_strings = ["0", "9", "a", "Z", "1C", "g8", "4c91"]
    for string in test_strings:
        assert b62encode(b62decode(string)) == string
