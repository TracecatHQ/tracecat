#!/usr/bin/env python3
"""Check every externally reachable RLS surface of a Tracecat deployment.

Run it before and after switching a deployment's `TRACECAT__RLS_MODE` to
`enforce`. Under `enforce` a query that runs without the right workspace
context returns no rows instead of failing, so each check compares what the
API returns with what should be there, and the run can be diffed against a
baseline taken under `off` (`--baseline`).

What it does, as one user in one organization:

1. Seeds a QA workspace pair (`<tag>-a`, `<tag>-b`) idempotently: a secret, a
   variable, a table with a row, a case field, a case, and the workflows the
   checks run. Reruns reuse them.
2. Runs one check per surface: workflow runs (manual, webhook, webhook with an
   API key, schedule, case trigger, child workflow + loop, secrets, variables,
   tables and cases read from inside a run), case search, agent turn with a
   tool call, workspace and case chat, the public Slack channel endpoint, and
   cross-workspace isolation through a service account bound to workspace A.
3. Calls every parameter-free GET route in the deployment's OpenAPI schema, in
   organization and in workspace context, and records status and item count.
4. Optionally runs a read-only SQL probe inside the cluster as the app's own
   login (`--sql`): per tenant table, rows visible to the owning workspace vs.
   with the RLS bypass, to another workspace, and to no context.
5. Optionally scans service logs for RLS error signatures (`--logs`).

Writes `results.json`, `summary.md` and evidence to `--out` (keep it outside
the repo: evidence names workspaces and objects in the target org) and prints
one PASS/FAIL line per check. Exits 0 when every check ran and passed, 1 on
any FAIL, 2 when nothing failed but a check was skipped (for example
`--sql skip`). With `--baseline`, a check that passed in the baseline and
fails or is skipped now, or a GET route that returned data in the baseline
and returns none now, is reported as a regression.

Never prints credentials. Request and response bodies are saved with
credential fields and capability-URL secrets redacted; raw log lines are
saved only with `--keep-log-lines`. Webhooks are left offline, the channel
token is deleted and the isolation service account is disabled after use.

Usage:
    uv run --no-project --with httpx==0.28.1 python scripts/rls_sweep.py \
        --target staging --out ~/rls-sweep/off
    uv run --no-project --with httpx==0.28.1 python scripts/rls_sweep.py \
        --target staging --out ~/rls-sweep/enforce --baseline ~/rls-sweep/off

Targets:
    staging  https://app.tracecat-staging.com. Password from
             `RLS_SWEEP_PASSWORD`, else the macOS Keychain item
             `tracecat-staging-dev`. SQL and logs through `kubectl` against the
             current context (namespace `tracecat`).
    local    `--origin` (default http://localhost). Password from
             `RLS_SWEEP_PASSWORD`, else the dev seed password. SQL and logs
             through `docker` (`--sql docker:<api container>`).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

FINAL_RUN_STATES = ("COMPLETED", "FAILED", "TERMINATED", "CANCELED", "TIMED_OUT")
DENIED = (401, 403, 404)
# Every object this script creates carries this in its description, and it
# reuses or mutates a name-matched object only if that object carries it too.
# This stops accidental collisions, not deliberate ones: descriptions are
# user-writable. Workspaces have no description and are matched by name alone.
OWNER_MARKER = "RLS sweep"
SECRET_VALUE = "synthetic-not-a-secret"
REDACT_KEYS = {
    "values",
    "endpoint_url",
    "public_token",
    "url",
    "access_token",
    "api_key",
    "cookie",
    "issued_api_key",
    "keys",
    "password",
    "raw_key",
    "secret",
    "slack_bot_token",
    "slack_signing_secret",
    "token",
    "value_hash",
}
# Routes that stream, block, redirect to a third party, or change state on GET.
GET_SWEEP_SKIP = re.compile(
    r"(callback|oauth|authorize|login|logout|stream|/wait|download|export|"
    r"install|openid|jwks|userinfo|/token|health|/ready|/sse|/events)",
    re.IGNORECASE,
)
LOG_SIGNATURES = re.compile(
    r"permission denied|row-level security|row level security|42501|"
    r"InsufficientPrivilege|RLSViolation|new row violates|must be owner|"
    r"UnsafeDatabaseLogin|Unauthorized channel|RLS_DIAG",
    re.IGNORECASE,
)
STAGING_DEPLOYMENTS = (
    "tracecat-api",
    "tracecat-worker",
    "tracecat-background-worker",
    "tracecat-executor",
    "tracecat-agent-executor",
    "tracecat-agent-worker",
    "tracecat-mcp",
    "tracecat-litellm",
    "tracecat-runtime-executor",
    "tracecat-runtime-worker",
    "tracecat-tool-gateway",
)
LOCAL_SERVICES = (
    "api",
    "worker",
    "background-worker",
    "executor",
    "agent-worker",
    "agent-executor",
    "mcp",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


# Capability URLs carry their credential in the path.
CAPABILITY_PATHS = re.compile(
    r"(/webhooks/[^/\s\"']+/)[^/\s\"'?]+|(/channels/[a-z_-]+/)[^/\s\"'?]+"
)


DB_URI_PASSWORD = re.compile(r"(\w+(?:\+\w+)?://[^:/@\s]+:)[^@\s]+@")


def scrub(text: str) -> str:
    """Mask capability-URL secrets and passwords in connection URIs."""
    text = CAPABILITY_PATHS.sub(
        lambda m: (m.group(1) or m.group(2)) + "[REDACTED]", text
    )
    return DB_URI_PASSWORD.sub(lambda m: m.group(1) + "[REDACTED]@", text)


# Substrings that mark a credential-bearing key (e.g. slack_client_secret).
SENSITIVE_PARTS = ("secret", "token", "password", "api_key", "cookie", "credential")


def sensitive_key(key: str) -> bool:
    k = key.lower()
    return k in REDACT_KEYS or any(part in k for part in SENSITIVE_PARTS)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if sensitive_key(k) else redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return scrub(value)
    return value


def summarize(value: Any) -> Any:
    """Shape of a read response: reads carry member emails, case text and row data."""
    items = items_of(value)
    if items is not None:
        return {"items": len(items)}
    if isinstance(value, dict):
        return {"keys": sorted(value)[:20]}
    return type(value).__name__


def items_of(body: Any) -> list[Any] | None:
    """Return the item list of a list or paginated response, else None."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "data", "results"):
            if isinstance(body.get(key), list):
                return body[key]
    return None


class CheckFailed(AssertionError):
    """A check observed something other than the expected behaviour."""


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


@dataclass(slots=True)
class Target:
    name: str
    origin: str
    email: str
    password: str = field(repr=False)
    sql: str | None
    logs: str | None
    keep_log_lines: bool
    model: dict[str, str]
    catalog_id: str | None
    harness: dict[str, str]


@dataclass(slots=True)
class CheckResult:
    name: str
    surface: str
    expected: str
    result: str = "SKIP"
    observed: Any = None
    error: str | None = None
    seconds: float = 0.0


@dataclass(slots=True)
class Run:
    target: Target
    tag: str
    out: Path
    stamp: str = field(default_factory=lambda: f"{datetime.now(UTC):%Y%m%d-%H%M%S}")
    started: str = field(default_factory=now)
    checks: list[CheckResult] = field(default_factory=list)
    seeds: dict[str, Any] = field(default_factory=dict)
    http_log: list[dict[str, Any]] = field(default_factory=list)


class Api:
    """Cookie-authenticated client that records every call for evidence."""

    def __init__(self, run: Run, *, headers: dict[str, str] | None = None) -> None:
        self.run = run
        self.client = httpx.Client(
            base_url=run.target.origin, timeout=120, headers=headers or {}
        )
        self.workspace_id: str | None = None

    def login(self) -> dict[str, Any]:
        t = self.run.target
        r = self.client.post(
            "/api/auth/login", data={"username": t.email, "password": t.password}
        )
        expect(r.status_code in (200, 204), f"login HTTP {r.status_code}")
        # The session cookie is Secure; re-set it so plain-http local runs send it.
        for k, v in r.cookies.items():
            self.client.cookies.set(k, v)
        me = self.client.get("/api/users/me").raise_for_status().json()
        expect(me.get("email") == t.email, "logged in as the wrong account")
        return me

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        ws: str | None = "",
        ok: tuple[int, ...] | None = (200, 201, 202, 204),
        **kw: Any,
    ) -> httpx.Response:
        params = dict(kw.pop("params", {}))
        workspace = self.workspace_id if ws == "" else ws
        if workspace and "workspace_id" not in params:
            params["workspace_id"] = workspace
        r = self.client.request(method, path, params=params, json=body, **kw)
        try:
            value = r.json() if r.content else None
        except ValueError:
            value = r.text[:2000]
        self.run.http_log.append(
            {
                "at": now(),
                "method": method,
                "path": path,
                "params": params,
                "status": r.status_code,
                "request": redact(body),
                "response": summarize(value) if method == "GET" else redact(value),
            }
        )
        if ok is not None:
            expect(
                r.status_code in ok,
                scrub(f"{method} {path}: HTTP {r.status_code} {redact(value)}")[:400],
            )
        return r

    def json(self, method: str, path: str, **kw: Any) -> Any:
        r = self.call(method, path, **kw)
        return r.json() if r.content else None


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


def find_or_create_workspace(api: Api, name: str) -> str:
    for ws in api.json("GET", "/api/workspaces", ws=None):
        if ws["name"] == name:
            return ws["id"]
    return api.json("POST", "/api/workspaces", ws=None, body={"name": name})["id"]


