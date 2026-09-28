"""Local read-only viewer for workflow load-test artifacts.

Serves a single-page UI plus a small JSON API over an artifact root written by
``tracecat_benchmark.collector`` and ``tracecat_benchmark.runner``:

    uv run --all-packages tracecat-benchmark-viewer

The viewer never writes to the artifact root and binds to localhost by default.
Run directories are addressed only by their ``sha256:<hex>`` fingerprint, which
is validated before any filesystem path is derived from it.

Artifact documents are served through unchanged; their authoritative shapes are
the models in ``tracecat_benchmark.models``. Partial runs are normal here - an
aborted run keeps whatever the collector managed to write - so every artifact
read tolerates a missing or malformed file and yields ``None``.

The resolved Compose model is the one artifact served as text rather than JSON:
it is ~900 lines per run, so it gets its own endpoint instead of riding along in
the run detail payload. The scheduling and concurrency knobs buried in it are
the exception - those are extracted into the run detail payload, because they
are what the benchmark matrix actually varies between runs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, TypedDict, cast
from urllib.parse import unquote, urlsplit

import yaml

from .models import PgActivitySample, ResourceUsageSample, TemporalBacklogSample

DEFAULT_ARTIFACT_ROOT: Final = Path("/tmp/tracecat-load-test")
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8321

RUN_ID_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")
MATRIX_DIR_RE: Final = re.compile(r"matrix-[0-9]{8}T[0-9]{6}Z-[0-9a-f]+")
RUN_DETAIL_ROUTE: Final = re.compile(r"/api/runs/(?P<run_id>[^/]+)")
RUN_TIMESERIES_ROUTE: Final = re.compile(r"/api/runs/(?P<run_id>[^/]+)/timeseries")
RUN_COMPOSE_ROUTE: Final = re.compile(r"/api/runs/(?P<run_id>[^/]+)/compose")

INDEX_HTML_PATH: Final = Path(__file__).with_name("viewer.html")

# Temporal SDK histogram buckets are large and the UI only charts counters.
SDK_METRICS_KEYS: Final = (
    "schema_version",
    "source",
    "generated_at",
    "measurement_window_seconds",
    "duration_unit",
    "counters",
    "notes",
)

# Environment names carrying a scheduling, concurrency, or pool knob. Matched as
# substrings so the same list covers the TEMPORAL__ and TRACECAT__ namespaces
# and any per-service prefix (executor, agent-executor, litellm).
TUNING_ENV_MARKERS: Final = (
    "MAX_CONCURRENT",
    "THREADPOOL",
    "DSL_SCHEDULER",
    "DISPATCH_WINDOW",
    "FOR_EACH",
    "WORKER_POOL",
    "DB_POOL",
    "MAX_OVERFLOW",
    "NUM_WORKERS",
)

# Artifact JSON is pass-through payload: the viewer reads a handful of scalar
# fields for the run list and forwards the rest untouched. A recursive JSON
# alias keeps that forwarding typed without restating every artifact schema.
type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonDocument = dict[str, JsonValue]

# Compose service name -> tuning environment name -> resolved value. A value of
# ``None`` is an explicitly null Compose entry and ``""`` is an unset variable,
# which the backend reads as "use the built-in default"; both are kept as-is.
type ServiceTuning = dict[str, dict[str, str | None]]


class RunListItem(TypedDict):
    """One row of the run list, flattened from several artifacts."""

    run_id: str
    case_id: str | None
    matrix: str | None
    started_at: str | None
    status: str | None
    load_type: str | None
    workflow_count: int | None
    branch_count: int | None
    one_shot: bool | None
    tracecat_commit: str | None
    submitted: int | None
    completed: int | None
    failed: int | None
    throughput_workflows_per_second: float | None
    throughput_actions_per_second: float | None
    latency_p50_seconds: float | None
    latency_p95_seconds: float | None
    latency_p99_seconds: float | None


class RunListPayload(TypedDict):
    """Response body for ``GET /api/runs``."""

    artifact_root: str
    runs: list[RunListItem]
    matrices: list[str]
    load_types: list[str]


class RunDetailPayload(TypedDict):
    """Response body for ``GET /api/runs/{run_id}``."""

    run_id: str
    artifact_root: str
    matrix: str | None
    scenario: JsonDocument | None
    manifest: JsonDocument | None
    summary: JsonDocument | None
    summary_text: str | None
    activity_metrics: JsonDocument | None
    pg_settings: list[JsonValue] | None
    row_correctness: JsonDocument | None
    table_drift: JsonDocument | None
    temporal_sdk_metrics: JsonDocument | None
    containers: list[JsonValue] | None
    service_tuning: ServiceTuning | None


class TimeseriesPayload(TypedDict):
    """Response body for ``GET /api/runs/{run_id}/timeseries``.

    Every series carries the runner/collector shared ``monotonic`` clock, so
    the UI can align all four sources on one x-axis.
    """

    run_id: str
    pg_activity: list[PgActivitySample]
    resource_usage: list[ResourceUsageSample]
    temporal_backlog: list[TemporalBacklogSample]
    executions: list[JsonDocument]


@dataclass(slots=True, frozen=True)
class ArtifactIndex:
    """Run directories under an artifact root and their owning matrix run."""

    root: Path
    run_ids: tuple[str, ...]
    matrix_by_run: dict[str, str]


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path) -> JsonValue | None:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return cast(JsonValue, json.loads(raw))
    except json.JSONDecodeError:
        return None


def _read_json_document(path: Path) -> JsonDocument | None:
    value = _read_json(path)
    return value if isinstance(value, dict) else None


def _read_json_array(path: Path) -> list[JsonValue] | None:
    value = _read_json(path)
    return value if isinstance(value, list) else None


def _read_jsonl(path: Path) -> list[JsonDocument]:
    """Return every well-formed JSON object line, skipping partial writes."""
    records: list[JsonDocument] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = cast(JsonValue, json.loads(stripped))
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
    except (OSError, UnicodeDecodeError):
        return records
    return records


def _get_document(document: JsonDocument | None, key: str) -> JsonDocument | None:
    if document is None:
        return None
    value = document.get(key)
    return value if isinstance(value, dict) else None


def _get_str(document: JsonDocument | None, key: str) -> str | None:
    if document is None:
        return None
    value = document.get(key)
    return value if isinstance(value, str) else None


def _get_int(document: JsonDocument | None, key: str) -> int | None:
    if document is None:
        return None
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _get_float(document: JsonDocument | None, key: str) -> float | None:
    if document is None:
        return None
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _get_bool(document: JsonDocument | None, key: str) -> bool | None:
    if document is None:
        return None
    value = document.get(key)
    return value if isinstance(value, bool) else None


def build_index(root: Path) -> ArtifactIndex:
    """Index run directories and associate each with its matrix directory.

    Matrix directories hold only per-run logs under ``runs/<run_id>``; the run
    artifacts themselves stay at the artifact root, so the matrix subdirectory
    is used purely as the association.
    """
    run_ids: list[str] = []
    matrix_by_run: dict[str, str] = {}
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return ArtifactIndex(root=root, run_ids=(), matrix_by_run={})
    for entry in entries:
        if not entry.is_dir():
            continue
        if RUN_ID_RE.fullmatch(entry.name):
            run_ids.append(entry.name)
            continue
        if not MATRIX_DIR_RE.fullmatch(entry.name):
            continue
        try:
            members = sorted((entry / "runs").iterdir())
        except OSError:
            continue
        for member in members:
            if RUN_ID_RE.fullmatch(member.name) and member.is_dir():
                matrix_by_run[member.name] = entry.name
    return ArtifactIndex(root=root, run_ids=tuple(run_ids), matrix_by_run=matrix_by_run)


def _summary_record(run_dir: Path) -> JsonDocument | None:
    """Return the runner's summary line, which aborted runs never write."""
    for record in reversed(_read_jsonl(run_dir / "runner_results.jsonl")):
        if record.get("kind") == "summary":
            return record
    return None


