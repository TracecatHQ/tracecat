from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

import tracecat.config as tracecat_config
from tracecat.config import bound_env, env_bool, env_networks, env_ports

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "tracecat" / "config.py"
FARGATE_ECS_LOCALS_PATH = (
    REPO_ROOT / "deployments" / "fargate" / "modules" / "ecs" / "locals.tf"
)
FARGATE_AGENT_WORKER_PATH = (
    REPO_ROOT / "deployments" / "fargate" / "modules" / "ecs" / "ecs-agent-worker.tf"
)
FARGATE_ECS_IAM_PATH = (
    REPO_ROOT / "deployments" / "fargate" / "modules" / "ecs" / "iam.tf"
)
SANDBOX_POLICY_COMPOSE_ENV_FILES = (
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.dev.yml",
    REPO_ROOT / "docker-compose.local.yml",
)
# The sandbox Compose file is an override and inherits policy variables from a base.
COMPOSE_ENV_FILES = (
    *SANDBOX_POLICY_COMPOSE_ENV_FILES,
    REPO_ROOT / "docker-compose.sandbox.yml",
)
ENV_EXAMPLE_FILES = (REPO_ROOT / ".env.example",)
DEPLOYMENT_ENV_FILES = (*COMPOSE_ENV_FILES, *ENV_EXAMPLE_FILES)
TRACED_COMPOSE_ENV_FILES = SANDBOX_POLICY_COMPOSE_ENV_FILES
TRACED_COMPOSE_SERVICES = (
    "api",
    "worker",
    "executor",
    "agent-worker",
    "agent-executor",
)
PLATFORM_OTEL_COMPOSE_ENV = (
    "TRACECAT__PLATFORM_OTEL_ENABLED: ${TRACECAT__PLATFORM_OTEL_ENABLED:-false}",
    "OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}",
    "OTEL_TRACES_SAMPLER: ${OTEL_TRACES_SAMPLER:-parentbased_traceidratio}",
    "OTEL_TRACES_SAMPLER_ARG: ${OTEL_TRACES_SAMPLER_ARG:-1.0}",
)
PLATFORM_OTEL_HEADERS_COMPOSE_ENV = (
    "OTEL_EXPORTER_OTLP_HEADERS: ${OTEL_EXPORTER_OTLP_HEADERS:-}"
)
SANDBOX_POLICY_ENV_VARS = {
    "TRACECAT__SANDBOX_INSTALL_ALLOWED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_INSTALL_ALLOWED_EGRESS_TCP_PORTS",
    "TRACECAT__SANDBOX_REGISTRY_ALLOWED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_REGISTRY_ALLOWED_EGRESS_TCP_PORTS",
    "TRACECAT__SANDBOX_SCRIPT_ALLOWED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_SCRIPT_ALLOWED_EGRESS_TCP_PORTS",
    "TRACECAT__SANDBOX_ACTION_ALLOWED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_ACTION_ALLOWED_EGRESS_TCP_PORTS",
    "TRACECAT__SANDBOX_AGENT_ALLOWED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_AGENT_ALLOWED_EGRESS_TCP_PORTS",
    "TRACECAT__SANDBOX_BLOCKED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_ALLOW_PUBLIC_IPV6_EGRESS",
}
REGISTRY_POLICY_ENV_VARS = {
    "TRACECAT__SANDBOX_REGISTRY_ALLOWED_EGRESS_CIDRS",
    "TRACECAT__SANDBOX_REGISTRY_ALLOWED_EGRESS_TCP_PORTS",
}
SENTRY_WORKFLOW_COMPOSE_SERVICES = ("worker", "agent-worker", "executor")


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


def test_env_networks_parses_ipv4_and_ipv6_cidrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TEST_NETWORKS_ENV",
        "10.42.0.0/16, 203.0.113.10, 2001:db8::/48",
    )

    networks = env_networks("TEST_NETWORKS_ENV")

    assert tuple(str(network) for network in networks) == (
        "10.42.0.0/16",
        "203.0.113.10/32",
        "2001:db8::/48",
    )


def test_env_networks_rejects_invalid_cidr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_NETWORKS_ENV", "10.42.0.0/16,not-a-cidr")

    with pytest.raises(ValueError, match="TEST_NETWORKS_ENV contains an invalid CIDR"):
        env_networks("TEST_NETWORKS_ENV")


