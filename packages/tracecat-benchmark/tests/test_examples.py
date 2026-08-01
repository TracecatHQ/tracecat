from __future__ import annotations

import tomllib

import yaml
from tracecat_benchmark.matrix import EXPERIMENT_ENV_PATH, load_matrix
from tracecat_benchmark.models import LoadType


def test_loadtest_override_bounds_executor_database_fanout() -> None:
    compose_path = EXPERIMENT_ENV_PATH.parents[2] / "docker-compose.loadtest.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    executor_environment = compose["services"]["executor"]["environment"]

    assert executor_environment["TRACECAT__EXECUTOR_MAX_CONCURRENT_ACTIVITIES"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_MAX_CONCURRENT_ACTIVITIES:-8}"
    )
    assert executor_environment["TRACECAT__EXECUTOR_FOR_EACH_MAX_CONCURRENCY"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_FOR_EACH_MAX_CONCURRENCY:-4}"
    )
    assert executor_environment["TRACECAT__DB_POOL_SIZE"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_DB_POOL_SIZE:-9}"
    )
    assert executor_environment["TRACECAT__DB_MAX_OVERFLOW"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_DB_MAX_OVERFLOW:-0}"
    )
    assert executor_environment["TRACECAT__DB_AUTH_POOL_SIZE"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_POOL_SIZE:-5}"
    )
    assert executor_environment["TRACECAT__DB_AUTH_MAX_OVERFLOW"] == (
        "${TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_MAX_OVERFLOW:-0}"
    )
    assert compose["services"]["temporal"]["environment"]["NUM_HISTORY_SHARDS"] == (
        "${TRACECAT__LOADTEST_TEMPORAL_NUM_HISTORY_SHARDS:-512}"
    )
    assert compose["services"]["executor"]["ports"] == [
        "127.0.0.1:${TEMPORAL_EXECUTOR_METRICS_PORT:-9465}-${TEMPORAL_EXECUTOR_METRICS_PORT_END:-9474}:9090",
        "127.0.0.1:${EXECUTOR_DB_POOL_METRICS_PORT:-9482}-${EXECUTOR_DB_POOL_METRICS_PORT_END:-9491}:9091",
    ]
    for service_name in ("api", "worker", "executor"):
        environment = compose["services"][service_name]["environment"]
        assert environment["TRACECAT_BENCHMARK_INTERNAL_DB_POOL_METRICS_PORT"] == 9091
        assert environment["PYTHONPATH"].startswith(
            "/app/packages/tracecat-benchmark/bootstrap:"
        )
    for service_name in ("executor", "agent-executor"):
        sandbox_cache = compose["services"][service_name]["volumes"][0]
        assert sandbox_cache["target"] == "/var/lib/tracecat/sandbox-cache"
        assert sandbox_cache["volume"]["nocopy"] is True


def test_pgdog_override_routes_long_lived_services_through_transaction_pool() -> None:
    package_root = EXPERIMENT_ENV_PATH.parents[2]
    compose_path = package_root / "docker-compose.pgdog.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]

    pgdog = services["pgdog"]
    assert pgdog["image"] == (
        "ghcr.io/pgdogdev/pgdog:v0.1.51@"
        "sha256:f1787473b62d0a5cfd18a67477dda24a549eeca9a81f5fa7ee670a1699299419"
    )
    assert pgdog["command"][-2:] == ["--session-mode", "false"]
    assert "${TRACECAT__LOADTEST_PGDOG_POOL_SIZE:-16}" in pgdog["command"]
    assert pgdog["depends_on"]["postgres_db"]["condition"] == "service_healthy"

    proxied_services = {
        "worker": "tracecat_worker",
        "executor": "tracecat_executor",
        "agent-worker": "tracecat_aux",
        "mcp": "tracecat_aux",
        "litellm": "tracecat_aux",
        "agent-executor": "tracecat_aux",
    }
    for service_name, logical_database in proxied_services.items():
        service = services[service_name]
        assert (
            f"@pgdog:6432/{logical_database}"
            in service["environment"]["TRACECAT__DB_URI"]
        )
        assert service["depends_on"]["pgdog"]["condition"] == "service_healthy"
    assert "migrations" not in services
    assert "api" not in services

    pgdog_environment = pgdog["environment"]
    expected_backend_pools = {
        "PGDOG_DATABASE_URL_1": (
            "tracecat_worker",
            "${TRACECAT__LOADTEST_PGDOG_WORKER_POOL_SIZE:-6}",
        ),
        "PGDOG_DATABASE_URL_2": (
            "tracecat_executor",
            "${TRACECAT__LOADTEST_PGDOG_EXECUTOR_POOL_SIZE:-16}",
        ),
        "PGDOG_DATABASE_URL_3": (
            "tracecat_aux",
            "${TRACECAT__LOADTEST_PGDOG_AUX_POOL_SIZE:-2}",
        ),
    }
    for variable, (logical_database, pool_size) in expected_backend_pools.items():
        backend_url = pgdog_environment[variable]
        assert f"/{logical_database}?database_name=postgres" in backend_url
        assert f"pool_size={pool_size}" in backend_url

    pgdog_config_path = EXPERIMENT_ENV_PATH.parents[1] / "pgdog" / "pgdog.toml"
    pgdog_config = tomllib.loads(pgdog_config_path.read_text(encoding="utf-8"))
    assert pgdog_config["general"]["pooler_mode"] == "transaction"
    assert pgdog_config["general"]["prepared_statements"] == "extended"
    assert pgdog_config["general"]["checkout_timeout"] == 60_000
    assert pgdog_config["general"]["openmetrics_port"] == 9090


