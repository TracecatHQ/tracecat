"""Staging burst replay harness (ENG-2613): drive, sample, collect, report.

Replays the November incidents against a staging cluster with two test orgs
(A: noisy, B: steady) and measures what org B sees. See README.md in this
folder for the scenarios, setup and the exact commands used.

Subcommands:

- ``drive``: send webhook starts from this machine, one or more streams at
  fixed rates; writes one JSON line per request (client latency, status,
  execution ID).
- ``sample``: poll the cluster with kubectl every few seconds (pod memory and
  restarts, PgDog pool metrics per database); writes JSON lines.
- ``collect``: run ``pod_collect.py`` inside the DSL worker pod to read the
  Temporal histories of every run the test started; writes JSON lines.
- ``report``: percentiles per org and the peaks, as Markdown and JSON, plus
  an SVG timeline.

Webhook URLs carry a secret, so they come from environment variables named in
the stream spec and are never written to output files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- drive


@dataclass(frozen=True, slots=True)
class Stream:
    """One stream of webhook starts.

    Attributes:
        label: Name in the output, conventionally ``<org>-<kind>`` (``A-lock``).
        url_env: Environment variable holding the webhook URL.
        rate: Starts per second; 0 sends as fast as ``concurrency`` allows.
        count: Number of starts.
        concurrency: Requests in flight at most.
        after: Seconds to wait after the drive begins before the first start.
        payload: JSON body; ``seq`` is added, and ``pad_bytes`` becomes a
            ``pad`` string of that many bytes.
    """

    label: str
    url_env: str
    rate: float
    count: int
    concurrency: int = 20
    after: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)


def _parse_stream(raw: str) -> Stream:
    data = json.loads(raw)
    return Stream(
        label=data["label"],
        url_env=data["url_env"],
        rate=float(data.get("rate", 0)),
        count=int(data["count"]),
        concurrency=int(data.get("concurrency", 20)),
        after=float(data.get("after", 0)),
        payload=dict(data.get("payload", {})),
    )


async def _run_stream(
    client: httpx.AsyncClient, stream: Stream, t_zero: float, out: Any
) -> None:
    url = os.environ[stream.url_env]
    base = dict(stream.payload)
    if pad := int(base.pop("pad_bytes", 0)):
        base["pad"] = "x" * pad
    sem = asyncio.Semaphore(stream.concurrency)

    async def one(seq: int) -> None:
        async with sem:
            sent = time.time()
            row: dict[str, Any] = {"label": stream.label, "seq": seq, "sent": sent}
            try:
                resp = await client.post(url, json=base | {"seq": seq})
                row["status"] = resp.status_code
                if resp.status_code == 200:
                    row["wf_exec_id"] = resp.json().get("wf_exec_id")
                else:
                    row["error"] = resp.text[:200]
            except httpx.HTTPError as e:
                row["status"] = 0
                row["error"] = f"{type(e).__name__}: {e}"[:200]
            row["latency"] = time.time() - sent
            out.write(json.dumps(row) + "\n")
            out.flush()

    await asyncio.sleep(max(0.0, t_zero + stream.after - time.time()))
    start = time.time()
    tasks: list[asyncio.Task[None]] = []
    for seq in range(stream.count):
        if stream.rate > 0:
            await asyncio.sleep(max(0.0, start + seq / stream.rate - time.time()))
        else:
            # Unpaced: only queue as many as the semaphore can take.
            while sum(not t.done() for t in tasks) >= stream.concurrency:
                await asyncio.sleep(0.005)
        tasks.append(asyncio.create_task(one(seq)))
    await asyncio.gather(*tasks)


async def drive(args: argparse.Namespace) -> None:
    streams = [_parse_stream(raw) for raw in args.stream]
    missing = [s.url_env for s in streams if not os.environ.get(s.url_env)]
    if missing:
        sys.exit(f"missing webhook URL env vars: {', '.join(missing)}")
    limits = httpx.Limits(max_connections=sum(s.concurrency for s in streams))
    timeout = httpx.Timeout(args.timeout)
    t_zero = time.time()
    print(f"drive start {datetime.now(UTC).isoformat()}", file=sys.stderr)
    with open(args.out, "a") as out:
        async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
            await asyncio.gather(
                *(_run_stream(client, s, t_zero, out) for s in streams)
            )
    print(f"drive end {datetime.now(UTC).isoformat()}", file=sys.stderr)


# --------------------------------------------------------------------------- sample


def _kubectl(args: argparse.Namespace, *cmd: str, timeout: float = 20) -> str:
    full = ["kubectl", "--context", args.context, "-n", args.namespace, *cmd]
    proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return proc.stdout


def _mem_mib(value: str) -> float:
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    for unit, factor in units.items():
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    return float(value) / (1024 * 1024)


def _pgdog_metrics(text: str) -> dict[str, dict[str, float]]:
    """Per-database PgDog pool gauges from its Prometheus text."""
    wanted = {"pgdog_cl_waiting", "pgdog_sv_active", "pgdog_sv_idle", "pgdog_maxwait"}
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        if line.startswith("pgdog_clients "):
            out.setdefault("_all", {})["clients"] = float(line.split()[1])
            continue
        name, _, rest = line.partition("{")
        if name not in wanted or 'database="' not in rest:
            continue
        database = rest.split('database="', 1)[1].split('"', 1)[0]
        value = float(rest.rsplit("}", 1)[1])
        out.setdefault(database, {})[name.removeprefix("pgdog_")] = value
    return out


def _raw_metrics(args: argparse.Namespace, pod: str) -> dict[str, Any]:
    path = f"/api/v1/namespaces/{args.namespace}/pods/{pod}:9090/proxy/metrics"
    try:
        proc = subprocess.run(
            ["kubectl", "--context", args.context, "get", "--raw", path],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)[:200]}
    return _pgdog_metrics(proc.stdout)


def sample(args: argparse.Namespace) -> None:
    deadline = time.time() + args.duration
    pgdog_pods = [
        line.split("/", 1)[1]
        for line in _kubectl(args, "get", "pods", "-o", "name").splitlines()
        if "pgdog" in line
    ]
    pool = ThreadPoolExecutor(max_workers=2 + len(pgdog_pods))
    with open(args.out, "a") as out:
        while time.time() < deadline:
            row: dict[str, Any] = {"t": time.time()}
            pods_f = pool.submit(_kubectl, args, "get", "pods", "-o", "json")
            top_f = pool.submit(_kubectl, args, "top", "pods", "--no-headers")
            pgdog_f = {pod: pool.submit(_raw_metrics, args, pod) for pod in pgdog_pods}
            try:
                pods = json.loads(pods_f.result())
                row["pods"] = {
                    p["metadata"]["name"]: {
                        "phase": p["status"].get("phase"),
                        "restarts": sum(
                            c.get("restartCount", 0)
                            for c in p["status"].get("containerStatuses", [])
                        ),
                        "last_terminated": [
                            c.get("lastState", {}).get("terminated", {}).get("reason")
                            for c in p["status"].get("containerStatuses", [])
                            if c.get("lastState", {}).get("terminated")
                        ],
                    }
                    for p in pods["items"]
                }
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                row["pods_error"] = str(e)
            try:
                top = top_f.result()
                rows = [line.split() for line in top.splitlines()]
                row["mem_mib"] = {r[0]: _mem_mib(r[2]) for r in rows if len(r) >= 3}
                row["cpu_m"] = {
                    r[0]: float(r[1].removesuffix("m")) for r in rows if len(r) >= 3
                }
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                row["top_error"] = str(e)
            row["pgdog"] = {pod: f.result() for pod, f in pgdog_f.items()}
            out.write(json.dumps(row) + "\n")
            out.flush()
            time.sleep(max(0.0, args.interval - (time.time() - row["t"])))


# --------------------------------------------------------------------------- collect


def collect(args: argparse.Namespace) -> None:
    env = [
        f"BURST_WORKSPACES={args.workspaces or os.environ['BURST_WORKSPACES']}",
        f"BURST_SINCE={args.since}",
        f"BURST_UNTIL={args.until}",
    ]
    cmd = [
        "kubectl", "--context", args.context, "-n", args.namespace, "exec", "-i",
        f"deploy/{args.pod_deployment}", "-c", args.container, "--",
        "env", *env, "python", "-",
    ]  # fmt: skip
    script = (HERE / "pod_collect.py").read_text()
    labels = {pair.split("=", 1)[0] for pair in env[0].split("=", 1)[1].split(",")}
    for attempt in range(1, args.attempts + 1):
        proc = subprocess.run(
            cmd, input=script, capture_output=True, text=True, timeout=1800
        )
        done = {
            line.split()[1]
            for line in proc.stdout.splitlines()
            if line.startswith("DONE ")
        }
        # kubectl exec can exit 0 after its stream was reset, so completeness
        # is judged by the collector's DONE line per workspace.
        if proc.returncode == 0 and done >= labels:
            break
        print(
            f"collect attempt {attempt} incomplete (done {sorted(done)}): "
            f"{proc.stderr[-300:]}",
            file=sys.stderr,
        )
    else:
        sys.exit("collect failed")
    with open(args.out, "w") as out:
        for line in proc.stdout.splitlines():
            if line.startswith("RUN "):
                out.write(line[4:] + "\n")
            else:
                print(line, file=sys.stderr)


# --------------------------------------------------------------------------- report


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return ordered[rank]


def _stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "p50": _pct(values, 50),
        "p95": _pct(values, 95),
        "p99": _pct(values, 99),
        "max": max(values) if values else None,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _max_width(intervals: list[tuple[float, float]]) -> int:
    points = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals])
    width = best = 0
    for _, step in points:
        width += step
        best = max(best, width)
    return best


def summarize_run(run_dir: Path) -> dict[str, Any]:
    """Compute the per-org metrics for one run directory."""
    runs = [r for r in _read_jsonl(run_dir / "runs.jsonl") if "error" not in r]
    requests = _read_jsonl(run_dir / "requests.jsonl")
    samples = _read_jsonl(run_dir / "samples.jsonl")
    orgs = sorted(
        {r["org"] for r in runs} | {q["label"].split("-", 1)[0] for q in requests}
    )
    result: dict[str, Any] = {"run": run_dir.name, "orgs": {}}
    for org in orgs:
        dsl = [r for r in runs if r["org"] == org and r["type"] == "DSLWorkflow"]
        tools = [
            r
            for r in runs
            if r["org"] == org and r["type"] == "ExecuteRegistryToolWorkflow"
        ]
        reqs = [q for q in requests if q["label"].split("-", 1)[0] == org]
        start_delay = [
            r["first_wft"] - r["started"]
            for r in dsl
            if r["first_wft"] and r["started"]
        ]
        pacing = [r["pacing"] for r in dsl if r["pacing"] is not None]
        ex_pacing = [
            r["first_wft"] - r["started"] - (r["pacing"] or 0)
            for r in dsl
            if r["first_wft"] and r["started"]
        ]
        acts = [a for r in dsl for a in r["acts"]]
        act_s2s = [a[2] - a[1] for a in acts if a[2] is not None]
        tool_acts = [a for r in tools for a in r["acts"]]
        tool_s2s = [a[2] - a[1] for a in tool_acts if a[2] is not None]
        tool_e2e = [
            r["acts"][0][2] - r["started"]
            for r in tools
            if r["acts"] and r["acts"][0][2] is not None and r["started"]
        ]
        statuses: dict[str, int] = {}
        for r in dsl + tools:
            statuses[f"{r['type']}:{r['status']}"] = (
                statuses.get(f"{r['type']}:{r['status']}", 0) + 1
            )
        failures = sorted({r["failure"] for r in dsl + tools if r.get("failure")})[:10]
        # Bounced attempts ran nothing, so they don't count toward the width.
        intervals = [
            (a[2], a[3])
            for a in acts
            if a[2] is not None and a[3] is not None and a[5] != "not_admitted"
        ]
        ok_lat = [q["latency"] for q in reqs]
        started_by_id = {r["workflow_id"]: r["started"] for r in dsl}
        # Server accept time minus client send time: one-way network plus the
        # API's work before the start lands (includes any clock skew).
        accept_lag = [
            started_by_id[q["wf_exec_id"]] - q["sent"]
            for q in reqs
            if q.get("wf_exec_id") in started_by_id
        ]
        result["orgs"][org] = {
            "workflow_start_delay": _stats(start_delay),
            "workflow_start_delay_ex_pacing": _stats(ex_pacing),
            "pacing_delay": _stats(pacing),
            "action_schedule_to_start": _stats(act_s2s),
            "tool_call_schedule_to_start": _stats(tool_s2s),
            "tool_call_start_to_activity_start": _stats(tool_e2e),
            "api_latency": _stats(ok_lat),
            "api_accept_lag": _stats(accept_lag),
            "not_admitted": sum(1 for a in acts if a[5] == "not_admitted"),
            "actions_failed": sum(1 for a in acts if a[5] in ("failed", "timed_out")),
            "api_status": {
                str(s): sum(1 for q in reqs if q.get("status") == s)
                for s in sorted({q.get("status") for q in reqs}, key=str)
            },
            "statuses": statuses,
            "failures": failures,
            "max_action_width": _max_width(intervals),
            "history_events_max": max((r["events"] for r in dsl), default=0),
            "signals_total": sum(r["signals"] for r in dsl),
            "first_start": min(
                (r["started"] for r in dsl + tools if r["started"]), default=None
            ),
            "last_close": max(
                (r["closed"] for r in dsl + tools if r["closed"]), default=None
            ),
        }
    peaks: dict[str, Any] = {"pgdog": {}, "mem_mib": {}, "restarts": {}, "oom": []}
    for s in samples:
        for pod, dbs in s.get("pgdog", {}).items():
            for db, gauges in dbs.items():
                if db == "error" or not isinstance(gauges, dict):
                    continue
                key = f"{pod}:{db}"
                cur = peaks["pgdog"].setdefault(key, {})
                for g, v in gauges.items():
                    cur[g] = max(cur.get(g, 0), v)
        for pod, mib in s.get("mem_mib", {}).items():
            peaks["mem_mib"][pod] = max(peaks["mem_mib"].get(pod, 0), mib)
        for pod, info in s.get("pods", {}).items():
            peaks["restarts"][pod] = info["restarts"]
            if (
                "OOMKilled" in info.get("last_terminated", [])
                and pod not in peaks["oom"]
            ):
                peaks["oom"].append(pod)
    totals = [
        sum(
            g.get("sv_active", 0)
            for dbs in s.get("pgdog", {}).values()
            for db, g in dbs.items()
            if isinstance(g, dict) and db != "_all"
        )
        for s in samples
    ]
    peaks["pgdog_sv_active_total_max"] = max(totals, default=0)
    result["peaks"] = peaks
    return result


def _fmt(v: float | int | None) -> str:
    if v is None:
        return "-"
    if isinstance(v, int):
        return str(v)
    return f"{v:.2f}"


METRICS = [
    ("workflow_start_delay", "Start delay (accept → first task)"),
    ("pacing_delay", "  of which pacing"),
    ("workflow_start_delay_ex_pacing", "  excluding pacing"),
    ("action_schedule_to_start", "Action schedule-to-start"),
    ("tool_call_schedule_to_start", "Tool-call schedule-to-start"),
    ("api_latency", "API latency (webhook, client)"),
    ("api_accept_lag", "API send → start accepted"),
]


def _svg_timeline(run_dir: Path) -> str:
    """A small SVG: per-second action starts per org and PgDog sv_active total."""
    runs = [r for r in _read_jsonl(run_dir / "runs.jsonl") if "error" not in r]
    samples = _read_jsonl(run_dir / "samples.jsonl")
    starts: dict[str, list[float]] = {}
    for r in runs:
        for a in r["acts"]:
            if a[2] is not None:
                starts.setdefault(r["org"], []).append(a[2])
    all_t = [t for ts in starts.values() for t in ts] + [s["t"] for s in samples]
    if not all_t:
        return ""
    t0, t1 = min(all_t), max(all_t)
    width, height, pad = 900, 260, 40
    span = max(1.0, t1 - t0)

    def x(t: float) -> float:
        return pad + (t - t0) / span * (width - 2 * pad)

    colors = {"A": "#d1495b", "B": "#00798c"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'font-family="sans-serif" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="{pad}" y="16">{run_dir.name}: actions started per 5 s (bars) and '
        "PgDog sv_active total (line)</text>",
    ]
    bucket = 5.0
    peak = 1
    hist: dict[str, dict[int, int]] = {}
    for org, ts in starts.items():
        h: dict[int, int] = {}
        for t in ts:
            h[int((t - t0) // bucket)] = h.get(int((t - t0) // bucket), 0) + 1
        hist[org] = h
        peak = max(peak, *h.values())
    for i, (org, h) in enumerate(sorted(hist.items())):
        for b, n in h.items():
            bx = x(t0 + b * bucket) + i * 3
            bh = n / peak * (height - 2 * pad)
            parts.append(
                f'<rect x="{bx:.1f}" y="{height - pad - bh:.1f}" width="3" '
                f'height="{bh:.1f}" fill="{colors.get(org, "#888")}"/>'
            )
        parts.append(
            f'<text x="{width - 150}" y="{30 + 14 * i}" fill="{colors.get(org, "#888")}">'
            f"org {org} (peak {peak}/5 s)</text>"
        )
    totals = [
        (
            s["t"],
            sum(
                g.get("sv_active", 0)
                for dbs in s.get("pgdog", {}).values()
                for db, g in dbs.items()
                if isinstance(g, dict) and db != "_all"
            ),
        )
        for s in samples
    ]
    if totals:
        top = max(140.0, max(v for _, v in totals))
        pts = " ".join(
            f"{x(t):.1f},{height - pad - v / top * (height - 2 * pad):.1f}"
            for t, v in totals
        )
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="#333" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{width - 150}" y="{30 + 14 * 2}">sv_active (axis 0-{top:.0f})</text>'
        )
    parts.append(
        f'<text x="{pad}" y="{height - 10}">0 s</text>'
        f'<text x="{width - pad - 40}" y="{height - 10}">{span:.0f} s</text></svg>'
    )
    return "\n".join(parts)


def report(args: argparse.Namespace) -> None:
    sections: list[str] = []
    summaries = []
    for d in args.dirs:
        run_dir = Path(d)
        summary = summarize_run(run_dir)
        summaries.append(summary)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        if svg := _svg_timeline(run_dir):
            (run_dir / "timeline.svg").write_text(svg)
        lines = [
            f"### {run_dir.name}",
            "",
            "| Metric (s) | Org | n | p50 | p95 | p99 | max |",
            "|---|---|---|---|---|---|---|",
        ]
        for key, title in METRICS:
            for org, m in summary["orgs"].items():
                s = m[key]
                if s["n"]:
                    lines.append(
                        f"| {title} | {org} | {s['n']} | {_fmt(s['p50'])} | {_fmt(s['p95'])} "
                        f"| {_fmt(s['p99'])} | {_fmt(s['max'])} |"
                    )
        lines.append("")
        for org, m in summary["orgs"].items():
            lines.append(
                f"- Org {org}: statuses {m['statuses']}; API status {m['api_status']}; "
                f"max action width {m['max_action_width']}; not admitted {m['not_admitted']}; "
                f"actions failed {m['actions_failed']}; max history {m['history_events_max']} "
                f"events; signals {m['signals_total']}"
            )
            for f in m["failures"]:
                lines.append(f"  - failure: {f}")
        p = summary["peaks"]
        lines.append(
            f"- PgDog sv_active total peak: {p['pgdog_sv_active_total_max']:.0f}"
        )
        lines.append(
            "- Memory peaks (MiB): "
            + ", ".join(
                f"{k.rsplit('-', 2)[0]} {v:.0f}"
                for k, v in sorted(p["mem_mib"].items())
                if any(s in k for s in ("worker", "executor", "api", "pgdog"))
            )
        )
        lines.append(f"- OOMKilled: {p['oom'] or 'none'}")
        sections.append("\n".join(lines))
    text = "\n\n".join(sections) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    print(text)


# --------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--context", default="tracecat-staging")
    parser.add_argument("--namespace", default="tracecat")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("drive", help="send webhook starts")
    p.add_argument(
        "--stream", action="append", required=True, help="JSON stream spec (see Stream)"
    )
    p.add_argument("--out", required=True)
    p.add_argument(
        "--timeout", type=float, default=60.0, help="per-request timeout (s)"
    )

    p = sub.add_parser("sample", help="poll pods, memory and PgDog")
    p.add_argument("--out", required=True)
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--interval", type=float, default=5.0)

    p = sub.add_parser("collect", help="read Temporal histories in the worker pod")
    p.add_argument("--out", required=True)
    p.add_argument("--since", required=True, help="RFC 3339 UTC")
    p.add_argument("--until", required=True, help="RFC 3339 UTC")
    p.add_argument(
        "--workspaces", help="label=workspace_id,... (default $BURST_WORKSPACES)"
    )
    p.add_argument("--pod-deployment", default="tracecat-worker")
    p.add_argument("--container", default="worker")
    p.add_argument("--attempts", type=int, default=3)

    p = sub.add_parser("report", help="percentiles and peaks per run directory")
    p.add_argument("dirs", nargs="+")
    p.add_argument("--out")

    args = parser.parse_args()
    match args.cmd:
        case "drive":
            asyncio.run(drive(args))
        case "sample":
            sample(args)
        case "collect":
            collect(args)
        case "report":
            report(args)


if __name__ == "__main__":
    main()
