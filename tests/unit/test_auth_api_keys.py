from __future__ import annotations

import base64

import pytest

from tracecat.auth.api_keys import generate_api_key, verify_api_key


def test_generate_api_key_shapes() -> None:
    generated = generate_api_key()

    assert generated.raw.startswith("sk_")
    assert generated.raw.endswith(generated.suffix)
    assert generated.hashed
    assert generated.salt_b64
    # Salt must be valid base64
    base64.b64decode(generated.salt_b64.encode("ascii"), validate=True)


def test_verify_api_key_round_trip() -> None:
    generated = generate_api_key()
    assert verify_api_key(generated.raw, generated.salt_b64, generated.hashed)


@pytest.mark.parametrize("candidate", ["sk_wrong", "", None])
def test_verify_api_key_rejects_invalid(candidate: str | None) -> None:
    generated = generate_api_key()
    assert (
        verify_api_key(candidate or "", generated.salt_b64, generated.hashed) is False
    )
