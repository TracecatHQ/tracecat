"""CSV-driven orchestration for isolated Tracecat workflow load tests.

This module is invoked through ``just cluster loadtest``. It owns the complete
local lifecycle for a matrix:

1. validate every CSV row before touching Docker;
2. start one fresh numbered load-test cluster;
3. apply each row's resource configuration to that isolated project;
4. run the collector and workload runner as a coordinated pair;
5. reset the synthetic fixture between repeats and matrix cells; and
6. stop the cluster after a successful matrix while retaining its volumes.

On any failure, the cluster is intentionally left running for diagnosis.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, NoReturn, TextIO, cast
from urllib.parse import quote
from uuid import UUID

import httpx
import yaml
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .activity_metrics import ActivityHistoryMetrics, TemporalSdkMetrics
from .models import (
    MAX_BULK_BRANCH_COUNT,
    MAX_LOADTEST_EXECUTOR_REPLICAS,
    LoadType,
    run_id_fingerprint,
)
from .repository import REPOSITORY_ROOT_ENV, resolve_repository_root

REPO_ROOT: Final = resolve_repository_root()
CLUSTER_SCRIPT: Final = REPO_ROOT / "scripts/cluster"
PGDOG_COMPOSE_FILE: Final = (
    REPO_ROOT / "packages/tracecat-benchmark/docker-compose.pgdog.yml"
)
EXPERIMENT_ENV_PATH: Final = (
    Path(__file__).with_name("examples") / "experiment.env.example"
)
DEFAULT_ARTIFACT_ROOT: Final = Path("/tmp/tracecat-load-test")
DEFAULT_WORKSPACE_NAME: Final = "load-test"
LOADTEST_ENV_FILE_VARIABLE: Final = "TRACECAT_LOADTEST_ENV_FILE"
LOADTEST_MONITOR_DSN_VARIABLE: Final = "TRACECAT_LOADTEST_MONITOR_DSN"
LOADTEST_PROVISION_DSN_VARIABLE: Final = "TRACECAT_LOADTEST_PROVISION_DSN"
LOADTEST_EXECUTOR_REPLICAS_VARIABLE: Final = "TRACECAT__LOADTEST_EXECUTOR_REPLICAS"
TEMPORAL_HISTORY_SHARDS_VARIABLE: Final = (
    "TRACECAT__LOADTEST_TEMPORAL_NUM_HISTORY_SHARDS"
)
LOADTEST_ENV_NAME_RE: Final = re.compile(r"^TRACECAT__LOADTEST_[A-Z0-9_]+$")
CASE_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
CLUSTER_NUMBER_RE: Final = re.compile(r"^Auto-selected new cluster ([0-9]+)\b")
MIN_RECOVERY_SECONDS: Final = 5.0
FAILURE_LOG_TAIL_LINES: Final = 40

MATRIX_COLUMNS: Final = frozenset(
    {
        "case_id",
        "enabled",
        "load_type",
        "workflow_count",
        "branch_count",
        "ramp_seconds",
        "steady_state_seconds",
        "payload_bytes",
        "run_timeout_seconds",
        "poll_interval_seconds",
        "collector_ready_timeout_seconds",
        "sample_interval_seconds",
        "recovery_seconds",
        "warmup",
        "one_shot",
        "abort_stops_polling",
        "max_connections",
        "repeats",
    }
)


class MatrixConfigurationError(ValueError):
    """The matrix cannot be executed safely as written."""


class MatrixExecutionError(RuntimeError):
    """A matrix lifecycle command failed."""


@dataclass(frozen=True, slots=True)
class LoadTestCase:
    """One validated row from a load-test CSV matrix."""

    case_id: str
    enabled: bool
    load_type: LoadType
    workflow_count: int
    branch_count: int
    ramp_seconds: float
    steady_state_seconds: float
    payload_bytes: int
    run_timeout_seconds: float
    poll_interval_seconds: float
    collector_ready_timeout_seconds: float
    sample_interval_seconds: float
    recovery_seconds: float
    warmup: bool
    one_shot: bool
    abort_stops_polling: bool
    max_connections: int
    repeats: int
    environment: tuple[tuple[str, str], ...]
    environment_overrides: tuple[tuple[str, str], ...]

    def process_environment(self) -> dict[str, str]:
        """Return the effective load-test environment for this row."""
        return dict(self.environment)


@dataclass(frozen=True, slots=True)
class MatrixOptions:
    """Command-level controls shared by every selected matrix row."""

    matrix_path: Path
    artifact_root: Path
    workspace_name: str
    selected_case_ids: tuple[str, ...]
    dry_run: bool
    keep_cluster: bool
    sandbox: bool
    ee_multi_tenant: bool
    pgdog: bool
    startup_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ClusterPorts:
    """Direct host endpoints for the selected numbered cluster."""

    public_api_url: str
    postgres_target: str
    temporal_target: str
    temporal_worker_metrics_url: str
    temporal_executor_metrics_url: str
    temporal_executor_metrics_urls: tuple[str, ...] = ()
    api_db_pool_metrics_url: str = ""
    worker_db_pool_metrics_url: str = ""
    executor_db_pool_metrics_url: str = ""
    executor_db_pool_metrics_urls: tuple[str, ...] = ()
    pgdog_metrics_url: str = ""


@dataclass(frozen=True, slots=True)
class DeploymentContext:
    """Values resolved from the exact Compose model deployed for a row."""

    compose_files: tuple[str, ...]
    temporal_namespace: str
    temporal_workflow_queue: str
    temporal_executor_queue: str
    postgres_user: str
    postgres_password: str


@dataclass(frozen=True, slots=True)
class MatrixLogContext:
    """Durable process logs kept outside collector-owned artifact directories."""

    directory: Path
    orchestration_log: Path

    def process_log_directory(self, run_id: str) -> Path:
        return self.directory / "runs" / run_id_fingerprint(run_id)


def _parse_bool(value: str, *, field: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise MatrixConfigurationError(f"{field} must be a boolean, got {value!r}")


def _parse_int(
    value: str,
    *,
    field: str,
    default: int,
    minimum: int,
) -> int:
    if not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise MatrixConfigurationError(
            f"{field} must be an integer, got {value!r}"
        ) from exc
    if parsed < minimum:
        raise MatrixConfigurationError(f"{field} must be at least {minimum}")
    return parsed


def _parse_float(
    value: str,
    *,
    field: str,
    default: float,
    minimum: float,
    maximum: float | None = None,
) -> float:
    if not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise MatrixConfigurationError(
            f"{field} must be numeric, got {value!r}"
        ) from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise MatrixConfigurationError(
            f"{field} must be finite and at least {minimum:g}"
        )
    if maximum is not None and parsed > maximum:
        raise MatrixConfigurationError(f"{field} must be at most {maximum:g}")
    return parsed


def load_experiment_environment(
    path: Path = EXPERIMENT_ENV_PATH,
) -> dict[str, str]:
    """Load the checked-in baseline used for sparse CSV rows."""
    if not path.is_file():
        raise MatrixConfigurationError(f"experiment environment not found: {path}")

    environment: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not LOADTEST_ENV_NAME_RE.fullmatch(name):
            raise MatrixConfigurationError(
                f"{path}:{line_number} is not a load-test environment assignment"
            )
        if name in environment:
            raise MatrixConfigurationError(f"{path}:{line_number} duplicates {name}")
        environment[name] = value

    if not environment:
        raise MatrixConfigurationError(
            f"experiment environment contains no load-test variables: {path}"
        )
    return environment


def _cell(row: dict[str, str | None], name: str) -> str:
    value = row.get(name)
    return value.strip() if value is not None else ""


def _parse_case(
    row: dict[str, str | None],
    *,
    row_number: int,
    baseline_environment: dict[str, str],
) -> LoadTestCase:
    case_id = _cell(row, "case_id")
    if not CASE_ID_RE.fullmatch(case_id):
        raise MatrixConfigurationError(
            f"row {row_number}: case_id must match {CASE_ID_RE.pattern!r}"
        )

    raw_load_type = _cell(row, "load_type") or LoadType.SCATTER.value
    try:
        load_type = LoadType(raw_load_type)
    except ValueError as exc:
        choices = ", ".join(load_type.value for load_type in LoadType)
        raise MatrixConfigurationError(
            f"row {row_number}: load_type must be one of {choices}"
        ) from exc

    effective_environment = dict(baseline_environment)
    overrides: dict[str, str] = {}
    for name in baseline_environment:
        value = _cell(row, name)
        if value:
            if "\n" in value or "\r" in value or "\x00" in value:
                raise MatrixConfigurationError(
                    f"row {row_number}: {name} contains an unsupported control character"
                )
            effective_environment[name] = value
            overrides[name] = value

    prefix = f"row {row_number}"
    raw_executor_replicas = effective_environment.get(
        LOADTEST_EXECUTOR_REPLICAS_VARIABLE,
        "1",
    )
    try:
        executor_replicas = int(raw_executor_replicas)
    except ValueError as exc:
        raise MatrixConfigurationError(
            f"{prefix} {LOADTEST_EXECUTOR_REPLICAS_VARIABLE} must be an integer"
        ) from exc
    if not 1 <= executor_replicas <= MAX_LOADTEST_EXECUTOR_REPLICAS:
        raise MatrixConfigurationError(
            f"{prefix} {LOADTEST_EXECUTOR_REPLICAS_VARIABLE} must be between "
            f"1 and {MAX_LOADTEST_EXECUTOR_REPLICAS}"
        )
    workflow_count = _parse_int(
        _cell(row, "workflow_count"),
        field=f"{prefix} workflow_count",
        default=1,
        minimum=1,
    )
    branch_count = _parse_int(
        _cell(row, "branch_count"),
        field=f"{prefix} branch_count",
        default=1,
        minimum=1,
    )
    if load_type is LoadType.BULK and branch_count > MAX_BULK_BRANCH_COUNT:
        raise MatrixConfigurationError(
            f"{prefix} branch_count must be at most {MAX_BULK_BRANCH_COUNT} "
            "for bulk loads"
        )
    return LoadTestCase(
        case_id=case_id,
        enabled=_parse_bool(
            _cell(row, "enabled"),
            field=f"{prefix} enabled",
            default=True,
        ),
        load_type=load_type,
        workflow_count=workflow_count,
        branch_count=branch_count,
        ramp_seconds=_parse_float(
            _cell(row, "ramp_seconds"),
            field=f"{prefix} ramp_seconds",
            default=10.0,
            minimum=0.0,
        ),
        steady_state_seconds=_parse_float(
            _cell(row, "steady_state_seconds"),
            field=f"{prefix} steady_state_seconds",
            default=60.0,
            minimum=0.0,
        ),
        payload_bytes=_parse_int(
            _cell(row, "payload_bytes"),
            field=f"{prefix} payload_bytes",
            default=256,
            minimum=0,
        ),
        run_timeout_seconds=_parse_float(
            _cell(row, "run_timeout_seconds"),
            field=f"{prefix} run_timeout_seconds",
            default=300.0,
            minimum=sys.float_info.min,
        ),
        poll_interval_seconds=_parse_float(
            _cell(row, "poll_interval_seconds"),
            field=f"{prefix} poll_interval_seconds",
            default=1.0,
            minimum=sys.float_info.min,
        ),
        collector_ready_timeout_seconds=_parse_float(
            _cell(row, "collector_ready_timeout_seconds"),
            field=f"{prefix} collector_ready_timeout_seconds",
            default=120.0,
            minimum=sys.float_info.min,
        ),
        sample_interval_seconds=_parse_float(
            _cell(row, "sample_interval_seconds"),
            field=f"{prefix} sample_interval_seconds",
            default=0.5,
            minimum=sys.float_info.min,
            maximum=1.0,
        ),
        recovery_seconds=_parse_float(
            _cell(row, "recovery_seconds"),
            field=f"{prefix} recovery_seconds",
            default=60.0,
            minimum=MIN_RECOVERY_SECONDS,
        ),
        warmup=_parse_bool(
            _cell(row, "warmup"),
            field=f"{prefix} warmup",
            default=True,
        ),
        one_shot=_parse_bool(
            _cell(row, "one_shot"),
            field=f"{prefix} one_shot",
            default=False,
        ),
        abort_stops_polling=_parse_bool(
            _cell(row, "abort_stops_polling"),
            field=f"{prefix} abort_stops_polling",
            default=False,
        ),
        max_connections=_parse_int(
            _cell(row, "max_connections"),
            field=f"{prefix} max_connections",
            default=200,
            minimum=1,
        ),
        repeats=_parse_int(
            _cell(row, "repeats"),
            field=f"{prefix} repeats",
            default=1,
            minimum=1,
        ),
        environment=tuple(sorted(effective_environment.items())),
        environment_overrides=tuple(sorted(overrides.items())),
    )


def load_matrix(
    path: Path,
    *,
    experiment_env_path: Path = EXPERIMENT_ENV_PATH,
) -> tuple[LoadTestCase, ...]:
    """Validate and return every nonempty row from a CSV matrix."""
    if not path.is_file():
        raise MatrixConfigurationError(f"matrix file not found: {path}")

    baseline_environment = load_experiment_environment(experiment_env_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        raw_fieldnames = reader.fieldnames
        if raw_fieldnames is None:
            raise MatrixConfigurationError("matrix CSV has no header row")
        fieldnames = tuple(name.strip() for name in raw_fieldnames)
        if any(not name for name in fieldnames):
            raise MatrixConfigurationError("matrix CSV contains a blank header")
        if len(fieldnames) != len(set(fieldnames)):
            raise MatrixConfigurationError("matrix CSV contains duplicate headers")
        if fieldnames != tuple(raw_fieldnames):
            raise MatrixConfigurationError(
                "matrix CSV headers must not contain surrounding whitespace"
            )
        if "case_id" not in fieldnames:
            raise MatrixConfigurationError("matrix CSV requires a case_id column")

        allowed_columns = MATRIX_COLUMNS | baseline_environment.keys()
        unknown_columns = sorted(set(fieldnames) - allowed_columns)
        if unknown_columns:
            raise MatrixConfigurationError(
                "matrix CSV contains unknown columns: " + ", ".join(unknown_columns)
            )

        cases: list[LoadTestCase] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise MatrixConfigurationError(
                    f"row {row_number}: contains more values than the header"
                )
            if not any((value or "").strip() for value in row.values()):
                continue
            cases.append(
                _parse_case(
                    row,
                    row_number=row_number,
                    baseline_environment=baseline_environment,
                )
            )

    if not cases:
        raise MatrixConfigurationError("matrix CSV contains no cases")

    case_ids = [case.case_id for case in cases]
    duplicate_ids = sorted(
        case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
    )
    if duplicate_ids:
        raise MatrixConfigurationError(
            "matrix CSV contains duplicate case_id values: " + ", ".join(duplicate_ids)
        )
    return tuple(cases)


def select_cases(
    cases: tuple[LoadTestCase, ...],
    selected_case_ids: tuple[str, ...],
) -> tuple[LoadTestCase, ...]:
    """Apply enabled flags or an explicit case selection."""
    if not selected_case_ids:
        selected = tuple(case for case in cases if case.enabled)
        if not selected:
            raise MatrixConfigurationError("matrix has no enabled cases")
        _validate_immutable_environment(selected)
        return selected

    requested = set(selected_case_ids)
    known = {case.case_id for case in cases}
    unknown = sorted(requested - known)
    if unknown:
        raise MatrixConfigurationError("unknown --case values: " + ", ".join(unknown))
    selected = tuple(case for case in cases if case.case_id in requested)
    _validate_immutable_environment(selected)
    return selected


def _validate_immutable_environment(cases: tuple[LoadTestCase, ...]) -> None:
    """Reject per-row changes to settings fixed by persistent cluster state."""
    history_shards = {
        case.process_environment().get(TEMPORAL_HISTORY_SHARDS_VARIABLE)
        for case in cases
    }
    if len(history_shards) > 1:
        raise MatrixConfigurationError(
            f"selected cases vary {TEMPORAL_HISTORY_SHARDS_VARIABLE}, but Temporal "
            "history shard count is fixed when the shared persistence store is created"
        )


def _load_base_process_environment(repo_root: Path) -> dict[str, str]:
    """Load .env without letting it override the invoking shell."""
    file_values = dotenv_values(repo_root / ".env")
    environment = {
        key: value for key, value in file_values.items() if value is not None
    }
    environment.update(os.environ)
    environment[REPOSITORY_ROOT_ENV] = str(repo_root)
    if "TRACECAT_LOADTEST_PASSWORD" not in environment:
        dev_password = environment.get("TRACECAT__DEV_USER_PASSWORD")
        if dev_password:
            environment["TRACECAT_LOADTEST_PASSWORD"] = dev_password
    if "TRACECAT_LOADTEST_EMAIL" not in environment:
        dev_email = environment.get("TRACECAT__DEV_USER_EMAIL")
        if dev_email:
            environment["TRACECAT_LOADTEST_EMAIL"] = dev_email
    return environment


def _write_case_environment(path: Path, case: LoadTestCase) -> None:
    """Write a data-only env file consumed safely by the cluster wrapper."""
    lines = [f"{name}={value}" for name, value in case.environment]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _case_process_environment(
    base_environment: dict[str, str],
    case: LoadTestCase,
    *,
    env_file: Path,
    monitor_dsn: str | None,
) -> dict[str, str]:
    environment = dict(base_environment)
    environment.update(case.process_environment())
    environment[LOADTEST_ENV_FILE_VARIABLE] = str(env_file)
    if monitor_dsn is not None:
        environment[LOADTEST_MONITOR_DSN_VARIABLE] = monitor_dsn
    return environment


def _cluster_flags(options: MatrixOptions) -> list[str]:
    flags = [
        "--ee-multi-tenant",
        "true" if options.ee_multi_tenant else "false",
        "--sandbox" if options.sandbox else "--no-sandbox",
        "--loadtest",
    ]
    if options.pgdog:
        flags.extend(("--compose-override", str(PGDOG_COMPOSE_FILE)))
    return flags


def _cluster_command(
    options: MatrixOptions,
    command: str,
    *args: str,
    cluster_num: int | None = None,
) -> list[str]:
    result = [str(CLUSTER_SCRIPT)]
    if cluster_num is not None:
        result.append(str(cluster_num))
    result.extend(_cluster_flags(options))
    result.append(command)
    result.extend(args)
    return result


def _executor_replicas(environment: dict[str, str]) -> int:
    """Return the already-validated executor replica count defensively."""
    raw_value = environment.get(LOADTEST_EXECUTOR_REPLICAS_VARIABLE, "1")
    try:
        replicas = int(raw_value)
    except ValueError as exc:
        raise MatrixExecutionError(
            f"{LOADTEST_EXECUTOR_REPLICAS_VARIABLE} must be an integer"
        ) from exc
    if not 1 <= replicas <= MAX_LOADTEST_EXECUTOR_REPLICAS:
        raise MatrixExecutionError(
            f"{LOADTEST_EXECUTOR_REPLICAS_VARIABLE} must be between 1 and "
            f"{MAX_LOADTEST_EXECUTOR_REPLICAS}"
        )
    return replicas


def _display_command_failure(
    label: str,
    result: subprocess.CompletedProcess[str],
) -> MatrixExecutionError:
    stderr = (result.stderr or "").strip()
    detail = f": {stderr}" if stderr else ""
    return MatrixExecutionError(
        f"{label} failed with exit code {result.returncode}{detail}"
    )


def _create_matrix_log_context(artifact_root: Path) -> MatrixLogContext:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = artifact_root / f"matrix-{timestamp}-{secrets.token_hex(3)}"
    orchestration_log = directory / "orchestration.log"
    try:
        directory.mkdir(parents=True)
        orchestration_log.touch(exist_ok=False)
    except OSError as exc:
        raise MatrixExecutionError(
            f"could not create matrix log directory {directory}: {exc}"
        ) from exc
    return MatrixLogContext(
        directory=directory,
        orchestration_log=orchestration_log,
    )


def _write_command_log_header(
    handle: TextIO,
    *,
    label: str,
    args: list[str],
) -> None:
    timestamp = datetime.now(UTC).isoformat()
    handle.write(f"\n[{timestamp}] {label}\n$ {shlex.join(args)}\n")
    handle.flush()


def _read_log_tail(
    log_path: Path,
    *,
    line_count: int = FAILURE_LOG_TAIL_LINES,
) -> tuple[str, ...]:
    try:
        with log_path.open(encoding="utf-8", errors="replace") as handle:
            return tuple(
                line.rstrip("\r\n") for line in deque(handle, maxlen=line_count)
            )
    except OSError:
        return ()


def _logged_process_failure(
    label: str,
    returncode: int,
    log_path: Path,
) -> MatrixExecutionError:
    return _logged_failure(
        f"{label} failed with exit code {returncode}",
        log_path,
    )


def _logged_failure(
    message: str,
    log_path: Path,
) -> MatrixExecutionError:
    lines = [
        message,
        f"Full output: {log_path}",
    ]
    tail = _read_log_tail(log_path)
    if tail:
        lines.append("Recent output:")
        lines.extend(f"  {line}" for line in tail)
    return MatrixExecutionError("\n".join(lines))


def _process_start_failure(
    label: str,
    log_path: Path,
    exc: OSError,
) -> MatrixExecutionError:
    return MatrixExecutionError(
        f"{label} could not start: {exc}\nFull output: {log_path}"
    )


def _selected_cluster_number(log_path: Path, *, start_offset: int) -> int | None:
    """Read the selected cluster number from one logged startup invocation."""
    try:
        with log_path.open(encoding="utf-8", errors="replace") as handle:
            handle.seek(start_offset)
            for line in handle:
                if match := CLUSTER_NUMBER_RE.match(line):
                    return int(match.group(1))
    except OSError:
        return None
    return None


def _possibly_running_cluster_detail(cluster_num: int | None) -> str:
    if cluster_num is None:
        return ""
    return (
        f"\nCluster {cluster_num} may remain running; stop it with: "
        f"just cluster {cluster_num} down"
    )


def _run_capture(
    args: list[str],
    *,
    env: dict[str, str],
    label: str,
) -> str:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _display_command_failure(label, result)
    return result.stdout


def _run_visible(
    args: list[str],
    *,
    env: dict[str, str],
    label: str,
) -> None:
    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise _display_command_failure(label, result)


def _run_logged(
    args: list[str],
    *,
    env: dict[str, str],
    label: str,
    log_path: Path,
) -> None:
    try:
        output = log_path.open("a", encoding="utf-8")
    except OSError as exc:
        raise MatrixExecutionError(
            f"could not open process log {log_path}: {exc}"
        ) from exc

    with output:
        _write_command_log_header(output, label=label, args=args)
        try:
            result = subprocess.run(
                args,
                cwd=REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise _process_start_failure(label, log_path, exc) from exc
    if result.returncode != 0:
        raise _logged_process_failure(label, result.returncode, log_path)


def _start_new_cluster(
    options: MatrixOptions,
    *,
    env: dict[str, str],
    log_path: Path,
) -> int:
    args = _cluster_command(
        options,
        "up",
        "-d",
        "--new",
        "--scale",
        f"executor={_executor_replicas(env)}",
    )
    try:
        output = log_path.open("a", encoding="utf-8")
    except OSError as exc:
        raise MatrixExecutionError(
            f"could not open process log {log_path}: {exc}"
        ) from exc

    with output:
        _write_command_log_header(output, label="cluster startup", args=args)
        start_offset = output.tell()
        try:
            process = subprocess.Popen(
                args,
                cwd=REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            raise _process_start_failure("cluster startup", log_path, exc) from exc

        try:
            returncode = process.wait(timeout=options.startup_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _interrupt_process(process)
            output.flush()
            cluster_num = _selected_cluster_number(
                log_path,
                start_offset=start_offset,
            )
            error = _logged_failure(
                "cluster startup timed out after "
                f"{options.startup_timeout_seconds:g} seconds",
                log_path,
            )
            raise MatrixExecutionError(
                f"{error}{_possibly_running_cluster_detail(cluster_num)}"
            ) from exc
        except KeyboardInterrupt:
            _interrupt_process(process)
            output.flush()
            cluster_num = _selected_cluster_number(
                log_path,
                start_offset=start_offset,
            )
            if cluster_num is not None:
                print(
                    f"\nCluster {cluster_num} may remain running; stop it with: "
                    f"just cluster {cluster_num} down",
                    file=sys.stderr,
                )
            raise
        except OSError as exc:
            _interrupt_process(process)
            raise MatrixExecutionError(
                f"cluster startup output could not be written to {log_path}: {exc}"
            ) from exc
        except BaseException:
            _interrupt_process(process)
            raise

    cluster_num = _selected_cluster_number(log_path, start_offset=start_offset)
    if returncode != 0:
        error = _logged_process_failure("cluster startup", returncode, log_path)
        raise MatrixExecutionError(
            f"{error}{_possibly_running_cluster_detail(cluster_num)}"
        )
    if cluster_num is None:
        raise MatrixExecutionError(
            "cluster startup succeeded but did not report its selected number\n"
            f"Full output: {log_path}"
        )
    return cluster_num


def _reconfigure_cluster(
    options: MatrixOptions,
    *,
    cluster_num: int,
    env: dict[str, str],
    log_path: Path,
) -> None:
    _run_logged(
        _cluster_command(
            options,
            "up",
            "-d",
            "--no-seed",
            "--skip-dependency-sync",
            "--scale",
            f"executor={_executor_replicas(env)}",
            cluster_num=cluster_num,
        ),
        env=env,
        label="cluster reconfiguration",
        log_path=log_path,
    )


def _parse_ports(output: str) -> ClusterPorts:
    values: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(
            r"^\s*(API|PostgreSQL|Temporal|Worker metrics|Executor metrics|"
            r"API DB pool metrics|Worker DB pool metrics|"
            r"Executor DB pool metrics|PgDog metrics):"
            r"\s+(\S+)",
            line,
        )
        if match:
            values[match.group(1)] = match.group(2)
    missing = {
        "API",
        "PostgreSQL",
        "Temporal",
        "Worker metrics",
        "Executor metrics",
        "API DB pool metrics",
        "Worker DB pool metrics",
        "Executor DB pool metrics",
        "PgDog metrics",
    } - values.keys()
    if missing:
        raise MatrixExecutionError(
            "cluster ports output is missing: " + ", ".join(sorted(missing))
        )
    return ClusterPorts(
        public_api_url=values["API"],
        postgres_target=values["PostgreSQL"],
        temporal_target=values["Temporal"],
        temporal_worker_metrics_url=values["Worker metrics"],
        temporal_executor_metrics_url=values["Executor metrics"],
        temporal_executor_metrics_urls=(values["Executor metrics"],),
        api_db_pool_metrics_url=values["API DB pool metrics"],
        worker_db_pool_metrics_url=values["Worker DB pool metrics"],
        executor_db_pool_metrics_url=values["Executor DB pool metrics"],
        executor_db_pool_metrics_urls=(values["Executor DB pool metrics"],),
        pgdog_metrics_url=values["PgDog metrics"],
    )


def _parse_compose_port(
    output: str,
    *,
    replica_index: int,
    path: str,
) -> str:
    """Convert `docker compose port` output into one local metrics URL."""
    lines = tuple(line.strip() for line in output.splitlines() if line.strip())
    if len(lines) != 1:
        raise MatrixExecutionError(
            f"executor replica {replica_index} reported an invalid metrics mapping"
        )
    _host, separator, raw_port = lines[0].rpartition(":")
    if not separator or not raw_port.isdigit():
        raise MatrixExecutionError(
            f"executor replica {replica_index} reported an invalid metrics mapping"
        )
    return f"http://localhost:{int(raw_port)}{path}"


def _resolve_executor_metrics_urls(
    options: MatrixOptions,
    *,
    cluster_num: int,
    env: dict[str, str],
) -> tuple[str, ...]:
    replicas = _executor_replicas(env)
    urls = tuple(
        _parse_compose_port(
            _run_capture(
                _cluster_command(
                    options,
                    "port",
                    "--index",
                    str(replica_index),
                    "executor",
                    "9090",
                    cluster_num=cluster_num,
                ),
                env=env,
                label=f"executor replica {replica_index} metrics lookup",
            ),
            replica_index=replica_index,
            path="/metrics",
        )
        for replica_index in range(1, replicas + 1)
    )
    if len(set(urls)) != replicas:
        raise MatrixExecutionError(
            "scaled executors did not receive unique metrics port mappings"
        )
    return urls


def _resolve_executor_db_pool_metrics_urls(
    options: MatrixOptions,
    *,
    cluster_num: int,
    env: dict[str, str],
) -> tuple[str, ...]:
    replicas = _executor_replicas(env)
    urls = tuple(
        _parse_compose_port(
            _run_capture(
                _cluster_command(
                    options,
                    "port",
                    "--index",
                    str(replica_index),
                    "executor",
                    "9091",
                    cluster_num=cluster_num,
                ),
                env=env,
                label=f"executor replica {replica_index} DB metrics lookup",
            ),
            replica_index=replica_index,
            path="/db-pool-metrics",
        )
        for replica_index in range(1, replicas + 1)
    )
    if len(set(urls)) != replicas:
        raise MatrixExecutionError(
            "scaled executors did not receive unique DB metrics port mappings"
        )
    return urls


def _parse_compose_files(output: str) -> tuple[str, ...]:
    lines = output.splitlines()
    try:
        start = lines.index("Compose files:") + 1
    except ValueError as exc:
        raise MatrixExecutionError(
            "cluster wrapper did not report Compose files"
        ) from exc
    files = tuple(line.strip() for line in lines[start:] if line.startswith("  "))
    if not files:
        raise MatrixExecutionError("cluster wrapper reported no Compose files")
    return files


def _service_environment(
    compose_config: dict[str, object],
    service_name: str,
) -> dict[str, str]:
    services = compose_config.get("services")
    if not isinstance(services, dict):
        raise MatrixExecutionError("Compose config has no services mapping")
    service = services.get(service_name)
    if not isinstance(service, dict):
        raise MatrixExecutionError(f"Compose config has no {service_name!r} service")
    environment = service.get("environment")
    if not isinstance(environment, dict):
        raise MatrixExecutionError(
            f"Compose service {service_name!r} has no environment mapping"
        )
    return {
        str(key): str(value) for key, value in environment.items() if value is not None
    }


def _resolve_deployment_context(
    options: MatrixOptions,
    *,
    cluster_num: int,
    env: dict[str, str],
) -> tuple[ClusterPorts, DeploymentContext]:
    ports = _parse_ports(
        _run_capture(
            [str(CLUSTER_SCRIPT), str(cluster_num), "ports"],
            env=env,
            label="cluster ports lookup",
        )
    )
    executor_metrics_urls = _resolve_executor_metrics_urls(
        options,
        cluster_num=cluster_num,
        env=env,
    )
    executor_db_pool_metrics_urls = _resolve_executor_db_pool_metrics_urls(
        options,
        cluster_num=cluster_num,
        env=env,
    )
    ports = ClusterPorts(
        public_api_url=ports.public_api_url,
        postgres_target=ports.postgres_target,
        temporal_target=ports.temporal_target,
        temporal_worker_metrics_url=ports.temporal_worker_metrics_url,
        temporal_executor_metrics_url=executor_metrics_urls[0],
        temporal_executor_metrics_urls=executor_metrics_urls,
        api_db_pool_metrics_url=ports.api_db_pool_metrics_url,
        worker_db_pool_metrics_url=ports.worker_db_pool_metrics_url,
        executor_db_pool_metrics_url=executor_db_pool_metrics_urls[0],
        executor_db_pool_metrics_urls=executor_db_pool_metrics_urls,
        pgdog_metrics_url=ports.pgdog_metrics_url,
    )
    compose_files = _parse_compose_files(
        _run_capture(
            _cluster_command(
                options,
                "compose-files",
                cluster_num=cluster_num,
            ),
            env=env,
            label="Compose file lookup",
        )
    )
    rendered = _run_capture(
        _cluster_command(options, "config", cluster_num=cluster_num),
        env=env,
        label="Compose config rendering",
    )
    loaded: object = yaml.safe_load(rendered)
    if not isinstance(loaded, dict):
        raise MatrixExecutionError("Compose config did not render a mapping")
    worker_environment = _service_environment(loaded, "worker")
    executor_environment = _service_environment(loaded, "executor")
    postgres_environment = _service_environment(loaded, "postgres_db")

    required_values = {
        "TEMPORAL__CLUSTER_NAMESPACE": worker_environment.get(
            "TEMPORAL__CLUSTER_NAMESPACE"
        ),
        "TEMPORAL__CLUSTER_QUEUE": worker_environment.get("TEMPORAL__CLUSTER_QUEUE"),
        "TRACECAT__EXECUTOR_QUEUE": executor_environment.get(
            "TRACECAT__EXECUTOR_QUEUE"
        ),
        "POSTGRES_USER": postgres_environment.get("POSTGRES_USER"),
        "POSTGRES_PASSWORD": postgres_environment.get("POSTGRES_PASSWORD"),
    }
    missing = sorted(name for name, value in required_values.items() if not value)
    if missing:
        raise MatrixExecutionError(
            "Compose config is missing required values: " + ", ".join(missing)
        )

    return ports, DeploymentContext(
        compose_files=compose_files,
        temporal_namespace=required_values["TEMPORAL__CLUSTER_NAMESPACE"] or "",
        temporal_workflow_queue=required_values["TEMPORAL__CLUSTER_QUEUE"] or "",
        temporal_executor_queue=required_values["TRACECAT__EXECUTOR_QUEUE"] or "",
        postgres_user=required_values["POSTGRES_USER"] or "",
        postgres_password=required_values["POSTGRES_PASSWORD"] or "",
    )


def _wait_for_api(
    public_api_url: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    health_url = f"{public_api_url.rstrip('/')}/health"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(health_url, timeout=2.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    raise MatrixExecutionError(
        f"API did not become healthy within {timeout_seconds:g} seconds"
    )


def _bootstrap_workspace(
    options: MatrixOptions,
    *,
    public_api_url: str,
    env: dict[str, str],
) -> str:
    output = _run_capture(
        [
            sys.executable,
            "-m",
            "tracecat_benchmark.runner",
            "--base-url",
            public_api_url,
            "--workspace-name",
            options.workspace_name,
            "--bootstrap-workspace",
        ],
        env=env,
        label="workspace bootstrap",
    )
    workspace_id = output.strip()
    try:
        UUID(workspace_id)
    except ValueError as exc:
        raise MatrixExecutionError(
            "workspace bootstrap did not return a valid workspace ID"
        ) from exc
    return workspace_id


def _provision_monitor(
    *,
    workspace_id: str,
    ports: ClusterPorts,
    deployment: DeploymentContext,
    env: dict[str, str],
) -> str:
    host, separator, port = ports.postgres_target.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise MatrixExecutionError("PostgreSQL target must use the host:port form")
    user = quote(deployment.postgres_user, safe="")
    password = quote(deployment.postgres_password, safe="")
    provision_env = dict(env)
    provision_env[LOADTEST_PROVISION_DSN_VARIABLE] = (
        f"postgresql://{user}:{password}@{host}:{port}/postgres"
    )
    output = _run_capture(
        [
            sys.executable,
            "-m",
            "tracecat_benchmark.provision_monitor",
            "--workspace-id",
            workspace_id,
        ],
        env=provision_env,
        label="monitor provisioning",
    )
    monitor_dsn = output.strip()
    if not monitor_dsn.startswith(("postgresql://", "postgres://")):
        raise MatrixExecutionError(
            "monitor provisioning did not return a PostgreSQL DSN"
        )
    return monitor_dsn


def _reset_fixture_table(
    *,
    workspace_id: str,
    public_api_url: str,
    env: dict[str, str],
) -> None:
    _run_visible(
        [
            sys.executable,
            "-m",
            "tracecat_benchmark.runner",
            "--base-url",
            public_api_url,
            "--workspace-id",
            workspace_id,
            "--reset-fixture-table",
        ],
        env=env,
        label="fixture reset",
    )


def _collector_command(
    case: LoadTestCase,
    options: MatrixOptions,
    *,
    run_id: str,
    workspace_id: str,
    cluster_num: int,
    ports: ClusterPorts,
    deployment: DeploymentContext,
    activity_metrics_handoff: Path,
) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "tracecat_benchmark.collector",
        "--run-id",
        run_id,
        "--case-id",
        case.case_id,
        "--workspace-id",
        workspace_id,
        "--artifact-root",
        str(options.artifact_root),
        "--sample-interval-seconds",
        str(case.sample_interval_seconds),
        "--readiness-timeout-seconds",
        str(case.collector_ready_timeout_seconds),
        "--cluster-num",
        str(cluster_num),
        "--public-api-url",
        ports.public_api_url,
        "--ee-multi-tenant",
        "true" if options.ee_multi_tenant else "false",
        "--temporal-target",
        ports.temporal_target,
        "--temporal-namespace",
        deployment.temporal_namespace,
        "--temporal-workflow-task-queue",
        deployment.temporal_workflow_queue,
        "--temporal-activity-task-queue",
        deployment.temporal_workflow_queue,
        "--temporal-activity-task-queue",
        deployment.temporal_executor_queue,
        "--temporal-executor-task-queue",
        deployment.temporal_executor_queue,
        "--activity-metrics-handoff",
        str(activity_metrics_handoff),
        "--temporal-worker-metrics-url",
        ports.temporal_worker_metrics_url,
        "--api-db-pool-metrics-url",
        ports.api_db_pool_metrics_url,
        "--worker-db-pool-metrics-url",
        ports.worker_db_pool_metrics_url,
        "--recovery-seconds",
        str(case.recovery_seconds),
    ]
    executor_metrics_urls = ports.temporal_executor_metrics_urls or (
        ports.temporal_executor_metrics_url,
    )
    for metrics_url in executor_metrics_urls:
        args.extend(("--temporal-executor-metrics-url", metrics_url))
    executor_db_metrics_urls = ports.executor_db_pool_metrics_urls or (
        ports.executor_db_pool_metrics_url,
    )
    for metrics_url in executor_db_metrics_urls:
        args.extend(("--executor-db-pool-metrics-url", metrics_url))
    if options.pgdog:
        args.extend(("--pgdog-metrics-url", ports.pgdog_metrics_url))
    for compose_file in deployment.compose_files:
        args.extend(("--compose-file", compose_file))
    return args


def _runner_command(
    case: LoadTestCase,
    options: MatrixOptions,
    *,
    run_id: str,
    workspace_id: str,
    cluster_num: int,
    ports: ClusterPorts,
    activity_metrics_handoff: Path,
) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "tracecat_benchmark.runner",
        "--base-url",
        ports.public_api_url,
        "--cluster-num",
        str(cluster_num),
        "--workspace-id",
        workspace_id,
        "--workspace-name",
        options.workspace_name,
        "--run-id",
        run_id,
        "--case-id",
        case.case_id,
        "--artifact-root",
        str(options.artifact_root),
        "--activity-metrics-handoff",
        str(activity_metrics_handoff),
        "--load-type",
        case.load_type.value,
        "--workflow-count",
        str(case.workflow_count),
        "--branch-count",
        str(case.branch_count),
        "--ramp-seconds",
        str(case.ramp_seconds),
        "--steady-state-seconds",
        str(case.steady_state_seconds),
        "--payload-bytes",
        str(case.payload_bytes),
        "--run-timeout-seconds",
        str(case.run_timeout_seconds),
        "--poll-interval-seconds",
        str(case.poll_interval_seconds),
        "--collector-ready-timeout-seconds",
        str(case.collector_ready_timeout_seconds),
        "--max-connections",
        str(case.max_connections),
    ]
    if not case.warmup:
        args.append("--no-warmup")
    if case.one_shot:
        args.append("--one-shot")
    if case.abort_stops_polling:
        args.append("--abort-stops-polling")
    return args


def _interrupt_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _wait_for_collector_readiness(
    collector: subprocess.Popen[str],
    *,
    artifact_dir: Path,
    timeout_seconds: float,
    collector_log: Path,
) -> None:
    """Wait until collector preflight and initial sampling have completed."""
    ready_path = artifact_dir / "collector_ready.json"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if ready_path.is_file():
            return
        returncode = collector.poll()
        if returncode is not None:
            raise _logged_process_failure(
                "collector before becoming ready",
                returncode,
                collector_log,
            )
        time.sleep(0.1)

    _interrupt_process(collector)
    raise _logged_failure(
        f"collector was not ready within {timeout_seconds:g} seconds",
        collector_log,
    )


def _run_cell(
    case: LoadTestCase,
    options: MatrixOptions,
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
    try:
        process_log_dir.mkdir(parents=True)
        with contextlib.ExitStack() as stack:
            collector_log = process_log_dir / "collector.log"
            runner_log = process_log_dir / "runner.log"
            collector_output = stack.enter_context(
                collector_log.open("x", encoding="utf-8")
            )
            runner_output = stack.enter_context(runner_log.open("x", encoding="utf-8"))
            try:
                collector = subprocess.Popen(
                    _collector_command(
                        case,
                        options,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        cluster_num=cluster_num,
                        ports=ports,
                        deployment=deployment,
                        activity_metrics_handoff=activity_metrics_handoff,
                    ),
                    cwd=REPO_ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=collector_output,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except OSError as exc:
                raise _process_start_failure("collector", collector_log, exc) from exc

            runner: subprocess.Popen[str] | None = None
            try:
                _wait_for_collector_readiness(
                    collector,
                    artifact_dir=(options.artifact_root / run_id_fingerprint(run_id)),
                    timeout_seconds=case.collector_ready_timeout_seconds,
                    collector_log=collector_log,
                )
                print("Collector ready; starting load...", flush=True)
                try:
                    runner = subprocess.Popen(
                        _runner_command(
                            case,
                            options,
                            run_id=run_id,
                            workspace_id=workspace_id,
                            cluster_num=cluster_num,
                            ports=ports,
                            activity_metrics_handoff=activity_metrics_handoff,
                        ),
                        cwd=REPO_ROOT,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=runner_output,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                except OSError as exc:
                    raise _process_start_failure(
                        "load runner", runner_log, exc
                    ) from exc

                while True:
                    runner_code = runner.poll()
                    collector_code = collector.poll()
                    if collector_code is not None and runner_code is None:
                        _interrupt_process(runner)
                        raise _logged_process_failure(
                            "collector before the runner completed",
                            collector_code,
                            collector_log,
                        )
                    if runner_code is not None:
                        break
                    time.sleep(0.2)

                if runner_code != 0:
                    _interrupt_process(collector)
                    raise _logged_process_failure(
                        "load runner",
                        runner_code,
                        runner_log,
                    )
                print(
                    "Load generator completed; waiting for collector recovery "
                    "samples...",
                    flush=True,
                )
                collector_code = collector.wait()
                if collector_code != 0:
                    raise _logged_process_failure(
                        "collector",
                        collector_code,
                        collector_log,
                    )
            except BaseException:
                if runner is not None:
                    _interrupt_process(runner)
                _interrupt_process(collector)
                raise
    except FileExistsError as exc:
        raise MatrixExecutionError(
            f"refusing to overwrite existing process logs: {process_log_dir}"
        ) from exc
    except OSError as exc:
        raise MatrixExecutionError(
            f"could not create process logs under {process_log_dir}: {exc}"
        ) from exc


def _print_run_summary(artifact_dir: Path) -> None:
    summary_path = artifact_dir / "summary.txt"
    try:
        summary = summary_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return
    except OSError as exc:
        print(
            f"Run completed, but its summary could not be read: {exc}",
            file=sys.stderr,
        )
        return
    if summary:
        print(f"\n{summary}")


def _format_history_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 1:
        return f"{seconds * 1_000:,.0f} ms"
    return f"{seconds:,.2f} s"


def _format_sdk_duration(milliseconds: float | None) -> str:
    if milliseconds is None:
        return "—"
    if milliseconds < 1_000:
        return f"≤ {milliseconds:,.0f} ms"
    return f"≤ {milliseconds / 1_000:,.2f} s"


def _short_fingerprint(value: str | None) -> str:
    if value is None:
        return "—"
    if value.startswith("sha256:") and len(value) > 19:
        return f"{value[:19]}…"
    return value


def _load_metrics_artifact(path: Path, *, label: str) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"Run completed, but its {label} could not be read: {exc}",
            file=sys.stderr,
        )
        return None


def _print_activity_summary(artifact_dir: Path) -> None:
    history_payload = _load_metrics_artifact(
        artifact_dir / "activity_metrics.json",
        label="activity metrics",
    )
    if not isinstance(history_payload, dict):
        return
    history = cast(ActivityHistoryMetrics, history_payload)
    groups = history.get("groups")
    if not isinstance(groups, list):
        print(
            "Run completed, but its activity metrics have an invalid shape.",
            file=sys.stderr,
        )
        return

    console = Console(highlight=False)
    console.print()
    throughput = Text()
    throughput.append("Completed throughput · ", style="bold")
    throughput.append(
        f"decoded Tracecat actions {history['completed_tracecat_actions']:,} "
        f"({history['completed_tracecat_actions_per_second']:,.2f}/s)"
    )
    throughput.append(" · ")
    throughput.append(
        f"all Temporal activities {history['completed_activities']:,} "
        f"({history['completed_activities_per_second']:,.2f}/s)"
    )
    console.print(throughput)

    table = Table(
        title="Per-activity and per-action results · measured interval",
        title_justify="left",
        header_style="bold cyan",
    )
    table.add_column("Tracecat action / Temporal activity", overflow="fold", ratio=3)
    table.add_column("Outcomes", overflow="fold", ratio=2)
    table.add_column("Completed/s", justify="right", no_wrap=True)
    table.add_column("Successful latency p95", overflow="fold", ratio=2)
    for group in groups:
        action_label = group["action_name"] or (
            "— (action decode failed)"
            if group["input_decode_failures"]
            else "— (internal activity)"
        )
        identity = Text(action_label)
        identity.append(
            f"\n{group['activity_type']} · {group['queue_role']} queue",
            style="dim",
        )
        outcomes = Text(
            f"Completed {group['completed']:,} · open {group['open']:,}\n"
            f"Failed {group['failed']:,} · timed out {group['timed_out']:,} · "
            f"canceled {group['canceled']:,}\n"
            f"Retries {group['retries']:,}"
        )
        latency = Text(
            "Schedule → start  "
            f"{_format_history_duration(group['schedule_to_start']['p95_seconds'])}\n"
            "Start → close  "
            f"{_format_history_duration(group['start_to_close']['p95_seconds'])}\n"
            "Schedule → close  "
            f"{_format_history_duration(group['schedule_to_close']['p95_seconds'])}"
        )
        table.add_row(
            identity,
            outcomes,
            f"{group['completed_per_second']:,.2f}",
            latency,
        )
    console.print(table)
    console.print(
        "History latency covers successful completions. Schedule → start "
        "excludes retried completions; "
        "schedule → close includes queueing and retry backoff.",
        style="dim",
    )
    action_decode_failures = sum(group["input_decode_failures"] for group in groups)
    if action_decode_failures:
        console.print(
            f"{action_decode_failures:,} execute_action_activity input(s) could "
            "not be decoded; decoded Actions/s excludes them.",
            style="bold yellow",
        )

    sdk_payload = _load_metrics_artifact(
        artifact_dir / "temporal_sdk_metrics.json",
        label="Temporal SDK metrics",
    )
    if not isinstance(sdk_payload, dict):
        return
    sdk_metrics = cast(TemporalSdkMetrics, sdk_payload)
    histogram_rows = sdk_metrics.get("histograms")
    if not isinstance(histogram_rows, list):
        return
    histograms = [histogram for histogram in histogram_rows if histogram["count"] > 0]
    if not histograms:
        return

    metric_labels = {
        "temporal_activity_schedule_to_start_latency": "Schedule → start",
        "temporal_activity_execution_latency": "Start → close",
        "temporal_activity_succeed_endtoend_latency": "Schedule → close (success)",
    }
    sdk_table = Table(
        title="Temporal SDK timing · retry-aware worker/queue view",
        title_justify="left",
        header_style="bold magenta",
    )
    sdk_table.add_column("Service / activity / queue", overflow="fold", ratio=3)
    sdk_table.add_column("Timing", overflow="fold", ratio=2)
    sdk_table.add_column("Observations", justify="right", overflow="fold")
    sdk_table.add_column("p95 bucket upper bound", justify="right")
    for histogram in histograms:
        labels = histogram["labels"]
        source = Text(histogram["service"])
        source.append(f"\n{labels.get('activity_type', '—')}", style="dim")
        source.append(
            f"\nqueue {_short_fingerprint(labels.get('task_queue'))}",
            style="dim",
        )
        sdk_table.add_row(
            source,
            metric_labels.get(histogram["metric"], histogram["metric"]),
            (f"{histogram['count']:,.0f}\n{histogram['rate_per_second']:,.2f}/s"),
            _format_sdk_duration(histogram["p95_upper_bound_milliseconds"]),
        )
    console.print(sdk_table)
    console.print(
        "SDK timing includes retry-aware queue behavior, but it cannot be "
        "attributed to an individual Tracecat action name.",
        style="dim",
    )
    if any(histogram["counter_reset_detected"] for histogram in histograms):
        console.print(
            "A Temporal SDK process-local histogram reset during measurement; "
            "its delta contains only the visible post-reset observations.",
            style="bold yellow",
        )


def _down_cluster(
    options: MatrixOptions,
    *,
    cluster_num: int,
    env: dict[str, str],
    log_path: Path,
) -> None:
    _run_logged(
        _cluster_command(options, "down", cluster_num=cluster_num),
        env=env,
        label="cluster shutdown",
        log_path=log_path,
    )


def _new_run_id(case: LoadTestCase, repeat: int) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    nonce = secrets.token_hex(3)
    return f"{case.case_id}-r{repeat}-{timestamp}-{nonce}"


def _print_plan(
    all_cases: tuple[LoadTestCase, ...],
    selected_cases: tuple[LoadTestCase, ...],
    options: MatrixOptions,
) -> None:
    console = Console(highlight=False)
    selected_case_ids = {case.case_id for case in selected_cases}
    matrix_table = Table(
        title=(
            f"Benchmark matrix · {len(selected_cases)} will run / {len(all_cases)} rows"
        ),
        title_justify="left",
        header_style="bold cyan",
    )
    matrix_table.add_column("State", no_wrap=True)
    matrix_table.add_column("Case × runs", no_wrap=True)
    matrix_table.add_column("Load", no_wrap=True)
    matrix_table.add_column("Workload", justify="right", no_wrap=True)
    matrix_table.add_column("Timing", justify="right", no_wrap=True)
    matrix_table.add_column("Pattern", no_wrap=True)

    for case in all_cases:
        if case.case_id in selected_case_ids:
            state = Text("RUN", style="bold green")
        elif case.enabled:
            state = Text("FILTERED", style="yellow")
        else:
            state = Text("DISABLED", style="dim")
        matrix_table.add_row(
            state,
            Text(f"{case.case_id} × {case.repeats}"),
            Text(case.load_type.value),
            f"{case.workflow_count} × {case.branch_count}",
            f"{case.ramp_seconds:g}s / {case.steady_state_seconds:g}s",
            "one-shot" if case.one_shot else "sustained",
            style=None if case.case_id in selected_case_ids else "dim",
        )

    try:
        displayed_matrix_path = options.matrix_path.relative_to(REPO_ROOT)
    except ValueError:
        displayed_matrix_path = options.matrix_path
    console.print()
    console.print("Matrix:", displayed_matrix_path)
    console.print("[dim]Workload = workflows × branches; timing = ramp / sustain.[/]")
    if options.pgdog:
        console.print(
            "[bold cyan]Database paths:[/] API → PostgreSQL; worker services → "
            "PgDog transaction pool → PostgreSQL"
        )
    else:
        console.print("[bold cyan]Database path:[/] Tracecat → PostgreSQL")
    console.print(matrix_table)

    override_count = sum(len(case.environment_overrides) for case in selected_cases)
    if override_count:
        console.print("[dim]Resource names omit the TRACECAT__LOADTEST_ prefix.[/]")
        overrides_table = Table(
            title=f"Selected resource overrides · {override_count}",
            title_justify="left",
            header_style="bold cyan",
            min_width=48,
        )
        overrides_table.add_column("Case", no_wrap=True)
        overrides_table.add_column("Resource")
        overrides_table.add_column("Value", no_wrap=True)
        for case in selected_cases:
            for name, value in case.environment_overrides:
                overrides_table.add_row(
                    Text(case.case_id),
                    Text(name.removeprefix("TRACECAT__LOADTEST_")),
                    Text(value),
                )
        console.print(overrides_table)
    else:
        console.print(
            "[dim]No selected resource overrides; using the checked-in baseline.[/]"
        )


def execute_matrix(
    cases: tuple[LoadTestCase, ...],
    options: MatrixOptions,
) -> int:
    """Execute selected rows sequentially on one isolated cluster."""
    base_environment = _load_base_process_environment(REPO_ROOT)
    cluster_num: int | None = None
    workspace_id: str | None = None
    monitor_dsn: str | None = None
    prior_run_completed = False
    final_environment = dict(base_environment)
    log_context: MatrixLogContext | None = None

    try:
        log_context = _create_matrix_log_context(options.artifact_root)
        print(f"\nMatrix logs: {log_context.directory}")
        with tempfile.TemporaryDirectory(prefix="tracecat-benchmark-") as temp_dir:
            temp_root = Path(temp_dir)
            for case_index, case in enumerate(cases, start=1):
                print(
                    f"\n=== Matrix case {case_index}/{len(cases)}: {case.case_id} ===",
                    flush=True,
                )
                env_file = temp_root / f"case-{case_index}.env"
                _write_case_environment(env_file, case)
                case_environment = _case_process_environment(
                    base_environment,
                    case,
                    env_file=env_file,
                    monitor_dsn=monitor_dsn,
                )

                if cluster_num is None:
                    print("Starting isolated cluster...", flush=True)
                    cluster_num = _start_new_cluster(
                        options,
                        env=case_environment,
                        log_path=log_context.orchestration_log,
                    )
                    print(f"Cluster {cluster_num} started.", flush=True)
                    # The wrapper creates .env on the first `up` when this is a
                    # new worktree. Reload it before invoking Python clients so
                    # they use the same seeded credentials as the cluster.
                    base_environment = _load_base_process_environment(REPO_ROOT)
                    case_environment = _case_process_environment(
                        base_environment,
                        case,
                        env_file=env_file,
                        monitor_dsn=monitor_dsn,
                    )
                else:
                    print(f"Reconfiguring cluster {cluster_num}...", flush=True)
                    _reconfigure_cluster(
                        options,
                        cluster_num=cluster_num,
                        env=case_environment,
                        log_path=log_context.orchestration_log,
                    )
                final_environment = case_environment

                ports, deployment = _resolve_deployment_context(
                    options,
                    cluster_num=cluster_num,
                    env=case_environment,
                )
                _wait_for_api(
                    ports.public_api_url,
                    timeout_seconds=options.startup_timeout_seconds,
                )

                if workspace_id is None:
                    workspace_id = _bootstrap_workspace(
                        options,
                        public_api_url=ports.public_api_url,
                        env=case_environment,
                    )
                    monitor_dsn = _provision_monitor(
                        workspace_id=workspace_id,
                        ports=ports,
                        deployment=deployment,
                        env=case_environment,
                    )
                    case_environment[LOADTEST_MONITOR_DSN_VARIABLE] = monitor_dsn

                if workspace_id is None or monitor_dsn is None:
                    raise AssertionError("load-test bootstrap state is incomplete")

                for repeat in range(1, case.repeats + 1):
                    if prior_run_completed:
                        _reset_fixture_table(
                            workspace_id=workspace_id,
                            public_api_url=ports.public_api_url,
                            env=case_environment,
                        )
                    run_id = _new_run_id(case, repeat)
                    artifact_dir = options.artifact_root / run_id_fingerprint(run_id)
                    process_log_dir = log_context.process_log_directory(run_id)
                    activity_metrics_handoff = temp_root / (
                        "activity-metrics-"
                        f"{run_id_fingerprint(run_id).removeprefix('sha256:')}.json"
                    )
                    print(f"\n--- {case.case_id} repeat {repeat}/{case.repeats} ---")
                    print(f"Artifacts: {artifact_dir}")
                    print(f"Process logs: {process_log_dir}")
                    print(
                        "Starting collector and waiting for initial samples...",
                        flush=True,
                    )
                    _run_cell(
                        case,
                        options,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        cluster_num=cluster_num,
                        ports=ports,
                        deployment=deployment,
                        env=case_environment,
                        process_log_dir=process_log_dir,
                        activity_metrics_handoff=activity_metrics_handoff,
                    )
                    _print_run_summary(artifact_dir)
                    _print_activity_summary(artifact_dir)
                    print(f"Run completed. Process logs: {process_log_dir}")
                    prior_run_completed = True
    except KeyboardInterrupt:
        print("\nLoad-test matrix interrupted.", file=sys.stderr)
        if log_context is not None:
            print(f"Matrix logs: {log_context.directory}", file=sys.stderr)
        if cluster_num is not None:
            print(
                f"Cluster {cluster_num} remains running for diagnosis.",
                file=sys.stderr,
            )
        return 130
    except MatrixExecutionError as exc:
        print(f"\nLoad-test matrix failed: {exc}", file=sys.stderr)
        if log_context is not None:
            print(f"Matrix logs: {log_context.directory}", file=sys.stderr)
        if cluster_num is not None:
            print(
                f"Cluster {cluster_num} remains running for diagnosis.",
                file=sys.stderr,
            )
            print(
                f"Stop it later with: just cluster {cluster_num} down",
                file=sys.stderr,
            )
        return 1

    if cluster_num is None:
        raise AssertionError("selected matrix produced no cluster")
    if log_context is None:
        raise AssertionError("selected matrix produced no log context")
    final_environment.pop(LOADTEST_ENV_FILE_VARIABLE, None)
    if options.keep_cluster:
        print(
            f"\nMatrix completed; cluster {cluster_num} remains running.\n"
            f"Matrix logs: {log_context.directory}"
        )
    else:
        try:
            print(f"Stopping cluster {cluster_num}...", flush=True)
            _down_cluster(
                options,
                cluster_num=cluster_num,
                env=final_environment,
                log_path=log_context.orchestration_log,
            )
        except KeyboardInterrupt:
            print(
                f"\nShutdown interrupted; cluster {cluster_num} may remain running.",
                file=sys.stderr,
            )
            return 130
        except MatrixExecutionError as exc:
            print(f"\nLoad tests passed, but {exc}.", file=sys.stderr)
            print(
                f"Cluster {cluster_num} may remain running; stop it with: "
                f"just cluster {cluster_num} down",
                file=sys.stderr,
            )
            return 1
        print(
            f"\nMatrix completed; cluster {cluster_num} stopped (volumes retained).\n"
            f"Matrix logs: {log_context.directory}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cluster loadtest",
        description=(
            "Run a CSV load-test matrix on one fresh isolated Tracecat cluster."
        ),
    )
    parser.add_argument(
        "--matrix",
        required=True,
        type=Path,
        help="CSV matrix containing named workload and resource configurations.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        dest="case_ids",
        help=(
            "Run only this case_id. Repeat the option to select multiple cases; "
            "explicit selection may include disabled rows."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the selected matrix without touching Docker.",
    )
    parser.add_argument(
        "--keep-cluster",
        action="store_true",
        help="Leave the isolated cluster running after a successful matrix.",
    )
    parser.add_argument(
        "--pgdog",
        action="store_true",
        help=(
            "Route transaction-friendly Tracecat worker services through the "
            "load-test PgDog transaction pool."
        ),
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
    )
    parser.add_argument("--workspace-name", default=DEFAULT_WORKSPACE_NAME)
    parser.add_argument(
        "--ee-multi-tenant",
        choices=("true", "false"),
        default="true",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Use the direct executor backend instead of the sandbox Compose layer.",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum wait for the API after a matrix row reconfigures services.",
    )
    return parser


def _configuration_error(message: str) -> NoReturn:
    print(f"Invalid load-test matrix: {message}", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if (
        not math.isfinite(args.startup_timeout_seconds)
        or args.startup_timeout_seconds <= 0
    ):
        _configuration_error("--startup-timeout-seconds must be finite and positive")
    if not args.workspace_name.strip():
        _configuration_error("--workspace-name must not be blank")

    options = MatrixOptions(
        matrix_path=args.matrix.resolve(),
        artifact_root=args.artifact_root.resolve(),
        workspace_name=args.workspace_name,
        selected_case_ids=tuple(args.case_ids),
        dry_run=args.dry_run,
        keep_cluster=args.keep_cluster,
        sandbox=not args.no_sandbox,
        ee_multi_tenant=args.ee_multi_tenant == "true",
        pgdog=args.pgdog,
        startup_timeout_seconds=args.startup_timeout_seconds,
    )
    try:
        all_cases = load_matrix(options.matrix_path)
        cases = select_cases(all_cases, options.selected_case_ids)
    except MatrixConfigurationError as exc:
        _configuration_error(str(exc))

    _print_plan(all_cases, cases, options)
    if options.dry_run:
        Console(highlight=False).print(
            "\n[bold green]Dry run complete.[/] Docker was not touched."
        )
        return
    raise SystemExit(execute_matrix(cases, options))


if __name__ == "__main__":
    main()
