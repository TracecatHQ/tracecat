from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

import tracecat.config as tracecat_config
from tracecat.config import bound_env, env_bool

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "tracecat" / "config.py"
COMPOSE_ENV_FILES = (
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.dev.yml",
    REPO_ROOT / "docker-compose.local.yml",
)
ENV_EXAMPLE_FILES = (REPO_ROOT / ".env.example",)
DEPLOYMENT_ENV_FILES = (*COMPOSE_ENV_FILES, *ENV_EXAMPLE_FILES)


def _config_bool_env_vars() -> set[str]:
    tree = ast.parse(CONFIG_PATH.read_text())
    env_vars: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "env_bool"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            env_vars.add(node.args[0].value)
    return env_vars


def test_bound_env_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_BOUND_ENV", raising=False)

    result = bound_env("TEST_BOUND_ENV", 16, lower=8)

    assert result == 16


@pytest.mark.parametrize("default", [False, True])
def test_env_bool_returns_default_when_unset(
    monkeypatch: pytest.MonkeyPatch, default: bool
) -> None:
    monkeypatch.delenv("TEST_BOOL_ENV", raising=False)

    result = env_bool("TEST_BOOL_ENV", default=default)

    assert result is default


@pytest.mark.parametrize("raw_value", ["", "   "])
@pytest.mark.parametrize("default", [False, True])
def test_env_bool_returns_default_for_blank_values(
    monkeypatch: pytest.MonkeyPatch, raw_value: str, default: bool
) -> None:
    monkeypatch.setenv("TEST_BOOL_ENV", raw_value)

    result = env_bool("TEST_BOOL_ENV", default=default)

    assert result is default


@pytest.mark.parametrize("raw_value", ["1", "true", "TRUE", "yes", "on"])
def test_env_bool_parses_true_tokens(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    monkeypatch.setenv("TEST_BOOL_ENV", raw_value)

    result = env_bool("TEST_BOOL_ENV", default=False)

    assert result is True


@pytest.mark.parametrize("raw_value", ["0", "false", "FALSE", "no", "off"])
def test_env_bool_parses_false_tokens(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
) -> None:
    monkeypatch.setenv("TEST_BOOL_ENV", raw_value)

    result = env_bool("TEST_BOOL_ENV", default=True)

    assert result is False


def test_env_bool_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_BOOL_ENV", "not-a-bool")

    with pytest.raises(ValueError, match="TEST_BOOL_ENV must be a boolean"):
        env_bool("TEST_BOOL_ENV", default=True)


def test_config_boolean_env_values_use_env_bool() -> None:
    source = CONFIG_PATH.read_text()
    forbidden_patterns = {
        r"\.lower\(\)\s*==\s*['\"]true['\"]": "inline true comparison",
        r"\.lower\(\)\s+in\s+\(": "inline truthy token tuple",
        r"bool\(\s*(?:os\.environ\.get|os\.getenv)\(": "bool(os.environ.get(...))",
    }

    violations = [
        f"{description}: {pattern}"
        for pattern, description in forbidden_patterns.items()
        if re.search(pattern, source)
    ]

    assert not violations, (
        "Boolean environment variables in tracecat/config.py must use env_bool(): "
        + ", ".join(violations)
    )


def test_boolean_env_values_preserve_defaults_and_compose_overrides() -> None:
    bool_env_vars = _config_bool_env_vars()
    assert bool_env_vars

    violations: list[str] = []
    for path in DEPLOYMENT_ENV_FILES:
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for env_var in bool_env_vars:
                if re.fullmatch(rf"{re.escape(env_var)}=", stripped):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {stripped}"
                    )
                if re.fullmatch(
                    rf"{re.escape(env_var)}:\s*\$\{{{re.escape(env_var)}\}}",
                    stripped,
                ):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {stripped}"
                    )
                if re.fullmatch(
                    rf"-\s*{re.escape(env_var)}=\$\{{{re.escape(env_var)}\}}",
                    stripped,
                ):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {stripped}"
                    )
                if path in COMPOSE_ENV_FILES and (
                    re.fullmatch(
                        rf"{re.escape(env_var)}:\s*['\"]?(?:true|false)['\"]?",
                        stripped,
                        flags=re.IGNORECASE,
                    )
                    or re.fullmatch(
                        rf"-\s*{re.escape(env_var)}=['\"]?(?:true|false)['\"]?",
                        stripped,
                        flags=re.IGNORECASE,
                    )
                ):
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: {stripped}"
                    )

    assert not violations, (
        "Boolean env vars must not be blank/defaultless. Compose files must use "
        "`${VAR:-default}` instead of hardcoded literals so .env overrides still "
        "work: " + ", ".join(violations)
    )


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


def test_action_gateway_socket_uses_default_for_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        with monkeypatch.context() as env:
            env.setenv("TRACECAT__ACTION_GATEWAY_SOCKET", "")

            reloaded_config = importlib.reload(tracecat_config)

            assert (
                reloaded_config.TRACECAT__ACTION_GATEWAY_SOCKET
                == "/var/run/tracecat/action-gateway.sock"
            )
    finally:
        importlib.reload(tracecat_config)


def test_action_gateway_enabled_defaults_true_and_allows_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        with monkeypatch.context() as env:
            env.delenv("TRACECAT__ACTION_GATEWAY_ENABLED", raising=False)
            reloaded_config = importlib.reload(tracecat_config)
            assert reloaded_config.TRACECAT__ACTION_GATEWAY_ENABLED is True

            env.setenv("TRACECAT__ACTION_GATEWAY_ENABLED", "")
            reloaded_config = importlib.reload(tracecat_config)
            assert reloaded_config.TRACECAT__ACTION_GATEWAY_ENABLED is True

            env.setenv("TRACECAT__ACTION_GATEWAY_ENABLED", "false")
            reloaded_config = importlib.reload(tracecat_config)
            assert reloaded_config.TRACECAT__ACTION_GATEWAY_ENABLED is False
    finally:
        importlib.reload(tracecat_config)


def test_audit_delivery_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    env_vars = [
        "TRACECAT__AUDIT_DELIVERY_ENABLED",
        "TRACECAT__AUDIT_DELIVERY_MAXLEN",
        "TRACECAT__AUDIT_DELIVERY_TTL_SECONDS",
        "TRACECAT__AUDIT_DELIVERY_BLOCK_MS",
        "TRACECAT__AUDIT_DELIVERY_BATCH",
        "TRACECAT__AUDIT_DELIVERY_MAX_ATTEMPTS",
        "TRACECAT__AUDIT_DELIVERY_CIRCUIT_THRESHOLD",
        "TRACECAT__AUDIT_DELIVERY_CIRCUIT_TTL_SECONDS",
        "TRACECAT__AUDIT_DELIVERY_TIMEOUT_SECONDS",
    ]
    try:
        with monkeypatch.context() as env:
            for env_var in env_vars:
                env.delenv(env_var, raising=False)
            reloaded_config = importlib.reload(tracecat_config)

            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_ENABLED is True
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_MAXLEN == 30000
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_TTL_SECONDS == 259200
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_BLOCK_MS == 2000
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_BATCH == 100
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_MAX_ATTEMPTS == 10
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_CIRCUIT_THRESHOLD == 5
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_CIRCUIT_TTL_SECONDS == 60
            assert reloaded_config.TRACECAT__AUDIT_DELIVERY_TIMEOUT_SECONDS == 10
    finally:
        importlib.reload(tracecat_config)


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
