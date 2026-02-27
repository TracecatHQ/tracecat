from __future__ import annotations

import pytest

from tracecat.config import bound_env


def test_bound_env_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_BOUND_ENV", raising=False)

    result = bound_env("TEST_BOUND_ENV", 16, lower=8)

    assert result == 16


def test_bound_env_clamps_below_lower(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOUND_ENV", "4")

    result = bound_env("TEST_BOUND_ENV", 16, lower=8)

    assert result == 8


def test_bound_env_clamps_above_upper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOUND_ENV", "99")

    result = bound_env("TEST_BOUND_ENV", 16, upper=32)

    assert result == 32


def test_bound_env_parses_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOUND_ENV", "1.5")

    result = bound_env("TEST_BOUND_ENV", 0.5, lower=0.0, upper=2.0)

    assert result == 1.5


def test_bound_env_uses_default_for_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_BOUND_ENV", "")

    result = bound_env("TEST_BOUND_ENV", 10, lower=8)

    assert result == 10


def test_bound_env_rejects_invalid_numeric_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_BOUND_ENV", "not-a-number")

    with pytest.raises(ValueError, match="TEST_BOUND_ENV must be an integer"):
        bound_env("TEST_BOUND_ENV", 16, lower=8)


def test_bound_env_rejects_invalid_bounds() -> None:
    with pytest.raises(
        ValueError, match="lower \\(10\\) cannot be greater than upper \\(8\\)"
    ):
        bound_env("TEST_BOUND_ENV", 16, lower=10, upper=8)