def _execution_records(run_dir: Path) -> list[JsonDocument]:
    return [
        record
        for record in _read_jsonl(run_dir / "runner_results.jsonl")
        if record.get("kind") == "execution"
    ]


def build_run_list_item(run_dir: Path, run_id: str, matrix: str | None) -> RunListItem:
    """Flatten the artifacts the run list shows into one record."""
    scenario = _read_json_document(run_dir / "scenario.json")
    manifest = _read_json_document(run_dir / "manifest.json")
    summary = _summary_record(run_dir)
    activity_metrics = _read_json_document(run_dir / "activity_metrics.json")
    latency = _get_document(summary, "latency")
    return RunListItem(
        run_id=run_id,
        case_id=_get_str(scenario, "case_id") or _get_str(manifest, "case_id"),
        matrix=matrix,
        started_at=_get_str(manifest, "started_at") or _get_str(scenario, "started_at"),
        status=_get_str(manifest, "status"),
        load_type=_get_str(scenario, "load_type"),
        workflow_count=_get_int(scenario, "workflow_count"),
        branch_count=_get_int(scenario, "branch_count"),
        one_shot=_get_bool(scenario, "one_shot"),
        tracecat_commit=_get_str(manifest, "tracecat_commit")
        or _get_str(scenario, "tracecat_commit"),
        submitted=_get_int(summary, "submitted"),
        completed=_get_int(summary, "completed"),
        failed=_get_int(summary, "failed"),
        throughput_workflows_per_second=_get_float(
            summary, "throughput_workflows_per_second"
        ),
        throughput_actions_per_second=_get_float(
            activity_metrics, "completed_tracecat_actions_per_second"
        ),
        latency_p50_seconds=_get_float(latency, "p50"),
        latency_p95_seconds=_get_float(latency, "p95"),
        latency_p99_seconds=_get_float(latency, "p99"),
    )