def snake(tag: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", tag.lower())


def workflow_definition(title: str, actions: list[dict[str, Any]], returns: Any) -> str:
    return json.dumps(
        {
            "definition": {
                "title": title,
                "description": "RLS sweep probe (scripts/rls_sweep.py); safe to delete.",
                "entrypoint": {"ref": actions[0]["ref"]},
                "actions": actions,
                "returns": returns,
            }
        }
    )


def definition_digest(actions: list[dict[str, Any]], returns: Any) -> str:
    raw = json.dumps([actions, returns], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:6]


def find_or_create_workflow(
    api: Api,
    title: str,
    actions: list[dict[str, Any]],
    returns: Any,
    alias: str | None = None,
) -> str:
    """Return the id of the workflow called `title`, creating and publishing it if needed.

    The title carries a hash of the definition, so a changed definition gets a
    new workflow instead of silently reusing a stale one.
    """
    title = f"{title}-{definition_digest(actions, returns)}"
    listing = api.json("GET", "/api/workflows", params={"limit": 200})
    for wf in items_of(listing) or []:
        if wf.get("title") == title:
            expect(
                OWNER_MARKER in (wf.get("description") or ""),
                f"workflow {title!r} exists but was not created by this script",
            )
            return wf["id"]
    raw = workflow_definition(title, actions, returns)
    wf = api.json(
        "POST",
        "/api/workflows",
        files={"file": (f"{title}.json", raw, "application/json")},
    )
    if alias:
        api.call(
            "PATCH",
            f"/api/workflows/{wf['id']}",
            body={"alias": alias},
        )
    api.call("POST", f"/api/workflows/{wf['id']}/commit")
    return wf["id"]


def seed(run: Run, api: Api) -> dict[str, Any]:
    """Create or reuse the QA objects every check reads. Idempotent per tag."""
    t = snake(run.tag)
    s: dict[str, Any] = {"tag": run.tag}
    s["workspace_a"] = find_or_create_workspace(api, f"{run.tag}-a")
    s["workspace_b"] = find_or_create_workspace(api, f"{run.tag}-b")

    for side in ("a", "b"):
        api.workspace_id = s[f"workspace_{side}"]
        secret = f"{t}_{side}_secret"
        existing = api.json("GET", "/api/secrets")
        if not any(x["name"] == secret for x in existing):
            api.call(
                "POST",
                "/api/secrets",
                body={
                    "name": secret,
                    "type": "custom",
                    "keys": [{"key": "K", "value": SECRET_VALUE}],
                },
            )
        s[f"{side}_secret_name"] = secret
        s[f"{side}_secret_id"] = next(
            x["id"] for x in api.json("GET", "/api/secrets") if x["name"] == secret
        )

        variable = f"{t}_{side}_var"
        if not any(v["name"] == variable for v in api.json("GET", "/api/variables")):
            api.call(
                "POST",
                "/api/variables",
                body={"name": variable, "values": {"k": f"v-{side}"}},
            )
        s[f"{side}_variable"] = variable

        table = f"{t}_{side}_table"
        tables = api.json("GET", "/api/tables")
        if not any(x["name"] == table for x in tables):
            api.call(
                "POST",
                "/api/tables",
                body={"name": table, "columns": [{"name": "note", "type": "TEXT"}]},
            )
            tables = api.json("GET", "/api/tables")
        table_id = next(x["id"] for x in tables if x["name"] == table)
        rows = items_of(api.json("GET", f"/api/tables/{table_id}/rows")) or []
        if not rows:
            api.call(
                "POST",
                f"/api/tables/{table_id}/rows",
                body={"data": {"note": f"{side}-row"}},
            )
        s[f"{side}_table"], s[f"{side}_table_id"] = table, table_id

        summary = f"{run.tag} {side} seed case"
        cases = items_of(api.json("GET", "/api/cases", params={"limit": 100})) or []
        if not any(c["summary"] == summary for c in cases):
            api.call(
                "POST",
                "/api/cases",
                body={
                    "summary": summary,
                    "description": "RLS sweep seed",
                    "status": "new",
                    "priority": "low",
                    "severity": "low",
                },
            )
            cases = items_of(api.json("GET", "/api/cases", params={"limit": 100})) or []
        s[f"{side}_case"] = next(c["id"] for c in cases if c["summary"] == summary)

    api.workspace_id = s["workspace_b"]
    s["b_workflow"] = find_or_create_workflow(
        api,
        f"{run.tag}-b-noop",
        [{"ref": "noop", "action": "core.transform.reshape", "args": {"value": 1}}],
        None,
    )

    api.workspace_id = s["workspace_a"]
    field_name = f"{t}_field"
    fields = api.json("GET", "/api/case-fields")
    if not any(f.get("id") == field_name for f in items_of(fields) or fields):
        api.call("POST", "/api/case-fields", body={"name": field_name, "type": "TEXT"})
    s["a_field"] = field_name

    child_actions = [
        {
            "ref": "echo",
            "action": "core.transform.reshape",
            "args": {"value": "${{ TRIGGER }}"},
        }
    ]
    child_returns = "${{ ACTIONS.echo.result }}"
    s["child_alias"] = f"{t}_child_{definition_digest(child_actions, child_returns)}"
    s["child"] = find_or_create_workflow(
        api, f"{run.tag}-child", child_actions, child_returns, alias=s["child_alias"]
    )
    s["probe"] = find_or_create_workflow(
        api, f"{run.tag}-probe", probe_actions(s), PROBE_RETURNS
    )
    s["echo"] = find_or_create_workflow(
        api,
        f"{run.tag}-echo",
        [
            {
                "ref": "echo",
                "action": "core.transform.reshape",
                "args": {"value": "${{ TRIGGER }}"},
            }
        ],
        "${{ ACTIONS.echo.result }}",
    )
    s["trigger"] = find_or_create_workflow(
        api,
        f"{run.tag}-case-trigger",
        [
            {
                "ref": "echo",
                "action": "core.transform.reshape",
                "args": {"value": "${{ TRIGGER }}"},
            }
        ],
        "${{ ACTIONS.echo.result }}",
    )
    return s


def probe_actions(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Actions that read every executor-side tenant object from inside one run."""
    return [
        {
            "ref": "secret_ok",
            "action": "core.transform.reshape",
            "args": {
                "value": f"${{{{ SECRETS.{s['a_secret_name']}.K == '{SECRET_VALUE}' }}}}"
            },
        },
        {
            "ref": "variable",
            "action": "core.transform.reshape",
            "args": {"value": f"${{{{ VARS.{s['a_variable']}.k }}}}"},
            "depends_on": ["secret_ok"],
        },
        {
            "ref": "table_row",
            "action": "core.table.lookup",
            "args": {"table": s["a_table"], "column": "note", "value": "a-row"},
            "depends_on": ["variable"],
        },
        {
            "ref": "case",
            "action": "core.cases.get_case",
            "args": {"case_id": "${{ TRIGGER.case_id }}"},
            "depends_on": ["table_row"],
        },
        {
            "ref": "loop",
            "action": "core.transform.reshape",
            "for_each": "${{ for var.x in [1, 2, 3] }}",
            "args": {"value": "${{ var.x }}"},
            "depends_on": ["case"],
        },
        {
            "ref": "child",
            "action": "core.workflow.execute",
            "args": {
                "workflow_alias": s["child_alias"],
                "trigger_inputs": {"from": "probe"},
                "wait_strategy": "wait",
            },
            "depends_on": ["loop"],
        },
    ]


PROBE_RETURNS = {
    "secret_ok": "${{ ACTIONS.secret_ok.result }}",
    "variable": "${{ ACTIONS.variable.result }}",
    "table_note": "${{ ACTIONS.table_row.result.note }}",
    "case_summary": "${{ ACTIONS.case.result.summary }}",
    "loop": "${{ ACTIONS.loop.result }}",
    "child": "${{ ACTIONS.child.result }}",
}


# --------------------------------------------------------------------------- #
# Workflow helpers
# --------------------------------------------------------------------------- #


def wait_run(
    api: Api, match: Callable[[dict[str, Any]], bool], timeout: float = 180
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = (
            items_of(api.json("GET", "/api/workflow-executions", params={"limit": 100}))
            or []
        )
        for x in runs:
            if match(x) and x.get("status") in FINAL_RUN_STATES:
                return x
        time.sleep(3)
    raise CheckFailed(f"no finished run within {timeout:.0f}s")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def for_workflow(
    workflow_id: str, since: str, trigger_type: str
) -> Callable[[dict[str, Any]], bool]:
    """Match a run of `workflow_id` with `trigger_type` that started after `since`."""
    key = workflow_id.replace("-", "")[-12:]
    after = parse_time(since)

    def match(x: dict[str, Any]) -> bool:
        started = x.get("start_time")
        return (
            key in json.dumps(x).replace("-", "")
            and x.get("trigger_type") == trigger_type
            and started is not None
            and parse_time(str(started)) >= after
        )

    return match


def run_result(api: Api, exec_id: str) -> Any:
    """Return the run's final result from the compact event history."""
    compact = api.json(
        "GET", f"/api/workflow-executions/{quote(exec_id, safe='')}/compact"
    )
    api.run.http_log[-1]["response"] = "[compact events omitted]"
    events = compact.get("events", []) if isinstance(compact, dict) else []
    final = [e for e in events if e.get("action_ref") == "__workflow_completed__"]
    return {
        "result": final[-1].get("action_result") if final else None,
        "events": [
            {"ref": e.get("action_ref"), "status": e.get("status")} for e in events
        ],
        "raw": compact,
    }


def run_completed(
    api: Api, workflow_id: str, since: str, trigger_type: str, timeout: float = 180
) -> dict[str, Any]:
    done = wait_run(api, for_workflow(workflow_id, since, trigger_type), timeout)
    expect(done["status"] == "COMPLETED", f"run {done['id']} {done['status']}")
    return done


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_org_reads(run: Run, api: Api) -> Any:
    s = run.seeds
    names = {w["name"]: w["id"] for w in api.json("GET", "/api/workspaces", ws=None)}
    expect(
        names.get(f"{run.tag}-a") == s["workspace_a"]
        and names.get(f"{run.tag}-b") == s["workspace_b"],
        "workspace list is missing the seeded pair",
    )
    members = items_of(api.json("GET", "/api/organization/members", ws=None)) or []
    expect(len(members) >= 1, "organization member list is empty")
    # Membership comes from role assignments; an org owner who creates a
    # workspace is not listed, so this count is compared against the baseline only.
    ws_members = items_of(
        api.json("GET", f"/api/workspaces/{s['workspace_a']}/members", ws=None)
    )
    return {
        "workspaces": len(names),
        "org_members": len(members),
        "ws_a_members": len(ws_members or []),
    }


def check_workspace_reads(run: Run, api: Api) -> Any:
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    seen = {
        "secrets": any(
            x["name"] == s["a_secret_name"] for x in api.json("GET", "/api/secrets")
        ),
        "variables": any(
            x["name"] == s["a_variable"] for x in api.json("GET", "/api/variables")
        ),
        "tables": any(
            x["id"] == s["a_table_id"] for x in api.json("GET", "/api/tables")
        ),
        "table_rows": bool(
            items_of(api.json("GET", f"/api/tables/{s['a_table_id']}/rows"))
        ),
        "cases": any(
            c["id"] == s["a_case"]
            for c in items_of(api.json("GET", "/api/cases", params={"limit": 100}))
            or []
        ),
        "case_by_id": api.json("GET", f"/api/cases/{s['a_case']}")["id"] == s["a_case"],
        "workflows": any(
            w["id"] == s["probe"]
            for w in items_of(api.json("GET", "/api/workflows", params={"limit": 200}))
            or []
        ),
        "case_fields": any(
            f.get("id") == s["a_field"]
            for f in items_of(api.json("GET", "/api/case-fields"))
            or api.json("GET", "/api/case-fields")
        ),
    }
    missing = [k for k, v in seen.items() if not v]
    expect(not missing, f"seeded objects missing from API reads: {missing}")
    return seen


def check_manual_run(run: Run, api: Api) -> Any:
    """Executor-side reads: secret, variable, table row, case, loop, child workflow."""
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    started = api.json(
        "POST",
        "/api/workflow-executions",
        body={"workflow_id": s["probe"], "inputs": {"case_id": s["a_case"]}},
    )
    exec_id = started["wf_exec_id"]
    done = wait_run(api, lambda x: x["id"] == exec_id, 240)
    out = run_result(api, exec_id)
    (run.out / "evidence" / "probe-run-compact.json").write_text(
        json.dumps(redact(out["raw"]), indent=2, default=str)
    )
    expect(
        done["status"] == "COMPLETED", f"probe run {done['status']}: {out['events']}"
    )
    result = out["result"]
    expected = {
        "secret_ok": True,
        "variable": "v-a",
        "table_note": "a-row",
        "case_summary": f"{run.tag} a seed case",
        "loop": [1, 2, 3],
    }
    # The final event carries the whole result; without it nothing was verified.
    expect(isinstance(result, dict), f"probe run has no result to verify: {result!r}")
    wrong = {k: result.get(k) for k, v in expected.items() if result.get(k) != v}
    expect(not wrong, f"probe read wrong values: {wrong}")
    expect(
        result.get("child") == {"from": "probe"},
        f"child result {result.get('child')}",
    )
    return {"exec_id": exec_id, "status": done["status"], "result": result}


def webhook_offline(api: Api, base: str) -> None:
    """Take the webhook offline; its URL secret is its only credential."""
    r = api.call("PATCH", base, body={"status": "offline"}, ok=None)
    expect(
        r.status_code == 200,
        f"could not take the webhook offline: HTTP {r.status_code}",
    )


def check_webhook(run: Run, api: Api) -> Any:
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    base = f"/api/workflows/{s['echo']}/webhook"
    try:
        hook = api.call("GET", base, ok=None)
        if hook.status_code == 404:
            api.call("POST", base, body={"status": "online", "methods": ["POST"]})
        else:
            api.call("PATCH", base, body={"status": "online"})
        # Setup: an interrupted earlier run can leave an API key on this webhook.
        api.call("DELETE", f"{base}/api-key", ok=None)
        hook = api.json("GET", base)
        path = urlparse(hook["url"]).path
        path = path if path.startswith("/api") else "/api" + path
        since = now()
        r = httpx.post(run.target.origin + path, json={"qa": run.tag}, timeout=60)
        run.http_log.append(
            {
                "at": now(),
                "method": "POST",
                "path": "<webhook url>",
                "status": r.status_code,
            }
        )
        expect(r.status_code in (200, 201, 202), f"webhook POST HTTP {r.status_code}")
        done = run_completed(api, s["echo"], since, "webhook")
    finally:
        webhook_offline(api, base)
    return {"run": done["id"], "trigger_type": done.get("trigger_type")}


def check_webhook_api_key(run: Run, api: Api) -> Any:
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    base = f"/api/workflows/{s['echo']}/webhook"
    key = ""
    try:
        api.call("PATCH", base, body={"status": "online"})
        key = api.json("POST", f"{base}/api-key")["api_key"]
        hook = api.json("GET", base)
        path = urlparse(hook["url"]).path
        path = path if path.startswith("/api") else "/api" + path
        missing = httpx.post(
            run.target.origin + path, json={"qa": "no-key"}, timeout=60
        )
        expect(
            missing.status_code in DENIED,
            f"webhook without key HTTP {missing.status_code}",
        )
        since = now()
        r = httpx.post(
            run.target.origin + path,
            json={"qa": run.tag},
            headers={"x-tracecat-api-key": key},
            timeout=60,
        )
        expect(
            r.status_code in (200, 201, 202), f"webhook with key HTTP {r.status_code}"
        )
        done = run_completed(api, s["echo"], since, "webhook")
        # Revocation is a tenant-scoped write; a silent no-op would leave the key valid.
        api.call("POST", f"{base}/api-key/revoke")
        revoked = httpx.post(
            run.target.origin + path,
            json={"qa": "revoked"},
            headers={"x-tracecat-api-key": key},
            timeout=60,
        )
        expect(
            revoked.status_code in DENIED,
            f"webhook with revoked key HTTP {revoked.status_code}",
        )
    finally:
        del key
        # A revoked key keeps rejecting key-less requests; delete it so the
        # plain webhook check still works on the next run, then take it offline.
        try:
            api.call("DELETE", f"{base}/api-key", ok=None)
        finally:
            webhook_offline(api, base)
    return {
        "without_key": missing.status_code,
        "with_key": r.status_code,
        "revoked_key": revoked.status_code,
        "run": done["id"],
    }


def check_schedule(run: Run, api: Api) -> Any:
    s = run.seeds
    api.workspace_id = s["workspace_a"]

    def remove_echo_schedules() -> None:
        # A leftover schedule keeps firing every minute: fail if one can't be removed.
        # Listing by workflow also catches one whose create response was lost.
        found = api.json("GET", "/api/schedules", params={"workflow_id": s["echo"]})
        for old in items_of(found) or []:
            api.call("DELETE", f"/api/schedules/{old['id']}", ok=(200, 204))

    remove_echo_schedules()
    since = now()
    try:
        sched = api.json(
            "POST",
            "/api/schedules",
            body={
                "workflow_id": s["echo"],
                "every": "PT1M",
                "status": "online",
                "inputs": {"qa": "schedule"},
            },
        )
        done = run_completed(api, s["echo"], since, "scheduled", timeout=150)
    finally:
        remove_echo_schedules()
    return {
        "schedule": sched["id"],
        "run": done["id"],
        "trigger_type": done.get("trigger_type"),
    }


def check_case_trigger(run: Run, api: Api) -> Any:
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    path = f"/api/workflows/{s['trigger']}/case-trigger"
    body = {"status": "online", "event_types": ["case_created"], "tag_filters": []}
    since = now()
    try:
        api.call("POST", path, body=body)
        api.call(
            "POST",
            "/api/cases",
            body={
                "summary": f"{run.tag} trigger case {run.stamp}",
                "description": "RLS sweep case trigger",
                "status": "new",
                "priority": "low",
                "severity": "low",
                "fields": {s["a_field"]: "set-by-sweep"},
            },
        )
        cases = items_of(api.json("GET", "/api/cases", params={"limit": 50})) or []
        cid = next(
            c["id"]
            for c in cases
            if c["summary"] == f"{run.tag} trigger case {run.stamp}"
        )
        field_values = [
            f
            for f in api.json("GET", f"/api/cases/{cid}").get("fields", [])
            if f.get("id") == s["a_field"]
        ]
        expect(
            bool(field_values) and field_values[0].get("value") == "set-by-sweep",
            f"case field read back {field_values}",
        )
        done = run_completed(api, s["trigger"], since, "case")
    finally:
        api.call("POST", path, body={**body, "status": "offline"}, ok=(200, 201, 204))
    return {"case": cid, "run": done["id"], "trigger_type": done.get("trigger_type")}


def check_case_search(run: Run, api: Api) -> Any:
    """Synchronous case search. It does not exercise background search indexing."""
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    hits = items_of(
        api.json(
            "GET",
            "/api/cases/search",
            params={"search_term": f"{run.tag} a seed case", "limit": 20},
        )
    )
    expect(
        any(c.get("id") == s["a_case"] for c in hits or []),
        "case search misses the seeded case",
    )
    return {"hits": len(hits or [])}


def stream_turn(
    api: Api,
    session_id: str,
    text: str,
    label: str,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stream one turn (or an approval continuation when `body` is given)."""
    t = api.run.target
    body = body or {
        "kind": "vercel",
        "model": t.model["name"],
        "model_provider": t.model["provider"],
        "message": {
            "id": str(uuid4()),
            "role": "user",
            "parts": [{"type": "text", "text": text}],
        },
    }
    approvals: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    done = False
    started = time.monotonic()
    with api.client.stream(
        "POST",
        f"/api/agent/sessions/{session_id}/messages",
        params={"workspace_id": api.workspace_id},
        json=body,
        timeout=600,
    ) as r:
        if r.status_code >= 400:
            r.read()
            raise CheckFailed(f"{label}: HTTP {r.status_code} {r.text[:300]}")
        for line in r.iter_lines():
            if line == "data: [DONE]":
                done = True
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                kind = str(data.get("type", ""))
                if kind == "data-approval-request" and isinstance(
                    data.get("data"), list
                ):
                    approvals.extend(
                        {
                            "tool_call_id": a.get("tool_call_id"),
                            "tool": a.get("tool_name"),
                        }
                        for a in data["data"]
                    )
                if kind.startswith("tool-") or kind in (
                    "error",
                    "finish",
                    "text-delta",
                ):
                    events.append(
                        {
                            "type": kind,
                            "tool": data.get("toolName"),
                            "call": data.get("toolCallId"),
                            # Failed tools still emit tool-output-available, with errorText.
                            "tool_error": (data.get("output") or {}).get("errorText")
                            if kind == "tool-output-available"
                            and isinstance(data.get("output"), dict)
                            else None,
                            "delta": data.get("delta"),
                            "error": data.get("errorText"),
                        }
                    )
    names = {e["call"]: e["tool"] for e in events if e["call"] and e["tool"]}
    (api.run.out / "evidence" / f"{label}-stream.json").write_text(
        json.dumps(events, indent=2)
    )
    summary = {
        "done": done,
        "seconds": round(time.monotonic() - started, 1),
        "tools": [
            (e["type"], e["tool"]) for e in events if e["type"].startswith("tool-")
        ],
        "errors": [e["error"] for e in events if e["type"] == "error"],
        "reply": "".join(e["delta"] or "" for e in events if e["type"] == "text-delta")[
            :200
        ],
        "approvals": approvals,
        # Output events carry only the call id; name them from the input events.
        "outputs": sorted(
            {
                names.get(e["call"], "?")
                for e in events
                if e["type"] == "tool-output-available" and not e["tool_error"]
            }
        ),
        "tool_errors": [
            f"{names.get(e['call'], '?')}: {str(e['tool_error'])[:200]}"
            for e in events
            if e["tool_error"]
        ],
    }
    expect(done and not summary["errors"], f"{label}: turn failed {summary['errors']}")
    expect(not summary["tool_errors"], f"{label}: tool failed {summary['tool_errors']}")
    return summary


def find_catalog_id(api: Api) -> str:
    """Return the org catalog row for the target's model."""
    model = api.run.target.model
    catalog = api.json(
        "GET",
        "/api/organization/agent-catalog",
        ws=None,
        params={"provider": model["provider"], "limit": 100},
    )
    rows = [r for r in items_of(catalog) or [] if r.get("model_name") == model["name"]]
    expect(bool(rows), f"no org catalog row for {model['provider']}/{model['name']}")
    return rows[0]["id"]


def check_agent_turn(run: Run, api: Api) -> Any:
    s, t = run.seeds, run.target
    api.workspace_id = s["workspace_a"]
    slug = f"{run.tag}-agent"
    presets = items_of(api.json("GET", "/api/agent/presets")) or []
    preset = next((p for p in presets if p.get("slug") == slug), None)
    if preset is not None:
        expect(
            OWNER_MARKER in (preset.get("description") or ""),
            f"preset {slug!r} exists but was not created by this script",
        )
    if preset is None:
        body: dict[str, Any] = {
            "name": slug,
            "slug": slug,
            "description": "RLS sweep probe; safe to delete.",
            "model_name": t.model["name"],
            "model_provider": t.model["provider"],
            "instructions": "Use the actual tools. Never invent tool results. Keep prose short.",
            "use_in_chat": True,
            "enable_thinking": False,
            "enable_internet_access": False,
            "retries": 3,
            "actions": ["core.transform.reshape"],
            "namespaces": [],
            "tool_approvals": {},
            "skills": [],
            "agents": {"subagents": []},
        }
        body["catalog_id"] = t.catalog_id or find_catalog_id(api)
        preset = api.json("POST", "/api/agent/presets", body=body)
    s["preset"] = preset["id"]
    session = api.json(
        "POST",
        "/api/agent/sessions",
        body={
            "title": f"{run.tag} agent {run.stamp}",
            "entity_type": "agent_preset",
            "entity_id": preset["id"],
            "agent_preset_id": preset["id"],
            **t.harness,
        },
    )
    out = stream_turn(
        api,
        session["id"],
        f'Call core.transform.reshape once with value {{"qa": "{run.tag}"}} and reply with its '
        "output only. Do not call any other tool.",
        "agent-turn",
    )
    expect(
        "core.transform.reshape" in out["outputs"],
        f"core.transform.reshape produced no output: {out['outputs']}",
    )
    return {"session": session["id"], **out}


def is_reshape(tool: str | None) -> bool:
    """Approval requests name the tool through the MCP proxy, tool events don't."""
    return tool in ("core.transform.reshape", "mcp__tracecat__core__transform__reshape")


def check_approval(run: Run, api: Api) -> Any:
    """A tool that needs approval pauses the turn; approving it resumes the run."""
    s, t = run.seeds, run.target
    api.workspace_id = s["workspace_a"]
    slug = f"{run.tag}-approval-agent"
    presets = items_of(api.json("GET", "/api/agent/presets")) or []
    preset = next((p for p in presets if p.get("slug") == slug), None)
    if preset is None:
        preset = api.json(
            "POST",
            "/api/agent/presets",
            body={
                "name": slug,
                "slug": slug,
                "description": "RLS sweep probe (approval); safe to delete.",
                "model_name": t.model["name"],
                "model_provider": t.model["provider"],
                "catalog_id": t.catalog_id or find_catalog_id(api),
                "instructions": "Use the actual tools. Keep prose short.",
                "use_in_chat": True,
                "enable_thinking": False,
                "enable_internet_access": False,
                "retries": 3,
                "actions": ["core.transform.reshape"],
                "namespaces": [],
                "tool_approvals": {"core.transform.reshape": True},
                "skills": [],
                "agents": {"subagents": []},
            },
        )
    expect(
        OWNER_MARKER in (preset.get("description") or ""),
        f"preset {slug!r} exists but was not created by this script",
    )
    session = api.json(
        "POST",
        "/api/agent/sessions",
        body={
            "title": f"{run.tag} approval {run.stamp}",
            "entity_type": "agent_preset",
            "entity_id": preset["id"],
            "agent_preset_id": preset["id"],
            **t.harness,
        },
    )
    paused = stream_turn(
        api,
        session["id"],
        f'Call core.transform.reshape once with value {{"qa": "{run.tag}"}}. '
        "Do not call any other tool.",
        "approval-request",
    )
    expect(bool(paused["approvals"]), "turn did not pause for approval")
    expect(
        "core.transform.reshape" not in paused["outputs"],
        f"tool ran before it was approved: outputs {paused['outputs']}",
    )
    # Approve only the call this check asks for; anything else is unexpected.
    unexpected = [a for a in paused["approvals"] if not is_reshape(a["tool"])]
    expect(not unexpected, f"unexpected approval requests: {unexpected}")
    decisions = [
        {"tool_call_id": a["tool_call_id"], "action": "approve"}
        for a in paused["approvals"]
    ]
    resumed = stream_turn(
        api,
        session["id"],
        "",
        "approval-continue",
        body={"kind": "continue", "source": "inbox", "decisions": decisions},
    )
    expect(
        "core.transform.reshape" in resumed["outputs"],
        f"approved tool produced no output after the run resumed: {resumed['outputs']}",
    )
    return {"session": session["id"], "approved": len(decisions), **resumed}


def check_chat(run: Run, api: Api, kind: str) -> Any:
    s, t = run.seeds, run.target
    api.workspace_id = s["workspace_a"]
    entity = s["workspace_a"] if kind == "copilot" else s["a_case"]
    session = api.json(
        "POST",
        "/api/agent/sessions",
        body={
            "title": f"{run.tag} {kind} {run.stamp}",
            "entity_type": kind,
            "entity_id": entity,
            **t.harness,
        },
    )
    return {
        "session": session["id"],
        **stream_turn(
            api, session["id"], "Reply with the single word: pong", f"chat-{kind}"
        ),
    }


def check_channel(run: Run, api: Api) -> Any:
    """Signed Slack events reach the public endpoint and resolve their token.

    Covers the synchronous path: token lookup without a user, signature check,
    `url_verification` and the `event_callback` acknowledgement. The background
    processing that follows is NOT covered: with a synthetic bot token it stops
    at Slack's API before resolving the session.
    """
    s = run.seeds
    api.workspace_id = s["workspace_a"]
    if "preset" not in s:
        raise CheckFailed("needs the agent preset from agent_turn")
    # Tokens from earlier runs carry signing secrets this run doesn't know.
    for old in items_of(api.json("GET", "/api/agent/channels/tokens")) or []:
        if old.get("agent_preset_id") == s["preset"]:
            api.call("DELETE", f"/api/agent/channels/tokens/{old['id']}", ok=(200, 204))
    signing_secret = secrets.token_hex(32)
    token = api.json(
        "POST",
        "/api/agent/channels/tokens",
        body={
            "agent_preset_id": s["preset"],
            "channel_type": "slack",
            "config": {
                "slack_bot_token": "xoxb-synthetic",
                "slack_signing_secret": signing_secret,
            },
        },
    )
    path = urlparse(token["endpoint_url"]).path
    url = run.target.origin + (path if path.startswith("/api") else "/api" + path)

    def signed(payload: dict[str, Any]) -> httpx.Response:
        raw = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = (
            "v0="
            + hmac.new(
                signing_secret.encode(),
                f"v0:{ts}:".encode() + raw,
                hashlib.sha256,
            ).hexdigest()
        )
        return httpx.post(
            url,
            content=raw,
            timeout=60,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": ts,
                "x-slack-signature": sig,
            },
        )

    try:
        return _channel_events(signed)
    finally:
        api.call("DELETE", f"/api/agent/channels/tokens/{token['id']}", ok=(200, 204))


def _channel_events(signed: Callable[[dict[str, Any]], httpx.Response]) -> Any:
    verify = signed(
        {"type": "url_verification", "challenge": "rls-sweep-challenge", "token": "x"}
    )
    expect(
        verify.status_code == 200 and "rls-sweep-challenge" in verify.text,
        f"url_verification HTTP {verify.status_code}",
    )
    ts = str(int(time.time()))
    event = signed(
        {
            "type": "event_callback",
            "team_id": "T0",
            "event_id": f"Ev{ts}",
            "event": {
                "type": "app_mention",
                "user": "U0",
                "text": "hi",
                "channel": "C0",
                "ts": f"{ts}.0001",
            },
        }
    )
    expect(
        event.status_code == 200,
        f"event_callback HTTP {event.status_code}",
    )
    return {"url_verification": verify.status_code, "event_callback": event.status_code}


def list_all(api: Api, path: str) -> list[dict[str, Any]]:
    """Follow cursor pagination for an org-level list route."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = api.json("GET", path, ws=None, params=params)
        out.extend(items_of(page) or [])
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not cursor:
            return out


def check_isolation(run: Run, api: Api) -> Any:
    """A key bound to workspace A must see none of B's objects."""
    s = run.seeds
    a = s["workspace_a"]
    sa_base = f"/api/workspaces/{a}/service-accounts"
    # Earlier runs' probe accounts: disable any still enabled.
    for old in list_all(api, sa_base):
        if (
            old.get("name", "").startswith(f"{run.tag}-a-only-")
            and OWNER_MARKER in (old.get("description") or "")
            and not old.get("disabled_at")
        ):
            api.call("POST", f"{sa_base}/{old['id']}/disable", ws=None, ok=(200, 204))
    scopes = api.json("GET", f"/api/workspaces/{a}/service-accounts/scopes", ws=None)
    wanted = {"case:read", "table:read", "workflow:read", "secret:read"}
    scope_ids = [x["id"] for x in scopes["items"] if x["name"] in wanted]
    # A missing scope would turn every probe for that object type into a vacuous 403.
    expect(
        len(scope_ids) == len(wanted),
        f"found {len(scope_ids)}/{len(wanted)} read scopes",
    )
    sa = api.json(
        "POST",
        f"/api/workspaces/{a}/service-accounts",
        ws=None,
        body={
            "name": f"{run.tag}-a-only-{run.stamp}",
            "description": "RLS sweep isolation probe: workspace A only",
            "scope_ids": scope_ids,
        },
    )
    key_api = Api(
        run, headers={"Authorization": f"Bearer {sa['issued_api_key']['raw_key']}"}
    )
    del sa["issued_api_key"]
    sa_id = sa["service_account"]["id"]
    try:
        results = _isolation_probes(run, key_api)
    finally:
        api.call("POST", f"{sa_base}/{sa_id}/disable", ws=None, ok=None)
    disabled = key_api.call("GET", f"/api/cases/{s['a_case']}", ws=a, ok=None)
    key_api.client.close()
    results.append(
        {
            "check": "disabled account's key rejected",
            "status": disabled.status_code,
            "leaked": [],
            "result": "PASS" if disabled.status_code in DENIED else "FAIL",
        }
    )
    (run.out / "evidence" / "isolation.json").write_text(json.dumps(results, indent=2))
    failed = [x for x in results if x["result"] == "FAIL"]
    expect(
        not failed,
        f"{len(failed)}/{len(results)} isolation probes failed: {failed[:3]}",
    )
    return {"probes": len(results), "passed": len(results) - len(failed)}


def _isolation_probes(run: Run, key_api: Api) -> list[dict[str, Any]]:
    s = run.seeds
    a, b = s["workspace_a"], s["workspace_b"]
    results: list[dict[str, Any]] = []

    def probe(
        name: str, path: str, ws: str, ok: tuple[int, ...], leak: list[str]
    ) -> None:
        r = key_api.call("GET", path, ws=ws, ok=None)
        leaked = [x for x in leak if x in r.text]
        results.append(
            {
                "check": name,
                "status": r.status_code,
                "leaked": leaked,
                "result": "PASS" if r.status_code in ok and not leaked else "FAIL",
            }
        )

    markers = [SECRET_VALUE, "b-row"]
    # Positive controls: the key can read each object type in its own workspace.
    controls = {
        "case": f"/api/cases/{s['a_case']}",
        "table": f"/api/tables/{s['a_table_id']}",
        "workflow": f"/api/workflows/{s['probe']}",
        "secret": f"/api/secrets/{s['a_secret_name']}",
    }
    for name, path in controls.items():
        probe(f"A {name} readable (control)", path, a, (200,), [])
    targets = {
        "case": f"/api/cases/{s['b_case']}",
        "table": f"/api/tables/{s['b_table_id']}",
        "table rows": f"/api/tables/{s['b_table_id']}/rows",
        "workflow": f"/api/workflows/{s['b_workflow']}",
        "secret": f"/api/secrets/{s['b_secret_name']}",
    }
    for name, path in targets.items():
        probe(f"B {name} with workspace_id=B", path, b, DENIED, markers)
        probe(f"B {name} by id with workspace_id=A", path, a, DENIED, markers)
    b_ids = [
        s["b_case"],
        s["b_table_id"],
        s["b_workflow"].replace("wf-", ""),
        s["b_secret_id"],
    ]
    for name in ("cases", "tables", "workflows", "secrets"):
        probe(
            f"A {name} list has no B ids",
            f"/api/{name}",
            a,
            # The key holds all four read scopes (asserted above), so lists must load.
            (200,),
            b_ids + markers,
        )
        probe(
            f"B {name} list with workspace_id=B",
            f"/api/{name}",
            b,
            DENIED,
            b_ids + markers,
        )
    return results


# --------------------------------------------------------------------------- #
# OpenAPI GET sweep
# --------------------------------------------------------------------------- #


def sweep_gets(run: Run, api: Api) -> dict[str, Any]:
    """Call every parameter-free GET route; record status and item count."""
    spec = (
        httpx.get(run.target.origin + "/api/openapi.json", timeout=60)
        .raise_for_status()
        .json()
    )
    (run.out / "evidence" / "openapi.json").write_text(json.dumps(spec))
    fill = {"workflow_id": run.seeds["probe"]}
    rows: dict[str, Any] = {}
    for path, ops in sorted(spec.get("paths", {}).items()):
        op = ops.get("get")
        if op is None or GET_SWEEP_SKIP.search(path):
            continue
        if re.search(r"\{(?!workspace_id\})", path):
            continue
        required = [
            p["name"]
            for p in op.get("parameters", [])
            if p.get("required") and p.get("in") == "query"
        ]
        if any(name not in fill and name != "workspace_id" for name in required):
            continue
        params = {name: fill[name] for name in required if name in fill}
        concrete = path.replace("{workspace_id}", run.seeds["workspace_a"])
        try:
            r = api.call(
                "GET", "/api" + concrete, ws=None, ok=None, params=params, timeout=30
            )
            api.run.http_log[-1]["response"] = "[get sweep body omitted]"
            try:
                body = r.json()
            except ValueError:
                body = None
            items = items_of(body)
            rows[path] = {
                "status": r.status_code,
                "count": len(items) if items is not None else None,
                "empty": (items is not None and len(items) == 0)
                or body in ({}, [], None, ""),
            }
        except httpx.HTTPError as e:
            rows[path] = {
                "status": None,
                "count": None,
                "empty": None,
                "error": type(e).__name__,
            }
    return rows


# --------------------------------------------------------------------------- #
# SQL probe (runs inside the cluster as the app's own login)
# --------------------------------------------------------------------------- #

SQL_PROBE = r'''
"""Read-only RLS probe. Every transaction is READ ONLY and only sets
transaction-local settings. Prints JSON."""
import asyncio, json, sys, uuid
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from tracecat.db.engine import _get_db_uri, _token_provider
from tracecat.db import tenant_rls as t
from tracecat.identifiers.workflow import WorkspaceUUID

ids = json.loads(sys.argv[1])
A, B = ids["workspace_a"], ids["workspace_b"]
provider = _token_provider()
cargs = {"password": asyncio.run(provider())} if provider is not None else {}
engine = create_engine(_get_db_uri(driver="psycopg"), poolclass=NullPool, connect_args=cargs)
SET = text("SELECT set_config('app.rls_bypass', :bypass, true), set_config('app.current_org_id', :org, true), "
           "set_config('app.current_workspace_id', :ws, true), set_config('app.current_user_id', '', true)")
out = {}

def ctx(c, bypass="off", org="", ws=""):
    c.execute(text("SET TRANSACTION READ ONLY"))
    c.execute(SET, {"bypass": bypass, "org": org, "ws": ws})

with engine.connect() as c:
    with c.begin():
        ctx(c, bypass="on")
        row = c.execute(text("SELECT current_user, r.rolsuper, r.rolbypassrls FROM pg_roles r "
                             "WHERE r.rolname = current_user")).one()
        out["login"] = {"user": row[0], "superuser": row[1], "bypassrls": row[2]}
        orgs = dict(c.execute(text("SELECT id::text, organization_id::text FROM workspace WHERE id IN "
                                   "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": A, "b": B}).tuples().all())
        cols = c.execute(text(
            "SELECT table_name, bool_or(column_name = 'workspace_id'), bool_or(column_name = 'organization_id') "
            "FROM information_schema.columns WHERE table_schema = 'public' GROUP BY table_name")).tuples().all()
        tier = c.execute(text(
            "SELECT (SELECT tier_id::text FROM organization_tier WHERE organization_id = CAST(:o AS uuid)), "
            "(SELECT id::text FROM tier WHERE is_default AND is_active LIMIT 1)"), {"o": orgs[A]}).one()
        out["org_tier"] = {"assigned": tier[0], "default": tier[1],
                           "assigned_differs_from_default": tier[0] is not None and tier[0] != tier[1]}
    org = orgs[A]
    tenant = set(t.ALL_TENANT_RLS_TABLES)
    # Policy metadata for every registered tenant table, independent of row counts:
    # an empty table with RLS off would otherwise pass every visibility check.
    with c.begin():
        ctx(c, bypass="on")
        meta = {r[0]: r[1:] for r in c.execute(text(
            "SELECT c.relname, c.relrowsecurity, (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')")).tuples().all()}
    policy_findings = []
    for n in sorted(tenant):
        if n not in meta:
            policy_findings.append({"table": n, "issue": "registered tenant table missing"})
        elif not meta[n][0]:
            policy_findings.append({"table": n, "issue": "RLS disabled"})
        elif not meta[n][1]:
            policy_findings.append({"table": n, "issue": "no RLS policy"})
    out["tenant_tables_registered"] = len(tenant)
    ws_tables = sorted(n for n, has_ws, _ in cols if n in tenant and has_ws)
    org_tables = sorted(n for n, has_ws, has_org in cols if n in tenant and has_org and not has_ws)
    # Tables that inherit tenancy through a parent row (no tenant column of their own).
    parent_tables = sorted(n for n, has_ws, has_org in cols if n in tenant and not has_ws and not has_org)
    dyn = []
    for w in (A, B):
        short = WorkspaceUUID.new(w).short()
        with c.begin():
            ctx(c, bypass="on")
            for schema, rel, rls, force, owner in c.execute(text(
                "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN (:t, :f) AND c.relkind IN ('r', 'p')"),
                {"t": f"tables_{short}", "f": f"custom_fields_{short}"}).tuples().all():
                dyn.append({"workspace": w, "schema": schema, "table": rel, "rls": rls, "force": force, "owner": owner})
    out["dynamic_tables"] = dyn

    def counts(bypass, org_id, ws_id, owner_ws):
        res = {}
        with c.begin():
            ctx(c, bypass=bypass, org=org_id, ws=ws_id)
            for n in ws_tables:
                res[n] = c.execute(text(f'SELECT count(*) FROM "{n}" WHERE workspace_id = CAST(:w AS uuid)'),
                                   {"w": owner_ws}).scalar_one()
            for d in dyn:
                if d["workspace"] == owner_ws:
                    res[f'{d["schema"]}.{d["table"]}'] = c.execute(
                        text(f'SELECT count(*) FROM "{d["schema"]}"."{d["table"]}"')).scalar_one()
        return res

    def org_counts(bypass, org_id):
        res = {}
        with c.begin():
            ctx(c, bypass=bypass, org=org_id)
            for n in org_tables:
                res[n] = c.execute(text(f'SELECT count(*) FROM "{n}" WHERE organization_id = CAST(:o AS uuid)'),
                                   {"o": org}).scalar_one()
        return res

    findings = list(policy_findings)
    pure_ws = t.WORKSPACE_POLICY_TABLES | t.SPECIAL_WORKSPACE_POLICY_TABLES
    for owner, other in ((A, B), (B, A)):
        views = {
            "bypass": counts("on", "", "", owner),
            "own": counts("off", org, owner, owner),
            "other_workspace": counts("off", org, other, owner),
            "org_only": counts("off", org, "", owner),
            "none": counts("off", "", "", owner),
        }
        out[f"workspace_{'a' if owner == A else 'b'}"] = views
        for n, total in views["bypass"].items():
            if views["own"][n] != total:
                findings.append({"table": n, "workspace": owner, "issue": "own workspace sees fewer rows than bypass",
                                 "bypass": total, "own": views["own"][n]})
            if views["other_workspace"][n]:
                findings.append({"table": n, "workspace": owner, "issue": "other workspace sees rows",
                                 "count": views["other_workspace"][n]})
            if views["none"][n]:
                findings.append({"table": n, "workspace": owner, "issue": "no context sees rows",
                                 "count": views["none"][n]})
            # Org-optional tables admit org-only sessions by design; pure workspace tables must not.
            # Dynamic tables ("schema.table") carry workspace policies too.
            if (n in pure_ws or "." in n) and views["org_only"][n]:
                findings.append({"table": n, "workspace": owner, "issue": "org-only context sees workspace rows",
                                 "count": views["org_only"][n]})
    org_views = {"bypass": org_counts("on", ""), "own": org_counts("off", org),
                 "foreign_org": org_counts("off", str(uuid.uuid4())), "none": org_counts("off", "")}
    out["organization"] = org_views
    for n, total in org_views["bypass"].items():
        if org_views["own"][n] != total:
            findings.append({"table": n, "issue": "own org sees fewer rows than bypass", "bypass": total,
                             "own": org_views["own"][n]})
        for view in ("foreign_org", "none"):
            if org_views[view][n]:
                findings.append({"table": n, "issue": f"{view} sees org rows", "count": org_views[view][n]})
    # Child tables with no tenant column of their own -> (foreign key, parent table).
    PARENTS = {
        "service_account_api_key": ("service_account_id", "service_account"),
        "service_account_scope": ("service_account_id", "service_account"),
        "agent_tag_link": ("tag_id", "agent_tag"),
        "skill_tag_link": ("tag_id", "skill_tag"),
    }
    unmapped = [n for n in parent_tables if n not in PARENTS]
    for n in unmapped:
        findings.append({"table": n, "issue": "parent-tenanted table not covered by the probe"})

    def child_counts(bypass, org_id, ws_id, parent_ids):
        res = {}
        with c.begin():
            ctx(c, bypass=bypass, org=org_id, ws=ws_id)
            for n in parent_tables:
                if n in PARENTS:
                    fk = PARENTS[n][0]
                    ids = parent_ids[n]
                    res[n] = c.execute(text(f'SELECT count(*) FROM "{n}" WHERE "{fk}" = ANY(CAST(:ids AS uuid[]))'),
                                       {"ids": ids}).scalar_one() if ids else 0
        return res

    parent_out = {}
    for owner, other in ((A, B), (B, A)):
        # Parent ids come from a bypass read, so each view below tests only the child table's policy.
        with c.begin():
            ctx(c, bypass="on")
            parent_ids = {n: [str(x) for x in c.execute(text(
                f'SELECT id FROM "{PARENTS[n][1]}" WHERE workspace_id = CAST(:w AS uuid)'), {"w": owner}).scalars()]
                for n in parent_tables if n in PARENTS}
        views = {
            "bypass": child_counts("on", "", "", parent_ids),
            "own": child_counts("off", org, owner, parent_ids),
            "other_workspace": child_counts("off", org, other, parent_ids),
            "org_only": child_counts("off", org, "", parent_ids),
            "none": child_counts("off", "", "", parent_ids),
            "foreign_org": child_counts("off", str(uuid.uuid4()), str(uuid.uuid4()), parent_ids),
        }
        parent_out[f"workspace_{'a' if owner == A else 'b'}"] = views
        for n, total in views["bypass"].items():
            if views["own"][n] != total:
                findings.append({"table": n, "workspace": owner, "issue": "own workspace sees fewer rows than bypass",
                                 "bypass": total, "own": views["own"][n]})
            # Tag links follow workspace-only parents; service-account children
            # follow an org-optional parent and admit org-only sessions by design.
            denied = ["other_workspace", "none", "foreign_org"]
            if PARENTS[n][1] in pure_ws:
                denied.append("org_only")
            for view in denied:
                if views[view][n]:
                    findings.append({"table": n, "workspace": owner, "issue": f"{view} sees rows",
                                     "count": views[view][n]})
    out["parent_tenanted"] = parent_out
    out["unexercised_child_tables"] = sorted(
        n for n in parent_tables if n in PARENTS
        and not any(v["bypass"].get(n) for v in parent_out.values())
    )
    for d in dyn:
        if not (d["rls"] and d["force"]):
            findings.append({"table": f'{d["schema"]}.{d["table"]}', "issue": "dynamic table without RLS+FORCE"})
    out["tables_checked"] = {"workspace": len(ws_tables), "org": len(org_tables),
                             "parent_tenanted": len(parent_tables), "dynamic": len(dyn)}
    out["findings"] = findings
engine.dispose()
print(json.dumps(out, default=str))
'''


def run_sql_probe(run: Run) -> dict[str, Any]:
    spec = run.target.sql
    assert spec is not None
    kind, _, where = spec.partition(":")
    ids = json.dumps({k: run.seeds[k] for k in ("workspace_a", "workspace_b")})
    if kind == "kubectl":
        namespace, deploy, container = (where.split("/") + ["", "", ""])[:3]
        cmd = [
            "kubectl",
            "-n",
            namespace or "tracecat",
            "exec",
            "-i",
            f"deploy/{deploy or 'tracecat-api'}",
            "-c",
            container or "api",
            "--",
            "python",
            "-",
            ids,
        ]
    elif kind == "docker":
        cmd = ["docker", "exec", "-i", where, "python", "-", ids]
    else:
        raise ValueError(f"unknown --sql {spec!r}")
    proc = subprocess.run(
        cmd, input=SQL_PROBE, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        # The probe imports the app's engine module, whose trace logs can carry the DB URI.
        (run.out / "evidence" / "sql-stderr.txt").write_text(
            scrub(proc.stderr[-20000:])
        )
    expect(
        proc.returncode == 0,
        scrub(f"SQL probe exit {proc.returncode}: {proc.stderr.strip()[-400:]}"),
    )
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    (run.out / "evidence" / "sql.json").write_text(json.dumps(data, indent=2))
    return data


def check_sql(run: Run) -> Any:
    data = run_sql_probe(run)
    login = data["login"]
    expect(
        not login["superuser"] and not login["bypassrls"],
        f"app login bypasses RLS: {login}",
    )
    checked = data["tables_checked"]
    # A probe that inspected nothing is not a pass.
    expect(
        data["tenant_tables_registered"] > 0
        and checked["workspace"] > 0
        and checked["org"] > 0,
        f"SQL probe inspected too little: {checked}",
    )
    expect(
        not data["findings"],
        f"{len(data['findings'])} policy findings: {data['findings'][:3]}",
    )
    return {
        "login": login,
        "tenant_tables_registered": data["tenant_tables_registered"],
        "tables_checked": checked,
        # Child tables with no rows in the QA pair: only their RLS metadata was checked.
        "unexercised_child_tables": data["unexercised_child_tables"],
        "org_tier": data["org_tier"],
    }


def check_logs(run: Run) -> Any:
    """Scan service logs since the run started for RLS error signatures.

    Saves only the matched signature per hit unless `--keep-log-lines` is set:
    raw lines can carry tenant data.
    """
    spec = run.target.logs
    assert spec is not None
    kind, _, where = spec.partition(":")
    commands: dict[str, list[str]]
    if kind == "kubectl":
        commands = {
            deploy: [
                "kubectl",
                "-n",
                where or "tracecat",
                "logs",
                f"deploy/{deploy}",
                "--all-containers",
                "--all-pods=true",
                f"--since-time={run.started}",
            ]
            for deploy in STAGING_DEPLOYMENTS
        }
    elif kind == "docker":
        commands = {
            svc: ["docker", "logs", "--since", run.started, f"{where}-{svc}-1"]
            for svc in LOCAL_SERVICES
        }
    else:
        raise ValueError(f"unknown --logs {spec!r}")
    hits: dict[str, list[dict[str, str]]] = {}
    unreadable: dict[str, str] = {}
    for name, cmd in commands.items():
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            # An empty scan of logs we couldn't read is not a clean result.
            unreadable[name] = scrub(proc.stderr.strip())[-200:]
            continue
        # docker writes container stderr to its own stderr.
        lines = (proc.stdout + (proc.stderr if kind == "docker" else "")).splitlines()
        hits[name] = [
            {
                "signature": m.group(0),
                **({"line": scrub(ln)[:1000]} if run.target.keep_log_lines else {}),
            }
            for ln in lines
            if (m := LOG_SIGNATURES.search(ln))
        ]
    (run.out / "evidence" / "log-hits.json").write_text(
        json.dumps({"hits": hits, "unreadable": unreadable}, indent=2)
    )
    expect(not unreadable, f"could not read logs for {sorted(unreadable)}")
    bad = {k: len(v) for k, v in hits.items() if v}
    expect(not bad, f"RLS signatures in logs: {bad}")
    return {"services": len(hits)}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

CHECKS: list[tuple[str, str, str]] = [
    (
        "org_reads",
        "H14 org-level reads",
        "workspace list has the pair; org + workspace member lists non-empty",
    ),
    (
        "workspace_reads",
        "H10/H14 workspace reads",
        "every seeded object is listed and readable in workspace A",
    ),
    (
        "manual_run",
        "H9/H10/H11 executor reads, loop, child workflow",
        "probe run COMPLETED with secret, variable, row, case, loop, child values",
    ),
    ("webhook", "H2 webhook", "webhook POST accepted and its run COMPLETED"),
    (
        "webhook_api_key",
        "H2 webhook with API key",
        "no key → denied; key → run COMPLETED",
    ),
    ("schedule", "H8 schedule", "a 1-minute schedule starts a run that COMPLETES"),
    (
        "case_trigger",
        "H6 case-trigger dispatch",
        "case field read back; case_created trigger run COMPLETED",
    ),
    (
        "case_search",
        "case search (synchronous; background indexing not covered)",
        "case search finds the seeded case",
    ),
    (
        "agent_turn",
        "H13/H23 agent turn",
        "turn finishes with a tool-output event and no errors",
    ),
    (
        "approval",
        "H12 approval pause + resume",
        "tool needing approval pauses the turn; approving resumes it and the tool runs",
    ),
    ("chat_workspace", "H13 workspace chat", "turn finishes with no errors"),
    ("chat_case", "H13 case chat", "turn finishes with no errors"),
    (
        "channel",
        "H1 public channel endpoint (sync path only; background not covered)",
        "signed url_verification 200 + challenge; event_callback 200",
    ),
    (
        "isolation",
        "cross-workspace isolation",
        "A-only key: A control 200; every B object denied and absent from lists",
    ),
    (
        "sql",
        "SQL policies as the app login",
        "own workspace sees all its rows; other workspace / no context see none",
    ),
    ("logs", "service logs", "no RLS error signatures since the run started"),
]


def run_checks(
    run: Run, api: Api, only: set[str] | None, skip: set[str]
) -> dict[str, Any]:
    (run.out / "evidence").mkdir(parents=True, exist_ok=True)
    fns: dict[str, Callable[[], Any]] = {
        "org_reads": lambda: check_org_reads(run, api),
        "workspace_reads": lambda: check_workspace_reads(run, api),
        "manual_run": lambda: check_manual_run(run, api),
        "webhook": lambda: check_webhook(run, api),
        "webhook_api_key": lambda: check_webhook_api_key(run, api),
        "schedule": lambda: check_schedule(run, api),
        "case_trigger": lambda: check_case_trigger(run, api),
        "case_search": lambda: check_case_search(run, api),
        "agent_turn": lambda: check_agent_turn(run, api),
        "approval": lambda: check_approval(run, api),
        "chat_workspace": lambda: check_chat(run, api, "copilot"),
        "chat_case": lambda: check_chat(run, api, "case"),
        "channel": lambda: check_channel(run, api),
        "isolation": lambda: check_isolation(run, api),
        "sql": lambda: check_sql(run),
        "logs": lambda: check_logs(run),
    }
    for name, surface, expected in CHECKS:
        result = CheckResult(name=name, surface=surface, expected=expected)
        run.checks.append(result)
        if (only and name not in only) or name in skip:
            continue
        if (
            name == "sql"
            and not run.target.sql
            or name == "logs"
            and not run.target.logs
        ):
            continue
        t0 = time.monotonic()
        try:
            result.observed = fns[name]()
            result.result = "PASS"
        except Exception as e:  # noqa: BLE001 - every check is recorded and the run continues
            result.result = "FAIL"
            result.error = scrub(f"{type(e).__name__}: {e}")[:1000]
            (run.out / "evidence" / f"{name}-traceback.txt").write_text(
                scrub(traceback.format_exc())
            )
        result.seconds = round(time.monotonic() - t0, 1)
        print(
            f"{result.result:4} {name:16} {result.seconds:6.1f}s  {result.error or ''}"[
                :300
            ],
            flush=True,
        )
    gets: dict[str, Any] = {}
    if (only and "get_sweep" not in only) or "get_sweep" in skip:
        run.checks.append(
            CheckResult(
                name="get_sweep",
                surface="H14 every parameter-free GET route",
                expected="no 500; vs baseline: no 2xx→error, no data→empty",
            )
        )
    else:
        gets = sweep_gets(run, api)
        # 501/503 are deliberate (unimplemented / unconfigured); 500 is a crash.
        # A route we could not read (timeout, reset) is unknown, not clean.
        server_errors = sorted(
            p
            for p, v in gets.items()
            # 501/503 are deliberate (unimplemented / unconfigured routes).
            if v.get("status") in (500, 502, 504, None)
        )
        print(
            f"{'FAIL' if server_errors else 'PASS':4} get_sweep        {len(gets)} routes, 5xx: {server_errors}"
        )
        run.checks.append(
            CheckResult(
                name="get_sweep",
                surface="H14 every parameter-free GET route",
                expected="no 500 or unreadable route; vs baseline: no 2xx→error, no data→empty",
                result="FAIL" if server_errors else "PASS",
                observed={"routes": len(gets), "server_errors": server_errors},
            )
        )
    return gets


def diff_baseline(
    run: Run, gets: dict[str, Any], baseline_dir: Path
) -> list[dict[str, Any]]:
    base = json.loads((baseline_dir / "results.json").read_text())
    regressions: list[dict[str, Any]] = []
    before = {c["name"]: c["result"] for c in base["checks"]}
    for c in run.checks:
        if before.get(c.name) == "PASS" and c.result in ("FAIL", "SKIP"):
            regressions.append(
                {"kind": "check", "name": c.name, "now": c.result, "error": c.error}
            )
    for path, b in base.get("get_sweep", {}).items():
        now_row = gets.get(path)
        if now_row is None or b.get("status") is None:
            continue
        b_ok, n_ok = 200 <= b["status"] < 300, 200 <= (now_row.get("status") or 0) < 300
        if b_ok and not n_ok:
            regressions.append(
                {
                    "kind": "get",
                    "path": path,
                    "before": b["status"],
                    "now": now_row.get("status"),
                }
            )
        elif b_ok and n_ok and b.get("empty") is False and now_row.get("empty") is True:
            regressions.append(
                {"kind": "get", "path": path, "before": b.get("count"), "now": "empty"}
            )
    if regressions:
        run.checks.append(
            CheckResult(
                name="baseline_diff",
                surface="regression vs baseline",
                expected="nothing that passed or returned data in the baseline fails, is skipped or is empty now",
                result="FAIL",
                observed=regressions,
            )
        )
    else:
        run.checks.append(
            CheckResult(
                name="baseline_diff",
                surface="regression vs baseline",
                expected="no regressions",
                result="PASS",
                observed=[],
            )
        )
    print(
        f"{run.checks[-1].result:4} baseline_diff    {len(regressions)} regressions vs {baseline_dir}"
    )
    return regressions


def write_outputs(
    run: Run, gets: dict[str, Any], regressions: list[dict[str, Any]] | None
) -> None:
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    results = {
        "target": run.target.name,
        "origin": run.target.origin,
        "tag": run.tag,
        "started": run.started,
        "finished": now(),
        "script_sha256": script_sha,
        "seeds": run.seeds,
        "checks": [
            {
                "name": c.name,
                "surface": c.surface,
                "expected": c.expected,
                "result": c.result,
                "observed": redact(c.observed),
                "error": c.error,
                "seconds": c.seconds,
            }
            for c in run.checks
        ],
        "get_sweep": gets,
        "regressions": regressions,
    }
    (run.out / "results.json").write_text(json.dumps(results, indent=2, default=str))
    (run.out / "evidence" / "http-log.json").write_text(
        json.dumps(run.http_log, indent=1, default=str)
    )
    lines = [
        f"# RLS sweep: {run.target.name} ({run.target.origin})",
        "",
        f"Started {run.started}, finished {results['finished']}, tag `{run.tag}`, script sha256 `{script_sha}`.",
        f"Workspaces A `{run.seeds.get('workspace_a')}`, B `{run.seeds.get('workspace_b')}`.",
        "",
        "| Check | Surface | Expected | Result | Observed / error |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in run.checks:
        observed = c.error or json.dumps(redact(c.observed), default=str)
        observed = observed.replace("|", "\\|").replace("\n", " ")[:240]
        lines.append(
            f"| {c.name} | {c.surface} | {c.expected} | **{c.result}** | {observed} |"
        )
    counts = {
        r: sum(1 for c in run.checks if c.result == r) for r in ("PASS", "FAIL", "SKIP")
    }
    lines += [
        "",
        f"PASS {counts['PASS']}, FAIL {counts['FAIL']}, SKIP {counts['SKIP']}.",
    ]
    if gets:
        lines += [
            "",
            f"GET sweep: {len(gets)} routes; statuses: "
            + json.dumps(_status_histogram(gets)),
        ]
    (run.out / "summary.md").write_text("\n".join(lines) + "\n")


def _status_histogram(gets: dict[str, Any]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for v in gets.values():
        key = str(v.get("status"))
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items()))


def build_target(args: argparse.Namespace) -> Target:
    password = os.environ.get("RLS_SWEEP_PASSWORD")
    if args.target == "staging":
        if password is None:
            password = subprocess.check_output(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    "tracecat-staging-dev",
                    "-w",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        return Target(
            name="staging",
            origin=args.origin or "https://app.tracecat-staging.com",
            email=args.email,
            password=password,
            sql=None
            if args.sql == "skip"
            else args.sql or "kubectl:tracecat/tracecat-api/api",
            logs=None if args.logs == "skip" else args.logs or "kubectl:tracecat",
            model={
                "name": args.model or "claude-sonnet-5-5",
                "provider": args.provider or "anthropic",
            },
            catalog_id=args.catalog_id,
            keep_log_lines=args.keep_log_lines,
            harness={"backend_id": "ee", "harness_type": "pi_durable"}
            if args.harness == "auto"
            else json.loads(args.harness),
        )
    return Target(
        name="local",
        origin=args.origin or "http://localhost",
        email=args.email,
        password=password or "password1234",
        sql=None if args.sql in (None, "skip") else args.sql,
        logs=None if args.logs in (None, "skip") else args.logs,
        model={
            "name": args.model or "gpt-5-mini",
            "provider": args.provider or "openai",
        },
        catalog_id=args.catalog_id,
        keep_log_lines=args.keep_log_lines,
        harness={} if args.harness == "auto" else json.loads(args.harness),
    )


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--target", choices=("staging", "local"), required=True)
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Fresh directory for results and evidence",
    )
    p.add_argument(
        "--tag", default="qa-rls-sweep", help="Prefix for the QA workspaces and objects"
    )
    p.add_argument(
        "--baseline", type=Path, help="A previous run's --out to diff against"
    )
    p.add_argument("--origin")
    p.add_argument("--email", default="dev@tracecat.com")
    p.add_argument(
        "--sql", help="kubectl:<ns>/<deploy>/<container> | docker:<container> | skip"
    )
    p.add_argument("--logs", help="kubectl:<ns> | docker:<compose project> | skip")
    p.add_argument("--model")
    p.add_argument("--provider")
    p.add_argument("--catalog-id")
    p.add_argument(
        "--harness",
        default="auto",
        help='JSON session fields, e.g. {"harness_type": "pi_durable"}',
    )
    p.add_argument("--only", help="Comma-separated check names (plus get_sweep)")
    p.add_argument("--skip", default="", help="Comma-separated check names to skip")
    p.add_argument(
        "--keep-log-lines",
        action="store_true",
        help="Save matching raw log lines (they can carry tenant data)",
    )
    args = p.parse_args()
    known = {name for name, _, _ in CHECKS} | {"get_sweep"}
    only = set(args.only.split(",")) if args.only else None
    skip = set(filter(None, args.skip.split(",")))
    if unknown := ((only or set()) | skip) - known:
        p.error(f"unknown check names {sorted(unknown)}; known: {sorted(known)}")

    args.out.mkdir(parents=True, exist_ok=True)
    run = Run(target=build_target(args), tag=args.tag, out=args.out)
    (run.out / "evidence").mkdir(exist_ok=True)
    api = Api(run)
    print(
        f"RLS sweep {run.target.name} {run.target.origin} tag={run.tag} out={run.out}",
        flush=True,
    )
    gets: dict[str, Any] = {}
    regressions: list[dict[str, Any]] | None = None
    try:
        api.login()
        run.seeds = seed(run, api)
        print(
            f"seeded A={run.seeds['workspace_a']} B={run.seeds['workspace_b']}",
            flush=True,
        )
        gets = run_checks(run, api, only, skip)
        if args.baseline:
            regressions = diff_baseline(run, gets, args.baseline)
    except Exception as e:  # noqa: BLE001 - login/seed failure is itself a FAIL
        run.checks.append(
            CheckResult(
                name="setup",
                surface="login + seed",
                expected="login and seed succeed",
                result="FAIL",
                error=scrub(f"{type(e).__name__}: {e}")[:1000],
            )
        )
        (run.out / "evidence" / "setup-traceback.txt").write_text(
            scrub(traceback.format_exc())
        )
        print(scrub(f"FAIL setup {type(e).__name__}: {e}")[:400])
    finally:
        write_outputs(run, gets, regressions)
        api.client.close()
    failed = [c.name for c in run.checks if c.result == "FAIL"]
    skipped = [c.name for c in run.checks if c.result == "SKIP"]
    verdict = "FAIL" if failed else "INCOMPLETE" if skipped else "PASS"
    print(
        f"{verdict}: {len(failed)} failed {failed}, {len(skipped)} skipped {skipped}; "
        f"summary {run.out / 'summary.md'}"
    )
    # A run that skipped a check is not evidence that the surface works.
    return 1 if failed else 2 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
