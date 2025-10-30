from __future__ import annotations

import base64

import pytest

from tracecat.auth.api_keys import (
    DEFAULT_API_KEY_PREFIX,
    generate_api_key,
    make_api_key_preview,
    verify_api_key,
)


def test_generate_api_key_shapes() -> None:
    generated = generate_api_key()

    assert generated.raw.startswith(DEFAULT_API_KEY_PREFIX)
    assert generated.raw.endswith(generated.suffix)
    assert generated.hashed
    assert generated.salt_b64
    # Salt must be valid base64
    base64.b64decode(generated.salt_b64.encode("ascii"), validate=True)


@pytest.mark.parametrize("length", [0, -1])
def test_generate_api_key_rejects_non_positive_length(length: int) -> None:
    with pytest.raises(ValueError, match="length must be positive"):
        generate_api_key(length=length)


def test_verify_api_key_round_trip() -> None:
    generated = generate_api_key()
    assert verify_api_key(generated.raw, generated.salt_b64, generated.hashed)


def test_make_api_key_preview_uses_prefix_and_tail() -> None:
    generated = generate_api_key()
    preview = make_api_key_preview(generated.raw)
    assert preview.startswith(DEFAULT_API_KEY_PREFIX)
    expected_tail = generated.raw[-4:]
    assert preview == f"{DEFAULT_API_KEY_PREFIX}...{expected_tail}"


@pytest.mark.parametrize("candidate", ["sk_wrong", "", None])
def test_verify_api_key_rejects_invalid(candidate: str | None) -> None:
    generated = generate_api_key()
    assert (
        verify_api_key(candidate or "", generated.salt_b64, generated.hashed) is False
    )