def test_pgdog_matrix_holds_load_constant_across_db_and_pool_calibrations() -> None:
    cases = load_matrix(EXPERIMENT_ENV_PATH.with_name("pgdog-scatter.csv"))

    assert [case.case_id for case in cases] == [
        "pgdog-1k-exec3-pg-2c4g-pool16",
        "pgdog-1k-exec3-pg-1c1g-pool16",
        "pgdog-1k-exec3-pg-1c1g-pool8",
        "pgdog-1k-exec3-pg-1c1g-pool12",
        "pgdog-1k-exec6-pg-1c1g-pool24",
        "pgdog-1k-exec3-pg-1c1g-pool32-pgmax64",
    ]
    assert [case.enabled for case in cases] == [True, True, False, False, False, False]
    assert all(case.workflow_count == 4 for case in cases)
    assert all(case.branch_count == 256 for case in cases)
    assert all(case.workflow_count * case.branch_count == 1_024 for case in cases)
    assert all(case.max_connections == 32 for case in cases)

    large_db, small_db, pool8, pool12, doubled, pool32 = (
        case.process_environment() for case in cases
    )
    held_constant = {
        "TRACECAT__LOADTEST_EXECUTOR_MAX_CONCURRENT_ACTIVITIES": "8",
        "TRACECAT__LOADTEST_EXECUTOR_DB_POOL_SIZE": "9",
        "TRACECAT__LOADTEST_EXECUTOR_DB_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_POOL_SIZE": "5",
        "TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_API_DB_POOL_SIZE": "5",
        "TRACECAT__LOADTEST_API_DB_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_API_DB_AUTH_POOL_SIZE": "2",
        "TRACECAT__LOADTEST_API_DB_AUTH_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_WORKER_DB_POOL_SIZE": "5",
        "TRACECAT__LOADTEST_WORKER_DB_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_WORKER_DB_AUTH_POOL_SIZE": "4",
        "TRACECAT__LOADTEST_WORKER_DB_AUTH_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_TEMPORAL_NUM_HISTORY_SHARDS": "512",
        "TRACECAT__LOADTEST_PGDOG_CPUS": "1",
        "TRACECAT__LOADTEST_PGDOG_MEMORY": "512m",
        "TRACECAT__LOADTEST_PGDOG_POOL_SIZE": "16",
        "TRACECAT__LOADTEST_PGDOG_MIN_POOL_SIZE": "1",
        "TRACECAT__LOADTEST_PGDOG_WORKER_POOL_SIZE": "6",
        "TRACECAT__LOADTEST_PGDOG_AUX_POOL_SIZE": "2",
    }
    for environment in (large_db, small_db, pool8, pool12, doubled, pool32):
        assert {name: environment[name] for name in held_constant} == held_constant
    assert [
        environment["TRACECAT__LOADTEST_PGDOG_EXECUTOR_POOL_SIZE"]
        for environment in (large_db, small_db, pool8, pool12, doubled, pool32)
    ] == ["16", "16", "8", "12", "24", "32"]
    assert all(
        environment["TRACECAT__LOADTEST_EXECUTOR_REPLICAS"] == "3"
        for environment in (large_db, small_db, pool8, pool12, pool32)
    )
    assert (
        large_db["TRACECAT__LOADTEST_POSTGRES_DB_CPUS"],
        large_db["TRACECAT__LOADTEST_POSTGRES_DB_MEMORY"],
        large_db["TRACECAT__LOADTEST_POSTGRES_MAX_CONNECTIONS"],
    ) == ("2", "4g", "100")
    assert (
        small_db["TRACECAT__LOADTEST_POSTGRES_DB_CPUS"],
        small_db["TRACECAT__LOADTEST_POSTGRES_DB_MEMORY"],
        small_db["TRACECAT__LOADTEST_POSTGRES_MAX_CONNECTIONS"],
    ) == ("1", "1g", "50")
    assert all(
        environment["TRACECAT__LOADTEST_POSTGRES_DB_CPUS"] == "1"
        and environment["TRACECAT__LOADTEST_POSTGRES_DB_MEMORY"] == "1g"
        and environment["TRACECAT__LOADTEST_POSTGRES_MAX_CONNECTIONS"] == "50"
        for environment in (pool8, pool12, doubled)
    )
    assert doubled["TRACECAT__LOADTEST_EXECUTOR_REPLICAS"] == "6"
    assert (
        pool32["TRACECAT__LOADTEST_POSTGRES_DB_CPUS"],
        pool32["TRACECAT__LOADTEST_POSTGRES_DB_MEMORY"],
        pool32["TRACECAT__LOADTEST_POSTGRES_MAX_CONNECTIONS"],
    ) == ("1", "1g", "64")