def list_runs(root: Path) -> RunListPayload:
    """Return every indexed run, newest first."""
    index = build_index(root)
    runs = [
        build_run_list_item(root / run_id, run_id, index.matrix_by_run.get(run_id))
        for run_id in index.run_ids
    ]
    # Runs with no readable start time sort last but keep a stable order.
    runs.sort(key=lambda run: (run["started_at"] or "", run["run_id"]), reverse=True)
    matrices = sorted({run["matrix"] for run in runs if run["matrix"] is not None})
    load_types = sorted({run["load_type"] for run in runs if run["load_type"]})
    return RunListPayload(
        artifact_root=str(root),
        runs=runs,
        matrices=matrices,
        load_types=load_types,
    )


def _sdk_metrics_counters(run_dir: Path) -> JsonDocument | None:
    document = _read_json_document(run_dir / "temporal_sdk_metrics.json")
    if document is None:
        return None
    return {key: document[key] for key in SDK_METRICS_KEYS if key in document}


def _tuning_environment(environment: object) -> dict[str, str | None]:
    """Return the tuning knobs of one Compose service environment mapping.

    ``docker compose config`` always renders ``environment`` as a mapping, so a
    service that declares it any other way simply contributes nothing.
    """
    if not isinstance(environment, dict):
        return {}
    tuning: dict[str, str | None] = {}
    for key, value in cast(dict[str, object], environment).items():
        name = str(key)
        if not any(marker in name for marker in TUNING_ENV_MARKERS):
            continue
        tuning[name] = None if value is None else str(value)
    return dict(sorted(tuning.items()))


def load_service_tuning(run_dir: Path) -> ServiceTuning | None:
    """Extract per-service scheduling knobs from the captured Compose model.

    Returns ``None`` when the run has no readable Compose capture, which is the
    normal shape for a run aborted before the collector wrote it.
    """
    raw = _read_text(run_dir / "compose_config.yml")
    if raw is None:
        return None
    try:
        loaded: object = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    services_obj = cast(dict[str, object], loaded).get("services")
    if not isinstance(services_obj, dict):
        return None
    tuning: ServiceTuning = {}
    for name, service in sorted(cast(dict[str, object], services_obj).items()):
        if not isinstance(service, dict):
            continue
        knobs = _tuning_environment(cast(dict[str, object], service).get("environment"))
        if knobs:
            tuning[str(name)] = knobs
    return tuning


def load_run_detail(root: Path, run_id: str, matrix: str | None) -> RunDetailPayload:
    """Bundle every per-run artifact the detail view renders."""
    run_dir = root / run_id
    return RunDetailPayload(
        run_id=run_id,
        artifact_root=str(root),
        matrix=matrix,
        scenario=_read_json_document(run_dir / "scenario.json"),
        manifest=_read_json_document(run_dir / "manifest.json"),
        summary=_summary_record(run_dir),
        summary_text=_read_text(run_dir / "summary.txt"),
        activity_metrics=_read_json_document(run_dir / "activity_metrics.json"),
        pg_settings=_read_json_array(run_dir / "pg_settings.json"),
        row_correctness=_read_json_document(run_dir / "row_correctness.json"),
        table_drift=_read_json_document(run_dir / "table_drift.json"),
        temporal_sdk_metrics=_sdk_metrics_counters(run_dir),
        containers=_read_json_array(run_dir / "containers.json"),
        service_tuning=load_service_tuning(run_dir),
    )


