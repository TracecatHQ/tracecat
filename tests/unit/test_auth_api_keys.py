from __future__ import annotations

import base64

import pytest

from tracecat.auth.api_keys import (
    DEFAULT_API_KEY_PREFIX,
    ORG_API_KEY_PREFIX,
    WORKSPACE_API_KEY_PREFIX,
    generate_api_key,
    generate_managed_api_key,
    parse_managed_api_key,
    verify_api_key,
)


def test_generate_api_key_shapes() -> None:
    generated = generate_api_key()

    assert generated.raw.startswith(DEFAULT_API_KEY_PREFIX)
    assert generated.hashed
    assert generated.salt_b64
    assert generated.prefix == DEFAULT_API_KEY_PREFIX
    # Salt must be valid base64
    base64.b64decode(generated.salt_b64.encode("ascii"), validate=True)


@pytest.mark.parametrize("length", [0, -1])
def test_generate_api_key_rejects_non_positive_length(length: int) -> None:
    with pytest.raises(ValueError, match="length must be positive"):
        generate_api_key(length=length)


def test_verify_api_key_round_trip() -> None:
    generated = generate_api_key()
    assert verify_api_key(generated.raw, generated.salt_b64, generated.hashed)


@pytest.mark.parametrize("prefix", [ORG_API_KEY_PREFIX, WORKSPACE_API_KEY_PREFIX])
def test_generate_managed_api_key_round_trip(prefix: str) -> None:
    generated = generate_managed_api_key(prefix=prefix)

    assert generated.raw.startswith(prefix)
    assert generated.key_id
    assert verify_api_key(generated.raw, generated.salt_b64, generated.hashed)

    parsed = parse_managed_api_key(generated.raw)
    assert parsed is not None
    assert parsed.prefix == prefix
    assert parsed.key_id == generated.key_id
    assert parsed.secret


def test_parse_managed_api_key_rejects_invalid_shape() -> None:
    assert parse_managed_api_key("tc_org_sk_missing_") is None


def test_make_api_key_preview_uses_prefix_and_tail() -> None:
    generated = generate_api_key()
    preview = generated.preview()
    assert preview.startswith(DEFAULT_API_KEY_PREFIX)
    expected_tail = generated.raw[-4:]
    assert preview == f"{DEFAULT_API_KEY_PREFIX}...{expected_tail}"


def test_generated_api_key_preview_with_custom_prefix() -> None:
    """Test that GeneratedApiKey.preview() works with custom prefixes."""
    custom_prefix = "custom_"
    generated = generate_api_key(prefix=custom_prefix)
    preview = generated.preview()
    assert preview.startswith(custom_prefix)
    expected_tail = generated.raw[-4:]
    assert preview == f"{custom_prefix}...{expected_tail}"


@pytest.mark.parametrize("candidate", ["sk_wrong", "", None])
def test_verify_api_key_rejects_invalid(candidate: str | None) -> None:
    generated = generate_api_key()
    assert (
        verify_api_key(candidate or "", generated.salt_b64, generated.hashed) is False
    )