def test_orchestration_overhead_matrix_is_a_matched_noop_control() -> None:
    cases = load_matrix(EXPERIMENT_ENV_PATH.with_name("orchestration-overhead.csv"))

    assert [case.case_id for case in cases] == [
        "orchestration-insert-row-1k-exec3",
        "orchestration-noop-reshape-1k-exec3",
    ]
    assert [case.load_type for case in cases] == [LoadType.SCATTER, LoadType.NOOP]
    assert all(case.enabled for case in cases)
    assert all(case.workflow_count == 4 for case in cases)
    assert all(case.branch_count == 256 for case in cases)
    assert all(case.workflow_count * case.branch_count == 1_024 for case in cases)
    assert all(case.one_shot for case in cases)

    insert_case, noop_case = cases
    assert insert_case.process_environment() == noop_case.process_environment()
    assert insert_case.environment_overrides == noop_case.environment_overrides

    environment = insert_case.process_environment()

    def pool_ceiling(service: str) -> int:
        prefix = f"TRACECAT__LOADTEST_{service}_DB_"
        return sum(
            int(environment[f"{prefix}{suffix}"])
            for suffix in (
                "POOL_SIZE",
                "MAX_OVERFLOW",
                "AUTH_POOL_SIZE",
                "AUTH_MAX_OVERFLOW",
            )
        )

    fixed_service_budget = sum(
        pool_ceiling(service)
        for service in (
            "API",
            "WORKER",
            "AGENT_WORKER",
            "MCP",
            "LITELLM",
            "AGENT_EXECUTOR",
        )
    )
    executor_budget = int(
        environment["TRACECAT__LOADTEST_EXECUTOR_REPLICAS"]
    ) * pool_ceiling("EXECUTOR")

    assert fixed_service_budget + executor_budget == 40
    assert fixed_service_budget + executor_budget < int(
        environment["TRACECAT__LOADTEST_POSTGRES_MAX_CONNECTIONS"]
    )