def load_run_compose(root: Path, run_id: str) -> str | None:
    """Return the resolved Compose model, or ``None`` when it was not captured."""
    return _read_text(root / run_id / "compose_config.yml")


def load_run_timeseries(root: Path, run_id: str) -> TimeseriesPayload:
    """Return every sampled series plus the runner's execution records."""
    run_dir = root / run_id
    return TimeseriesPayload(
        run_id=run_id,
        pg_activity=cast(
            list[PgActivitySample], _read_jsonl(run_dir / "pg_activity.jsonl")
        ),
        resource_usage=cast(
            list[ResourceUsageSample], _read_jsonl(run_dir / "resource_usage.jsonl")
        ),
        temporal_backlog=cast(
            list[TemporalBacklogSample], _read_jsonl(run_dir / "temporal_backlog.jsonl")
        ),
        executions=_execution_records(run_dir),
    )


class ViewerServer(ThreadingHTTPServer):
    """Threading HTTP server that carries the artifact root it serves."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        artifact_root: Path,
    ) -> None:
        self.artifact_root: Path = artifact_root
        super().__init__(server_address, handler_class)


class ViewerHandler(BaseHTTPRequestHandler):
    """Read-only request handler for the SPA and its JSON API."""

    server_version = "tracecat-benchmark-viewer"
    protocol_version = "HTTP/1.1"

    @property
    def artifact_root(self) -> Path:
        return cast(ViewerServer, self.server).artifact_root

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler dispatch name
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send_index()
            return
        if path == "/api/runs":
            self._send_json(cast(JsonValue, list_runs(self.artifact_root)))
            return
        timeseries_match = RUN_TIMESERIES_ROUTE.fullmatch(path)
        if timeseries_match is not None:
            run_id = self._resolved_run_id(timeseries_match.group("run_id"))
            if run_id is None:
                self._send_error(HTTPStatus.NOT_FOUND, "unknown run")
                return
            self._send_json(
                cast(JsonValue, load_run_timeseries(self.artifact_root, run_id))
            )
            return
        compose_match = RUN_COMPOSE_ROUTE.fullmatch(path)
        if compose_match is not None:
            run_id = self._resolved_run_id(compose_match.group("run_id"))
            if run_id is None:
                self._send_error(HTTPStatus.NOT_FOUND, "unknown run")
                return
            compose = load_run_compose(self.artifact_root, run_id)
            if compose is None:
                self._send_error(HTTPStatus.NOT_FOUND, "compose_config.yml missing")
                return
            self._send_bytes(
                HTTPStatus.OK, "text/plain; charset=utf-8", compose.encode("utf-8")
            )
            return
        detail_match = RUN_DETAIL_ROUTE.fullmatch(path)
        if detail_match is not None:
            run_id = self._resolved_run_id(detail_match.group("run_id"))
            if run_id is None:
                self._send_error(HTTPStatus.NOT_FOUND, "unknown run")
                return
            matrix = build_index(self.artifact_root).matrix_by_run.get(run_id)
            self._send_json(
                cast(JsonValue, load_run_detail(self.artifact_root, run_id, matrix))
            )
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def _resolved_run_id(self, raw: str) -> str | None:
        """Validate the fingerprint before it is ever joined onto a path."""
        run_id = unquote(raw)
        if RUN_ID_RE.fullmatch(run_id) is None:
            return None
        if not (self.artifact_root / run_id).is_dir():
            return None
        return run_id

    def _send_index(self) -> None:
        try:
            body = INDEX_HTML_PATH.read_bytes()
        except OSError:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "viewer.html missing")
            return
        self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", body)

    def _send_json(self, payload: JsonValue) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self._send_bytes(HTTPStatus.OK, "application/json; charset=utf-8", body)

    def _send_error(self, status: HTTPStatus, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tracecat-benchmark-viewer",
        description="Browse workflow load-test benchmark artifacts locally.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
        help=f"directory holding run artifacts (default: {DEFAULT_ARTIFACT_ROOT})",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"default: {DEFAULT_PORT}"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"default: {DEFAULT_HOST}")
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args(sys.argv[1:])
    artifact_root: Path = args.artifact_root.expanduser().resolve(strict=False)
    if not artifact_root.is_dir():
        sys.stderr.write(f"artifact root is not a directory: {artifact_root}\n")
        raise SystemExit(2)

    host: str = args.host
    port: int = args.port
    server = ViewerServer((host, port), ViewerHandler, artifact_root)
    sys.stderr.write(f"serving {artifact_root} on http://{host}:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("shutting down\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