def test_env_ports_parses_and_deduplicates_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_PORTS_ENV", "443, 8443,443")

    assert env_ports("TEST_PORTS_ENV", default=(80,)) == (443, 8443)


@pytest.mark.parametrize("raw_value", ["not-a-port", "0", "65536"])
def test_env_ports_rejects_invalid_ports(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("TEST_PORTS_ENV", raw_value)

    with pytest.raises(ValueError, match="TEST_PORTS_ENV contains an invalid port"):
        env_ports("TEST_PORTS_ENV", default=(443,))


@pytest.mark.parametrize("raw_value", [None, "", "   "])
def test_env_ports_uses_default_when_unset_or_blank(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str | None,
) -> None:
    if raw_value is None:
        monkeypatch.delenv("TEST_PORTS_ENV", raising=False)
    else:
        monkeypatch.setenv("TEST_PORTS_ENV", raw_value)

    assert env_ports("TEST_PORTS_ENV", default=(80, 443)) == (80, 443)


@pytest.mark.parametrize("raw_value", [None, "", "  "])
def test_env_networks_uses_default_when_unset_or_blank(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str | None,
) -> None:
    if raw_value is None:
        monkeypatch.delenv("TEST_NETWORKS_ENV", raising=False)
    else:
        monkeypatch.setenv("TEST_NETWORKS_ENV", raw_value)
    default = env_networks(
        "TEST_NETWORKS_ENV", default=tracecat_config.TRACECAT__AUDIT_TRUSTED_PROXY_CIDRS
    )

    assert default == tracecat_config.TRACECAT__AUDIT_TRUSTED_PROXY_CIDRS


def test_audit_trusted_proxy_env_is_wired_to_deployments() -> None:
    """Both audit consumers (api, mcp) must receive the override in every target."""
    name = "TRACECAT__AUDIT_TRUSTED_PROXY_CIDRS"
    for path in SANDBOX_POLICY_COMPOSE_ENV_FILES:
        source = path.read_text()
        for service in ("api", "mcp"):
            match = re.search(
                rf"(?ms)^  {service}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
                source,
            )
            assert match is not None, f"{path.name}: no {service} service block"
            assert name in match.group("body"), f"{path.name}: {service}"
    fargate = REPO_ROOT / "deployments/fargate"
    assert name in (fargate / "modules/ecs/locals.tf").read_text()
    for tf in ("variables.tf", "main.tf", "modules/ecs/variables.tf"):
        assert "audit_trusted_proxy_cidrs" in (fargate / tf).read_text(), tf


def test_sandbox_policy_env_vars_are_wired_to_compose_files() -> None:
    missing_by_file = {
        str(path.relative_to(REPO_ROOT)): sorted(
            name for name in SANDBOX_POLICY_ENV_VARS if name not in path.read_text()
        )
        for path in SANDBOX_POLICY_COMPOSE_ENV_FILES
    }
    missing_by_file = {
        path: missing for path, missing in missing_by_file.items() if missing
    }

    assert not missing_by_file


def test_registry_policy_env_vars_are_regular_executor_only() -> None:
    for path in SANDBOX_POLICY_COMPOSE_ENV_FILES:
        source = path.read_text()
        executor_match = re.search(
            r"(?ms)^  executor:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
            source,
        )
        agent_executor_match = re.search(
            r"(?ms)^  agent-executor:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)",
            source,
        )
        assert executor_match is not None
        assert agent_executor_match is not None

        executor_source = executor_match.group("body")
        agent_executor_source = agent_executor_match.group("body")
        for name in REGISTRY_POLICY_ENV_VARS:
            assert name in executor_source
            assert name not in agent_executor_source


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


@pytest.mark.parametrize("path", TRACED_COMPOSE_ENV_FILES, ids=lambda path: path.name)
@pytest.mark.parametrize("service", TRACED_COMPOSE_SERVICES)
def test_platform_otel_env_is_forwarded_to_traced_compose_services(
    path: Path, service: str
) -> None:
    source = path.read_text()
    service_match = re.search(
        rf"^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert service_match is not None

    service_body = service_match.group("body")
    for env_line in PLATFORM_OTEL_COMPOSE_ENV:
        assert env_line in service_body
    if service in {"executor", "agent-executor"}:
        assert PLATFORM_OTEL_HEADERS_COMPOSE_ENV not in service_body
    else:
        assert PLATFORM_OTEL_HEADERS_COMPOSE_ENV in service_body


@pytest.mark.parametrize("path", TRACED_COMPOSE_ENV_FILES, ids=lambda path: path.name)
@pytest.mark.parametrize("service", SENTRY_WORKFLOW_COMPOSE_SERVICES)
def test_sentry_dsn_is_forwarded_to_workflow_compose_services(
    path: Path, service: str
) -> None:
    source = path.read_text()
    service_match = re.search(
        rf"^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert service_match is not None

    assert "SENTRY_DSN: ${SENTRY_DSN:-}" in service_match.group("body")


@pytest.mark.parametrize("local_name", ["agent_worker_env", "agent_executor_env"])
def test_platform_otel_env_is_forwarded_to_fargate_agent_services(
    local_name: str,
) -> None:
    source = FARGATE_ECS_LOCALS_PATH.read_text()
    local_match = re.search(
        rf"(?ms)^  {re.escape(local_name)} = \[\n(?P<body>.*?)(?=^  [a-z][a-z0-9_]+ = |^\}})",
        source,
    )
    assert local_match is not None
    expected_env = (
        "local.tracecat_executor_platform_otel_env,"
        if local_name == "agent_executor_env"
        else "local.tracecat_platform_otel_env,"
    )
    assert expected_env in local_match.group("body")


def test_fargate_agent_worker_receives_platform_otel_header_secret() -> None:
    source = FARGATE_AGENT_WORKER_PATH.read_text()
    assert "secrets     = local.agent_worker_secrets" in source


def test_fargate_executors_use_credential_free_platform_gateway() -> None:
    source = FARGATE_ECS_LOCALS_PATH.read_text()
    for local_name in ("executor_env", "agent_executor_env"):
        local_match = re.search(
            rf"(?ms)^  {local_name} = \[\n(?P<body>.*?)(?=^  [a-z][a-z0-9_]+ = |^\}})",
            source,
        )
        assert local_match is not None
        assert "local.tracecat_executor_platform_otel_env" in local_match.group("body")
        assert "TRACECAT__PLATFORM_OTEL_HEADERS_SECRET_ARN" not in local_match.group(
            "body"
        )

    iam_source = FARGATE_ECS_IAM_PATH.read_text()
    assert (
        'resource "aws_iam_role_policy_attachment" "executor_task_platform_otel_headers"'
        not in iam_source
    )


def test_platform_otel_operator_settings_are_not_advertised_in_env_example() -> None:
    source = (REPO_ROOT / ".env.example").read_text()
    assert "TRACECAT__PLATFORM_OTEL_ENABLED" not in source
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in source
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in source


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


@pytest.mark.parametrize(
    ("sandbox_timeout", "expected_drain_timeout"),
    [(None, 3660), (900, 960), (7200, 7260)],
)
def test_agent_executor_drain_default_covers_all_supported_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_timeout: int | None,
    expected_drain_timeout: int,
) -> None:
    try:
        with monkeypatch.context() as env:
            if sandbox_timeout is None:
                env.delenv("TRACECAT__AGENT_SANDBOX_TIMEOUT", raising=False)
            else:
                env.setenv("TRACECAT__AGENT_SANDBOX_TIMEOUT", str(sandbox_timeout))
            env.delenv(
                "TRACECAT__AGENT_EXECUTOR_GRACEFUL_SHUTDOWN_TIMEOUT",
                raising=False,
            )

            reloaded_config = importlib.reload(tracecat_config)

            assert reloaded_config.TRACECAT__AGENT_SANDBOX_TIMEOUT == (
                expected_drain_timeout - 60
            )
            assert (
                reloaded_config.TRACECAT__AGENT_EXECUTOR_GRACEFUL_SHUTDOWN_TIMEOUT
                == expected_drain_timeout
            )
    finally:
        importlib.reload(tracecat_config)


def test_executor_concurrency_uses_bounded_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        with monkeypatch.context() as env:
            env.delenv("TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES", raising=False)
            env.delenv("TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS", raising=False)

            reloaded_config = importlib.reload(tracecat_config)

            assert reloaded_config.TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES == 16
            assert reloaded_config.TRACECAT__EXECUTOR_THREADPOOL_MAX_WORKERS == 16
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