def test_scatter_burst_matrix_defines_independent_action_profile() -> None:
    cases = load_matrix(EXPERIMENT_ENV_PATH.with_name("scatter-burst.csv"))
    expected_profiles = (
        (
            "scatter-burst-1k-pg-1c1g",
            1_024,
            "1",
            "1g",
            "50",
            "128MB",
            "5",
            "0",
            "1",
            "8",
            "6",
            "3",
            True,
        ),
        (
            "scatter-burst-2k-pg-1c1g",
            2_048,
            "1",
            "1g",
            "50",
            "128MB",
            "5",
            "0",
            "1",
            "8",
            "6",
            "3",
            True,
        ),
        (
            "scatter-burst-1k-pg-2c4g",
            1_024,
            "2",
            "4g",
            "100",
            "1GB",
            "24",
            "16",
            "1",
            "8",
            "6",
            "3",
            True,
        ),
        (
            "scatter-burst-2k-pg-2c4g",
            2_048,
            "2",
            "4g",
            "100",
            "1GB",
            "24",
            "16",
            "1",
            "8",
            "6",
            "3",
            True,
        ),
        (
            "scatter-burst-1k-pg-2c4g-exec32",
            1_024,
            "2",
            "4g",
            "100",
            "1GB",
            "24",
            "16",
            "1",
            "32",
            "24",
            "8",
            False,
        ),
        (
            "scatter-burst-1k-pg-2c4g-exec3",
            1_024,
            "2",
            "4g",
            "100",
            "1GB",
            "24",
            "16",
            "3",
            "8",
            "6",
            "3",
            False,
        ),
    )
    fixed_environment: dict[str, str] = {
        "TRACECAT__LOADTEST_API_CPUS": "2",
        "TRACECAT__LOADTEST_API_MEMORY": "2g",
        "TRACECAT__LOADTEST_WORKER_CPUS": "4",
        "TRACECAT__LOADTEST_WORKER_MEMORY": "4g",
        "TRACECAT__LOADTEST_EXECUTOR_CPUS": "4",
        "TRACECAT__LOADTEST_EXECUTOR_MEMORY": "4g",
        "TRACECAT__LOADTEST_TEMPORAL_CPUS": "2",
        "TRACECAT__LOADTEST_TEMPORAL_MEMORY": "4g",
        "TRACECAT__LOADTEST_TEMPORAL_POSTGRES_DB_CPUS": "2",
        "TRACECAT__LOADTEST_TEMPORAL_POSTGRES_DB_MEMORY": "4g",
        "TRACECAT__LOADTEST_DSL_SCHEDULER_MAX_PENDING_TASKS": "64",
        "TRACECAT__LOADTEST_CHILD_WORKFLOW_DISPATCH_WINDOW": "16",
        "TRACECAT__LOADTEST_TEMPORAL_THREADPOOL_MAX_WORKERS": "100",
        "TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_ACTIVITIES": "100",
        "TRACECAT__LOADTEST_TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS": "100",
        "TRACECAT__LOADTEST_API_DB_AUTH_POOL_SIZE": "2",
        "TRACECAT__LOADTEST_API_DB_AUTH_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_WORKER_DB_AUTH_POOL_SIZE": "4",
        "TRACECAT__LOADTEST_WORKER_DB_AUTH_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_POOL_SIZE": "5",
        "TRACECAT__LOADTEST_EXECUTOR_DB_AUTH_MAX_OVERFLOW": "0",
        "TRACECAT__LOADTEST_DB_POOL_TIMEOUT": "60",
    }

    assert len(cases) == len(expected_profiles)
    for case, (
        case_id,
        workflow_count,
        postgres_cpus,
        postgres_memory,
        postgres_max_connections,
        postgres_shared_buffers,
        api_pool_size,
        api_max_overflow,
        executor_replicas,
        executor_max_concurrent_activities,
        executor_pool_size,
        executor_max_overflow,
        enabled,
    ) in zip(cases, expected_profiles, strict=True):
        environment = case.process_environment()

        assert case.case_id == case_id
        assert case.enabled is enabled
        assert case.load_type is LoadType.SCATTER
        assert case.workflow_count == workflow_count
        assert case.branch_count == 1
        assert case.workflow_count * case.branch_count in {1_024, 2_048}
        assert case.ramp_seconds == 1
        assert case.steady_state_seconds == 0
        assert case.warmup
        assert case.one_shot
        assert not case.abort_stops_polling
        assert case.max_connections == 64
        assert case.repeats == 1
        assert case.run_timeout_seconds == (900 if workflow_count == 1_024 else 1_800)
        assert {
            name: environment[name] for name in fixed_environment
        } == fixed_environment
        assert environment["TRACECAT__LOADTEST_POSTGRES_DB_CPUS"] == postgres_cpus
        assert environment["TRACECAT__LOADTEST_POSTGRES_DB_MEMORY"] == postgres_memory
        assert (
            environment["TRACECAT__LOADTEST_POSTGRES_MAX_CONNECTIONS"]
            == postgres_max_connections
        )
        assert (
            environment["TRACECAT__LOADTEST_POSTGRES_SHARED_BUFFERS"]
            == postgres_shared_buffers
        )
        assert environment["TRACECAT__LOADTEST_API_DB_POOL_SIZE"] == api_pool_size
        assert environment["TRACECAT__LOADTEST_API_DB_MAX_OVERFLOW"] == api_max_overflow
        assert environment["TRACECAT__LOADTEST_EXECUTOR_REPLICAS"] == executor_replicas
        assert (
            environment["TRACECAT__LOADTEST_EXECUTOR_MAX_CONCURRENT_ACTIVITIES"]
            == executor_max_concurrent_activities
        )
        assert (
            environment["TRACECAT__LOADTEST_EXECUTOR_DB_POOL_SIZE"]
            == executor_pool_size
        )
        assert (
            environment["TRACECAT__LOADTEST_EXECUTOR_DB_MAX_OVERFLOW"]
            == executor_max_overflow
        )
