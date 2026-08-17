from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from tracecat_benchmark import collector as collector_module
from tracecat_benchmark import matrix as matrix_module
from tracecat_benchmark import runner as runner_module
from tracecat_benchmark.matrix import (
    ClusterPorts,
    DeploymentContext,
    LoadTestCase,
    MatrixConfigurationError,
    MatrixExecutionError,
    MatrixOptions,
    execute_matrix,
    load_matrix,
    select_cases,
)
from tracecat_benchmark.models import run_id_fingerprint
from tracecat_benchmark.repository import (
    REPOSITORY_ROOT_ENV,
    RepositoryRootError,
    resolve_repository_root,
)

REPO_ROOT = resolve_repository_root(Path(__file__))


def _write_matrix_files(
    tmp_path: Path,
    matrix_contents: str,
) -> tuple[Path, Path]:
    experiment_env = tmp_path / "experiment.env"
    experiment_env.write_text(
        "\n".join(
            (
                "TRACECAT__LOADTEST_API_CPUS=0",
                "TRACECAT__LOADTEST_API_MEMORY=1g",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    matrix_path = tmp_path / "matrix.csv"
    matrix_path.write_text(matrix_contents, encoding="utf-8")
    return matrix_path, experiment_env


def _options(tmp_path: Path, matrix_path: Path) -> MatrixOptions:
    return MatrixOptions(
        matrix_path=matrix_path,
        artifact_root=tmp_path / "artifacts",
        workspace_name="load-test",
        selected_case_ids=(),
        dry_run=False,
        keep_cluster=False,
        sandbox=True,
        ee_multi_tenant=True,
        pgdog=False,
        startup_timeout_seconds=30.0,
    )


def test_repository_root_resolves_from_package_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REPOSITORY_ROOT_ENV, raising=False)

    assert (
        resolve_repository_root(REPO_ROOT / "packages/tracecat-benchmark") == REPO_ROOT
    )


def test_repository_root_rejects_invalid_explicit_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REPOSITORY_ROOT_ENV, str(tmp_path))

    with pytest.raises(RepositoryRootError, match=REPOSITORY_ROOT_ENV):
        resolve_repository_root()


def test_pgdog_option_adds_the_opt_in_compose_layer(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.csv"
    options = replace(_options(tmp_path, matrix_path), pgdog=True)

    assert matrix_module._cluster_flags(options) == [
        "--ee-multi-tenant",
        "true",
        "--sandbox",
        "--loadtest",
        "--compose-override",
        str(matrix_module.PGDOG_COMPOSE_FILE),
    ]


def test_matrix_rows_inherit_baseline_and_explicit_selection_includes_disabled(
    tmp_path: Path,
) -> None:
    matrix_path, experiment_env = _write_matrix_files(
        tmp_path,
        (
            "case_id,enabled,workflow_count,TRACECAT__LOADTEST_API_CPUS\n"
            "enabled-case,true,4,2\n"
            "disabled-case,false,,\n"
        ),
    )

    cases = load_matrix(matrix_path, experiment_env_path=experiment_env)

    assert tuple(case.case_id for case in select_cases(cases, ())) == ("enabled-case",)
    assert tuple(case.case_id for case in select_cases(cases, ("disabled-case",))) == (
        "disabled-case",
    )
    assert cases[0].workflow_count == 4
    assert cases[1].workflow_count == 1
    assert cases[0].process_environment() == {
        "TRACECAT__LOADTEST_API_CPUS": "2",
        "TRACECAT__LOADTEST_API_MEMORY": "1g",
    }
    assert cases[1].process_environment() == {
        "TRACECAT__LOADTEST_API_CPUS": "0",
        "TRACECAT__LOADTEST_API_MEMORY": "1g",
    }


def test_selected_matrix_cases_cannot_vary_temporal_history_shards(
    tmp_path: Path,
) -> None:
    matrix_path, experiment_env = _write_matrix_files(
        tmp_path,
        (
            "case_id,enabled,TRACECAT__LOADTEST_TEMPORAL_NUM_HISTORY_SHARDS\n"
            "first,true,512\n"
            "second,true,1024\n"
        ),
    )
    with experiment_env.open("a", encoding="utf-8") as handle:
        handle.write("TRACECAT__LOADTEST_TEMPORAL_NUM_HISTORY_SHARDS=512\n")

    cases = load_matrix(matrix_path, experiment_env_path=experiment_env)

    assert select_cases(cases, ("second",)) == (cases[1],)
    with pytest.raises(MatrixConfigurationError, match="history shard count is fixed"):
        select_cases(cases, ())


@pytest.mark.parametrize(
    ("matrix_contents", "expected_error"),
    [
        (
            "case_id,TRACECAT__LOADTEST_API_CPUZ\ncase,1\n",
            "unknown columns",
        ),
        (
            "case_id\nsame\nsame\n",
            "duplicate case_id values",
        ),
        (
            "case_id,sample_interval_seconds\ncase,1.1\n",
            "sample_interval_seconds must be at most 1",
        ),
        (
            "case_id,load_type,branch_count\ncase,scatter,0\n",
            "branch_count must be at least 1",
        ),
        (
            "case_id,load_type,branch_count\ncase,bulk,1001\n",
            "branch_count must be at most 1000 for bulk loads",
        ),
    ],
)
def test_matrix_validation_rejects_ambiguous_or_unsupported_cells(
    tmp_path: Path,
    matrix_contents: str,
    expected_error: str,
) -> None:
    matrix_path, experiment_env = _write_matrix_files(tmp_path, matrix_contents)

    with pytest.raises(MatrixConfigurationError, match=expected_error):
        load_matrix(matrix_path, experiment_env_path=experiment_env)


def test_cluster_loadtest_subcommand_delegates_to_package_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        (
            "#!/bin/sh\n"
            "printf '<repo-root=%s>\\n' \"$TRACECAT_LOADTEST_REPO_ROOT\"\n"
            'for arg in "$@"; do printf \'<%s>\\n\' "$arg"; done\n'
        ),
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

    result = subprocess.run(
        [
            REPO_ROOT / "scripts/cluster",
            "loadtest",
            "--matrix",
            "synthetic.csv",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == [
        f"<repo-root={REPO_ROOT}>",
        "<run>",
        "<--all-packages>",
        "<tracecat-benchmark>",
        "<--matrix>",
        "<synthetic.csv>",
        "<--dry-run>",
    ]


def test_loadtest_cluster_sync_keeps_workspace_package_installed() -> None:
    cluster_script = (REPO_ROOT / "scripts/cluster").read_text(encoding="utf-8")

    assert 'if [[ "$LOADTEST_OVERRIDE" == "true" ]]; then' in cluster_script
    assert "uv_sync_args+=(--all-packages)" in cluster_script
    assert 'uv sync "${uv_sync_args[@]}"' in cluster_script


def test_dry_run_previews_all_matrix_states_and_selected_overrides(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path = tmp_path / "matrix.csv"
    matrix_path.write_text(
        (
            "case_id,enabled,TRACECAT__LOADTEST_API_CPUS\n"
            "selected,true,2\n"
            "filtered,true,3\n"
            "disabled,false,4\n"
        ),
        encoding="utf-8",
    )

    matrix_module.main(
        [
            "--matrix",
            str(matrix_path),
            "--case",
            "selected",
            "--dry-run",
        ]
    )

    stdout = capsys.readouterr().out
    assert "Benchmark matrix · 1 will run / 3 rows" in stdout
    assert "workflows" in stdout
    assert "branches" in stdout
    assert "ramp /" in stdout
    assert "sustain" in stdout
    assert "RUN" in stdout
    assert "selected" in stdout
    assert "FILTERED" in stdout
    assert "filtered" in stdout
    assert "DISABLED" in stdout
    assert "disabled" in stdout
    assert "Selected resource overrides · 1" in stdout
    assert "Resource names omit the TRACECAT__LOADTEST_ prefix." in stdout
    assert "API_CPUS" in stdout
    assert "Dry run complete. Docker was not touched." in stdout


def test_cluster_loadtest_environment_file_overrides_dotenv_as_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$TRACECAT__LOADTEST_API_CPUS\"\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    loadtest_env = tmp_path / "case.env"
    loadtest_env.write_text(
        "TRACECAT__LOADTEST_API_CPUS=3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("PORTLESS", "0")
    monkeypatch.setenv(
        matrix_module.LOADTEST_ENV_FILE_VARIABLE,
        str(loadtest_env),
    )

    result = subprocess.run(
        [REPO_ROOT / "scripts/cluster", "7", "--loadtest", "config"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "3"


def test_matrix_builds_commands_accepted_by_runner_and_collector(
    tmp_path: Path,
) -> None:
    matrix_path, experiment_env = _write_matrix_files(
        tmp_path,
        (
            "case_id,load_type,workflow_count,branch_count,warmup,one_shot,"
            "abort_stops_polling\n"
            "command-check,bulk,3,16,false,true,true\n"
        ),
    )
    case = load_matrix(matrix_path, experiment_env_path=experiment_env)[0]
    options = _options(tmp_path, matrix_path)
    ports = ClusterPorts(
        public_api_url="http://localhost:180/api",
        postgres_target="localhost:5532",
        temporal_target="localhost:7333",
        temporal_worker_metrics_url="http://localhost:9564/metrics",
        temporal_executor_metrics_url="http://localhost:9565/metrics",
    )
    deployment = DeploymentContext(
        compose_files=(
            "docker-compose.dev.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        temporal_namespace="default",
        temporal_workflow_queue="workflow-queue",
        temporal_executor_queue="executor-queue",
        postgres_user="postgres",
        postgres_password="postgres",
    )

    collector_args = collector_module.build_parser().parse_args(
        matrix_module._collector_command(
            case,
            options,
            run_id="command-check-r1",
            workspace_id="00000000-0000-4000-8000-000000000000",
            cluster_num=7,
            ports=ports,
            deployment=deployment,
            activity_metrics_handoff=tmp_path / "activity-metrics.json",
        )[3:]
    )
    runner_args = runner_module.build_parser().parse_args(
        matrix_module._runner_command(
            case,
            options,
            run_id="command-check-r1",
            workspace_id="00000000-0000-4000-8000-000000000000",
            cluster_num=7,
            ports=ports,
            activity_metrics_handoff=tmp_path / "activity-metrics.json",
        )[3:]
    )

    assert collector_args.compose_file == [
        "docker-compose.dev.yml",
        "packages/tracecat-benchmark/docker-compose.loadtest.yml",
    ]
    assert collector_args.temporal_activity_task_queue == [
        "workflow-queue",
        "executor-queue",
    ]
    assert collector_args.temporal_executor_metrics_url == [
        "http://localhost:9565/metrics"
    ]
    assert collector_args.case_id == "command-check"
    assert runner_args.case_id == "command-check"
    assert runner_args.load_type == "bulk"
    assert runner_args.workflow_count == 3
    assert runner_args.branch_count == 16
    assert runner_args.no_warmup
    assert runner_args.one_shot
    assert runner_args.abort_stops_polling


def test_matrix_execution_reconfigures_one_cluster_and_resets_between_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix_path, experiment_env = _write_matrix_files(
        tmp_path,
        ("case_id,repeats,TRACECAT__LOADTEST_API_CPUS\nfirst,2,1\nsecond,1,2\n"),
    )
    cases = load_matrix(matrix_path, experiment_env_path=experiment_env)
    options = _options(tmp_path, matrix_path)
    ports = ClusterPorts(
        public_api_url="http://localhost:180/api",
        postgres_target="localhost:5532",
        temporal_target="localhost:7333",
        temporal_worker_metrics_url="http://localhost:9564/metrics",
        temporal_executor_metrics_url="http://localhost:9565/metrics",
    )
    deployment = DeploymentContext(
        compose_files=(
            "docker-compose.dev.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        temporal_namespace="default",
        temporal_workflow_queue="workflow-queue",
        temporal_executor_queue="executor-queue",
        postgres_user="postgres",
        postgres_password="postgres",
    )
    events: list[str] = []
    base_environment_loads = 0

    def assert_case_environment(env: dict[str, str], expected_cpu: str) -> None:
        env_path = Path(env[matrix_module.LOADTEST_ENV_FILE_VARIABLE])
        assert env_path.is_file()
        assert env["TRACECAT__LOADTEST_API_CPUS"] == expected_cpu
        assert f"TRACECAT__LOADTEST_API_CPUS={expected_cpu}" in (
            env_path.read_text(encoding="utf-8")
        )

    def fake_start(
        _options: MatrixOptions,
        *,
        env: dict[str, str],
        log_path: Path,
    ) -> int:
        assert_case_environment(env, "1")
        assert env["BASE"] == "before-cluster-up"
        assert log_path.name == "orchestration.log"
        events.append("start:1")
        return 7

    def fake_reconfigure(
        _options: MatrixOptions,
        *,
        cluster_num: int,
        env: dict[str, str],
        log_path: Path,
    ) -> None:
        assert cluster_num == 7
        assert_case_environment(env, "2")
        assert env["BASE"] == "after-cluster-up"
        assert log_path.name == "orchestration.log"
        events.append("reconfigure:2")

    def fake_resolve(
        _options: MatrixOptions,
        *,
        cluster_num: int,
        env: dict[str, str],
    ) -> tuple[ClusterPorts, DeploymentContext]:
        assert cluster_num == 7
        assert env["BASE"] == "after-cluster-up"
        events.append(f"resolve:{env['TRACECAT__LOADTEST_API_CPUS']}")
        return ports, deployment

    def fake_wait(_public_api_url: str, *, timeout_seconds: float) -> None:
        assert timeout_seconds == 30.0
        events.append("wait")

    def fake_bootstrap(
        _options: MatrixOptions,
        *,
        public_api_url: str,
        env: dict[str, str],
    ) -> str:
        assert public_api_url == ports.public_api_url
        assert_case_environment(env, "1")
        events.append("bootstrap")
        return "00000000-0000-4000-8000-000000000000"

    def fake_provision(
        *,
        workspace_id: str,
        ports: ClusterPorts,
        deployment: DeploymentContext,
        env: dict[str, str],
    ) -> str:
        del ports, deployment
        assert workspace_id == "00000000-0000-4000-8000-000000000000"
        assert_case_environment(env, "1")
        events.append("provision")
        return "postgresql://monitor@localhost:5532/postgres"

    def fake_reset(
        *,
        workspace_id: str,
        public_api_url: str,
        env: dict[str, str],
    ) -> None:
        del workspace_id, public_api_url, env
        events.append("reset")

    def fake_run_cell(
        case: LoadTestCase,
        _options: MatrixOptions,
        *,
        run_id: str,
        workspace_id: str,
        cluster_num: int,
        ports: ClusterPorts,
        deployment: DeploymentContext,
        env: dict[str, str],
        process_log_dir: Path,
        activity_metrics_handoff: Path,
    ) -> None:
        del run_id, workspace_id, ports, deployment, activity_metrics_handoff
        assert cluster_num == 7
        assert process_log_dir.parent.name == "runs"
        expected_cpu = "1" if case.case_id == "first" else "2"
        assert_case_environment(env, expected_cpu)
        assert env[matrix_module.LOADTEST_MONITOR_DSN_VARIABLE].startswith(
            "postgresql://"
        )
        events.append(f"run:{case.case_id}")

    def fake_down(
        _options: MatrixOptions,
        *,
        cluster_num: int,
        env: dict[str, str],
        log_path: Path,
    ) -> None:
        assert cluster_num == 7
        assert matrix_module.LOADTEST_ENV_FILE_VARIABLE not in env
        assert env["BASE"] == "after-cluster-up"
        assert log_path.name == "orchestration.log"
        events.append("down")

    def fake_load_base_environment(_repo_root: Path) -> dict[str, str]:
        nonlocal base_environment_loads
        base_environment_loads += 1
        return {
            "BASE": (
                "before-cluster-up"
                if base_environment_loads == 1
                else "after-cluster-up"
            )
        }

    monkeypatch.setattr(
        matrix_module,
        "_load_base_process_environment",
        fake_load_base_environment,
    )
    monkeypatch.setattr(matrix_module, "_start_new_cluster", fake_start)
    monkeypatch.setattr(matrix_module, "_reconfigure_cluster", fake_reconfigure)
    monkeypatch.setattr(matrix_module, "_resolve_deployment_context", fake_resolve)
    monkeypatch.setattr(matrix_module, "_wait_for_api", fake_wait)
    monkeypatch.setattr(matrix_module, "_bootstrap_workspace", fake_bootstrap)
    monkeypatch.setattr(matrix_module, "_provision_monitor", fake_provision)
    monkeypatch.setattr(matrix_module, "_reset_fixture_table", fake_reset)
    monkeypatch.setattr(matrix_module, "_run_cell", fake_run_cell)
    monkeypatch.setattr(matrix_module, "_down_cluster", fake_down)

    assert execute_matrix(cases, options) == 0
    assert base_environment_loads == 2
    assert events == [
        "start:1",
        "resolve:1",
        "wait",
        "bootstrap",
        "provision",
        "run:first",
        "reset",
        "run:first",
        "reconfigure:2",
        "resolve:2",
        "wait",
        "reset",
        "run:second",
        "down",
    ]


def test_matrix_failure_leaves_cluster_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path, experiment_env = _write_matrix_files(
        tmp_path,
        "case_id\nfailure\n",
    )
    cases = load_matrix(matrix_path, experiment_env_path=experiment_env)
    options = _options(tmp_path, matrix_path)
    ports = ClusterPorts(
        public_api_url="http://localhost:180/api",
        postgres_target="localhost:5532",
        temporal_target="localhost:7333",
        temporal_worker_metrics_url="http://localhost:9564/metrics",
        temporal_executor_metrics_url="http://localhost:9565/metrics",
    )
    deployment = DeploymentContext(
        compose_files=(
            "docker-compose.dev.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        temporal_namespace="default",
        temporal_workflow_queue="workflow-queue",
        temporal_executor_queue="executor-queue",
        postgres_user="postgres",
        postgres_password="postgres",
    )
    down_called = False

    def fail_run(*_args: object, **_kwargs: object) -> None:
        raise MatrixExecutionError("synthetic runner failure")

    def fail_if_down_called(*_args: object, **_kwargs: object) -> None:
        nonlocal down_called
        down_called = True

    monkeypatch.setattr(
        matrix_module,
        "_load_base_process_environment",
        lambda _repo_root: {},
    )
    monkeypatch.setattr(matrix_module, "_start_new_cluster", lambda *_a, **_kw: 7)
    monkeypatch.setattr(
        matrix_module,
        "_resolve_deployment_context",
        lambda *_a, **_kw: (ports, deployment),
    )
    monkeypatch.setattr(matrix_module, "_wait_for_api", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        matrix_module,
        "_bootstrap_workspace",
        lambda *_a, **_kw: "00000000-0000-4000-8000-000000000000",
    )
    monkeypatch.setattr(
        matrix_module,
        "_provision_monitor",
        lambda **_kw: "postgresql://monitor@localhost:5532/postgres",
    )
    monkeypatch.setattr(matrix_module, "_run_cell", fail_run)
    monkeypatch.setattr(matrix_module, "_down_cluster", fail_if_down_called)

    assert execute_matrix(cases, options) == 1
    assert not down_called
    stderr = capsys.readouterr().err
    assert "synthetic runner failure" in stderr
    assert "Cluster 7 remains running for diagnosis." in stderr


def test_visible_command_failure_handles_uncaptured_stderr() -> None:
    result = subprocess.CompletedProcess[str](
        args=["synthetic-command"],
        returncode=1,
        stdout=None,
        stderr=None,
    )

    error = matrix_module._display_command_failure("synthetic command", result)

    assert str(error) == "synthetic command failed with exit code 1"


def test_cluster_start_output_is_logged_without_replaying_to_tty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path = tmp_path / "matrix.csv"
    matrix_path.touch()
    options = _options(tmp_path, matrix_path)
    cluster_script = tmp_path / "cluster"
    cluster_script.write_text(
        (
            "#!/bin/sh\n"
            'for arg in "$@"; do echo "arg:$arg"; done\n'
            "echo \"Auto-selected new cluster 7 (global) for worktree 'test'\"\n"
            "echo 'compose progress on stdout'\n"
            "echo 'compose diagnostic on stderr' >&2\n"
        ),
        encoding="utf-8",
    )
    cluster_script.chmod(0o755)
    orchestration_log = tmp_path / "orchestration.log"
    orchestration_log.touch()
    monkeypatch.setattr(matrix_module, "CLUSTER_SCRIPT", cluster_script)
    environment = dict(os.environ)
    environment[matrix_module.LOADTEST_EXECUTOR_REPLICAS_VARIABLE] = "3"

    cluster_num = matrix_module._start_new_cluster(
        options,
        env=environment,
        log_path=orchestration_log,
    )

    captured = capsys.readouterr()
    assert cluster_num == 7
    assert captured.out == ""
    assert captured.err == ""
    logged = orchestration_log.read_text(encoding="utf-8")
    assert "cluster startup" in logged
    assert "Auto-selected new cluster 7" in logged
    assert "compose progress on stdout" in logged
    assert "compose diagnostic on stderr" in logged
    assert "arg:--scale" in logged
    assert "arg:executor=3" in logged


def test_cluster_startup_timeout_interrupts_process_and_retains_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StalledProcess:
        def __init__(self) -> None:
            self.interrupted = False

        def poll(self) -> int | None:
            return 130 if self.interrupted else None

        def send_signal(self, _signal: int) -> None:
            self.interrupted = True

        def wait(self, timeout: float | None = None) -> int:
            if not self.interrupted:
                raise subprocess.TimeoutExpired("cluster", timeout or 0.0)
            return 130

        def terminate(self) -> None:
            self.interrupted = True

        def kill(self) -> None:
            self.interrupted = True

    process = StalledProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    options = replace(
        _options(tmp_path, tmp_path / "matrix.csv"),
        startup_timeout_seconds=0.25,
    )
    orchestration_log = tmp_path / "orchestration.log"
    orchestration_log.touch()

    with pytest.raises(MatrixExecutionError, match="timed out after 0.25 seconds"):
        matrix_module._start_new_cluster(
            options,
            env=dict(os.environ),
            log_path=orchestration_log,
        )

    assert process.interrupted
    assert "cluster startup" in orchestration_log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("replica_count", "expected_urls"),
    [
        (1, ("http://localhost:9565/metrics",)),
        (
            3,
            (
                "http://localhost:9565/metrics",
                "http://localhost:9566/metrics",
                "http://localhost:9567/metrics",
            ),
        ),
    ],
)
def test_executor_metrics_ports_are_discovered_per_replica(
    replica_count: int,
    expected_urls: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix_path = tmp_path / "matrix.csv"
    matrix_path.touch()
    options = _options(tmp_path, matrix_path)
    commands: list[list[str]] = []

    def fake_run_capture(
        args: list[str],
        *,
        env: dict[str, str],
        label: str,
    ) -> str:
        del env, label
        commands.append(args)
        replica_index = int(args[args.index("--index") + 1])
        return f"127.0.0.1:{9564 + replica_index}\n"

    monkeypatch.setattr(matrix_module, "_run_capture", fake_run_capture)

    urls = matrix_module._resolve_executor_metrics_urls(
        options,
        cluster_num=7,
        env={matrix_module.LOADTEST_EXECUTOR_REPLICAS_VARIABLE: str(replica_count)},
    )

    assert urls == expected_urls
    assert [command[command.index("--index") + 1] for command in commands] == [
        str(index) for index in range(1, replica_count + 1)
    ]


def test_logged_command_failure_prints_only_a_bounded_log_tail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = tmp_path / "failing-command"
    command.write_text(
        (
            "#!/bin/sh\n"
            "echo 'early-only-marker'\n"
            "line=1\n"
            'while [ "$line" -le 45 ]; do\n'
            '  echo "diagnostic-$line"\n'
            "  line=$((line + 1))\n"
            "done\n"
            "exit 9\n"
        ),
        encoding="utf-8",
    )
    command.chmod(0o755)
    log_path = tmp_path / "command.log"
    log_path.touch()

    with pytest.raises(MatrixExecutionError) as exc_info:
        matrix_module._run_logged(
            [str(command)],
            env=dict(os.environ),
            label="synthetic command",
            log_path=log_path,
        )

    captured = capsys.readouterr()
    message = str(exc_info.value)
    assert captured.out == ""
    assert captured.err == ""
    assert "synthetic command failed with exit code 9" in message
    assert f"Full output: {log_path}" in message
    assert "diagnostic-45" in message
    assert "early-only-marker" not in message
    assert "early-only-marker" in log_path.read_text(encoding="utf-8")


def test_runner_waits_for_collector_readiness_and_routes_process_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path, experiment_env = _write_matrix_files(
        tmp_path,
        "case_id\nquiet-run\n",
    )
    case = load_matrix(matrix_path, experiment_env_path=experiment_env)[0]
    options = _options(tmp_path, matrix_path)
    ports = ClusterPorts(
        public_api_url="http://localhost:180/api",
        postgres_target="localhost:5532",
        temporal_target="localhost:7333",
        temporal_worker_metrics_url="http://localhost:9564/metrics",
        temporal_executor_metrics_url="http://localhost:9565/metrics",
    )
    deployment = DeploymentContext(
        compose_files=(
            "docker-compose.dev.yml",
            "packages/tracecat-benchmark/docker-compose.loadtest.yml",
        ),
        temporal_namespace="default",
        temporal_workflow_queue="workflow-queue",
        temporal_executor_queue="executor-queue",
        postgres_user="postgres",
        postgres_password="postgres",
    )
    process_log_dir = tmp_path / "matrix-logs" / "runs" / "quiet-run"
    artifact_dir = options.artifact_root / run_id_fingerprint("quiet-run-r1")
    ready_path = artifact_dir / "collector_ready.json"

    def fake_collector_command(
        *_args: object,
        **_kwargs: object,
    ) -> list[str]:
        return [
            sys.executable,
            "-c",
            (
                "import sys, time; from pathlib import Path; "
                "ready = Path(sys.argv[1]); "
                "ready.parent.mkdir(parents=True); "
                "print('collector stdout'); "
                "print('collector stderr', file=sys.stderr); "
                "time.sleep(0.1); "
                "ready.write_text('{}', encoding='utf-8'); "
                "time.sleep(0.2)"
            ),
            str(ready_path),
        ]

    def fake_runner_command(
        *_args: object,
        **_kwargs: object,
    ) -> list[str]:
        return [
            sys.executable,
            "-c",
            (
                "import sys, time; from pathlib import Path; "
                "assert Path(sys.argv[1]).is_file(); "
                "print('runner stdout'); "
                "print('runner stderr', file=sys.stderr); "
                "time.sleep(0.05)"
            ),
            str(ready_path),
        ]

    monkeypatch.setattr(
        matrix_module,
        "_collector_command",
        fake_collector_command,
    )
    monkeypatch.setattr(matrix_module, "_runner_command", fake_runner_command)

    matrix_module._run_cell(
        case,
        options,
        run_id="quiet-run-r1",
        workspace_id="00000000-0000-4000-8000-000000000000",
        cluster_num=7,
        ports=ports,
        deployment=deployment,
        env=dict(os.environ),
        process_log_dir=process_log_dir,
        activity_metrics_handoff=tmp_path / "activity-metrics.json",
    )

    captured = capsys.readouterr()
    assert "Collector ready; starting load" in captured.out
    assert "Load generator completed" in captured.out
    assert "runner stdout" not in captured.out
    assert "runner stderr" not in captured.err
    assert "collector stdout" not in captured.out
    assert "collector stderr" not in captured.err
    runner_log = (process_log_dir / "runner.log").read_text(encoding="utf-8")
    collector_log = (process_log_dir / "collector.log").read_text(encoding="utf-8")
    assert "runner stdout" in runner_log
    assert "runner stderr" in runner_log
    assert "collector stdout" in collector_log
    assert "collector stderr" in collector_log
