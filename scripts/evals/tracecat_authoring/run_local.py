from __future__ import annotations

import argparse
import ast
import asyncio
import importlib
import inspect
import json
import os
import pkgutil
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastmcp import Client
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from tracecat.dsl.common import DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.mcp.json_patch import validate_patch_paths
from tracecat.mcp.schemas import JsonPatchOperation, WorkflowYamlPayload

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SKILL_SOURCE = REPO_ROOT / ".agents/skills/tracecat-automation-best-practices/SKILL.md"
DEFAULT_DIRECT_MCP_URL = "http://127.0.0.1:8099/mcp"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / ".tracecat/evals/tracecat_authoring"
ALLOWED_PATCH_ROOTS = {
    "metadata",
    "definition",
    "layout",
    "schedules",
    "case_trigger",
}
PROMPT_SOURCE_MAX_CHARS = {
    "mcp_instructions": 14_500,
    "dsl_reference": 15_500,
    "best_practices_skill": 9_500,
}


class Case(BaseModel):
    id: str
    groups: list[str] = []
    prompt: str
    rubric: list[str]
    seed_workflow: bool = False


class CasesFile(BaseModel):
    cases: list[Case]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class PromptSource:
    name: str
    text: str


@dataclass
class CaseResult:
    case_id: str
    agent: str
    passed: bool
    model: str | None = None
    workflow_ids: list[str] = field(default_factory=list)
    changed_workflow_ids: list[str] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    transcript_path: str | None = None
    final_response_path: str | None = None
    error: str | None = None
    duration_seconds: float | None = None
    accuracy: float | None = None
    mcp_tool_calls: int | None = None
    mcp_tool_successes: int | None = None
    mcp_tool_failures: int | None = None
    mcp_schema_input_failures: int | None = None
    workflow_node_count: int | None = None
    branch_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "agent": self.agent,
            "model": self.model,
            "passed": self.passed,
            "workflow_ids": self.workflow_ids,
            "changed_workflow_ids": self.changed_workflow_ids,
            "checks": [check.as_dict() for check in self.checks],
            "transcript_path": self.transcript_path,
            "final_response_path": self.final_response_path,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "accuracy": self.accuracy,
            "mcp_tool_calls": self.mcp_tool_calls,
            "mcp_tool_successes": self.mcp_tool_successes,
            "mcp_tool_failures": self.mcp_tool_failures,
            "mcp_schema_input_failures": self.mcp_schema_input_failures,
            "workflow_node_count": self.workflow_node_count,
            "branch_count": self.branch_count,
        }


@dataclass
class McpToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = "unknown"
    text: str = ""
    payload: Any = None


@dataclass
class McpToolCallMetrics:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    schema_input_failures: int = 0


@dataclass
class TranscriptAnalysis:
    mcp_metrics: McpToolCallMetrics
    mcp_calls: list[McpToolCall] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)
    changed_workflow_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class WorkflowSnapshot:
    workflow_id: str
    raw: dict[str, Any]
    yaml_payload: dict[str, Any] | None
    definition: DSLInput | None
    layout: dict[str, Any]

    @property
    def actions(self) -> list[ActionStatement]:
        return self.definition.actions if self.definition else []


@dataclass
class AgentRun:
    final_response: dict[str, Any]
    duration_seconds: float


class TracecatMCP:
    def __init__(self, url: str, bearer_token: str | None) -> None:
        self._client = Client(url, auth=bearer_token)

    async def __aenter__(self) -> TracecatMCP:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.__aexit__(*exc_info)

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await self._client.call_tool(name, arguments or {})
        return decode_mcp_result(result)


def decode_mcp_result(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    data = getattr(result, "data", None)
    if data is not None:
        return data

    content = getattr(result, "content", None) or []
    texts = [
        item.text
        for item in content
        if getattr(item, "type", None) == "text" and hasattr(item, "text")
    ]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    if texts:
        return "\n".join(texts)
    return result


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text())
    return CasesFile.model_validate(raw).cases


def select_cases(cases: list[Case], selector: str) -> list[Case]:
    if selector == "all":
        return cases
    selected = [
        case for case in cases if selector == case.id or selector in set(case.groups)
    ]
    if not selected:
        raise SystemExit(f"No cases match selector {selector!r}")
    return selected


def timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Final response is not a JSON object")
    return value


UUID_FRAGMENT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID_PATTERN = re.compile(rf"\b{UUID_FRAGMENT}\b", re.IGNORECASE)


def unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def analyze_transcript(transcript_path: Path | None) -> TranscriptAnalysis:
    calls: dict[str, McpToolCall] = {}
    notes: list[str] = []
    for event in transcript_json_events(transcript_path):
        collect_actual_mcp_tool_calls(event, calls)
        if isinstance(event.get("result"), str):
            notes.append(event["result"])

    metrics = McpToolCallMetrics(total=len(calls))
    workflow_ids: list[str] = []
    changed_workflow_ids: list[str] = []

    for call in calls.values():
        if mcp_call_failed(call):
            call.status = "failed"
            metrics.failed += 1
            if schema_input_failure_text(call.text):
                metrics.schema_input_failures += 1
        else:
            call.status = "succeeded"
            metrics.succeeded += 1

        workflow_ids.extend(workflow_ids_from_call(call))
        changed_workflow_ids.extend(changed_workflow_ids_from_call(call))

    return TranscriptAnalysis(
        mcp_metrics=metrics,
        mcp_calls=list(calls.values()),
        workflow_ids=unique_strings(workflow_ids),
        changed_workflow_ids=unique_strings(changed_workflow_ids),
        notes=[note for note in notes if note.strip()],
    )


def collect_actual_mcp_tool_calls(value: Any, calls: dict[str, McpToolCall]) -> None:
    if isinstance(value, list):
        for item in value:
            collect_actual_mcp_tool_calls(item, calls)
        return
    if not isinstance(value, dict):
        return

    if (
        value.get("type") == "tool_use"
        and isinstance(value.get("name"), str)
        and value["name"].startswith("mcp__")
    ):
        tool_name = tracecat_tool_name(value["name"])
        if tool_name:
            calls[str(value["id"])] = McpToolCall(
                call_id=str(value["id"]),
                tool_name=tool_name,
                arguments=value.get("input")
                if isinstance(value.get("input"), dict)
                else {},
            )

    if value.get("type") == "mcp_tool_call":
        tool_name = tracecat_tool_name(str(value.get("tool", "")))
        call_id = value.get("id")
        if tool_name and isinstance(call_id, str):
            call = calls.setdefault(call_id, McpToolCall(call_id, tool_name))
            call.tool_name = tool_name
            if isinstance(value.get("arguments"), dict):
                call.arguments = value["arguments"]
            if isinstance(value.get("status"), str):
                call.status = value["status"].lower()
            if "result" in value:
                call.payload = value["result"]
                call.text += " " + compact_text(value["result"])
            if "error" in value:
                call.text += " " + compact_text(value["error"])

    if (
        value.get("type") == "tool_result"
        and isinstance(value.get("tool_use_id"), str)
        and value["tool_use_id"] in calls
    ):
        call = calls[value["tool_use_id"]]
        call.payload = value.get("content")
        call.text += " " + compact_text(value.get("content"))

    for item in value.values():
        collect_actual_mcp_tool_calls(item, calls)


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(compact_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(compact_text(item) for item in value.values())
    return str(value)


def mcp_call_failed(call: McpToolCall) -> bool:
    text = call.text.lower()
    if call.status in {"failed", "error", "errored"}:
        return True
    if call.status in {"unknown", "in_progress"} and not text.strip():
        return True
    return any(
        needle in text
        for needle in (
            "toolerror",
            "internal error:",
            "input validation",
            "invalid arguments",
            "invalid input",
            "401 unauthorized",
            "403 forbidden",
            "404 not found",
            "500 internal server error",
        )
    )


def schema_input_failure_text(text: str) -> bool:
    lowered = text.lower()
    return any(
        needle in lowered
        for needle in (
            "input validation",
            "invalid arguments",
            "invalid input",
            "json schema",
            "schema validation",
            "field required",
            "extra_forbidden",
            "additional properties",
            "unexpected keyword",
            "missing required",
            "pydantic",
            "model_validate",
        )
    )


def workflow_ids_from_call(call: McpToolCall) -> list[str]:
    if call.tool_name == "create_workflow":
        return ids_from_payload_keys(call.payload, "id")
    if call.tool_name in {
        "edit_workflow",
        "get_workflow",
        "validate_workflow",
        "run_draft_workflow",
        "run_published_workflow",
    }:
        return ids_from_payload_keys(call.payload, "workflow_id", "id")
    return []


def changed_workflow_ids_from_call(call: McpToolCall) -> list[str]:
    if call.tool_name == "create_workflow":
        return ids_from_payload_keys(call.payload, "id")
    if call.tool_name != "edit_workflow":
        return []
    if call.arguments.get("validate_only") is True:
        return []
    return ids_from_payload_keys(call.payload, "workflow_id")


def ids_from_payload_keys(payload: Any, *keys: str) -> list[str]:
    ids: list[str] = []
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if isinstance(payload, list):
        for item in payload:
            ids.extend(ids_from_payload_keys(item, *keys))
    elif isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and UUID_PATTERN.fullmatch(value):
                ids.append(value)
        for key in (
            "content",
            "structuredContent",
            "structured_content",
            "result",
            "text",
        ):
            if key in payload:
                ids.extend(ids_from_payload_keys(payload[key], *keys))
    return ids


def normalize_final_response(
    raw: Mapping[str, Any], *, case_id: str, analysis: TranscriptAnalysis
) -> dict[str, Any]:
    workflow_ids = response_id_list(raw, "workflow_ids", "workflow_id")
    changed_workflow_ids = response_id_list(
        raw, "changed_workflow_ids", "changed_workflow_id"
    )
    if not workflow_ids:
        workflow_ids = analysis.workflow_ids
    if not changed_workflow_ids:
        changed_workflow_ids = analysis.changed_workflow_ids

    notes = raw.get("notes")
    if isinstance(notes, str):
        normalized_notes = [notes]
    elif isinstance(notes, list):
        normalized_notes = [str(note) for note in notes if note is not None]
    else:
        normalized_notes = analysis.notes

    return {
        "case_id": str(raw.get("case_id") or case_id),
        "workflow_ids": unique_strings(workflow_ids),
        "changed_workflow_ids": unique_strings(changed_workflow_ids),
        "notes": normalized_notes,
    }


def response_id_list(raw: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return [value for value in values if UUID_PATTERN.fullmatch(value)]


def normalize_item_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return [item for item in value["items"] if isinstance(item, dict)]
        if isinstance(value.get("workspaces"), list):
            return [item for item in value["workspaces"] if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


async def choose_workspace(mcp: TracecatMCP) -> str:
    workspace_id = os.getenv("TRACECAT_EVAL_WORKSPACE_ID")
    if workspace_id:
        return workspace_id

    response = await mcp.call("list_workspaces", {"limit": 20})
    workspaces = normalize_item_list(response)
    if not workspaces:
        raise RuntimeError("list_workspaces returned no workspaces")
    for key in ("id", "workspace_id"):
        if key in workspaces[0]:
            return str(workspaces[0][key])
    raise RuntimeError("Could not find workspace id in list_workspaces response")


def isolated_workspace(case_dir: Path) -> Path:
    workspace = case_dir / "workspace"
    skill_target = workspace / ".agents/skills/tracecat-automation-best-practices"
    skill_target.parent.mkdir(parents=True, exist_ok=True)
    # Copy the whole skill directory so progressive-disclosure references/ files
    # (loaded on demand by SKILL.md links) are present in the workspace too.
    shutil.copytree(SKILL_SOURCE.parent, skill_target, dirs_exist_ok=True)
    return workspace


def seed_workflow_yaml(title: str) -> str:
    return yaml.safe_dump(
        {
            "definition": {
                "title": title,
                "description": "Synthetic seeded workflow for authoring evals.",
                "entrypoint": {
                    "ref": "emit_seed",
                    "expects": {
                        "source": {
                            "type": "str",
                            "description": "Synthetic source name.",
                            "default": "example.com",
                        }
                    },
                },
                "actions": [
                    {
                        "ref": "emit_seed",
                        "action": "core.script.run_python",
                        "args": {
                            "inputs": {"source": "${{ TRIGGER.source }}"},
                            "script": (
                                "def main(source):\n"
                                "    return {'source': source, 'message': 'seed'}\n"
                            ),
                        },
                    }
                ],
                "returns": "${{ ACTIONS.emit_seed.result }}",
            },
            "layout": {
                "trigger": {"x": 0, "y": 0},
                "actions": [{"ref": "emit_seed", "x": 320, "y": 0}],
            },
        },
        sort_keys=False,
    )


async def seed_case_workflow(
    mcp: TracecatMCP,
    *,
    workspace_id: str,
    run_prefix: str,
    case_id: str,
) -> str:
    title = f"{run_prefix}-{case_id}-seed"
    response = await mcp.call(
        "create_workflow",
        {
            "workspace_id": workspace_id,
            "title": title,
            "description": "Synthetic seeded workflow for authoring evals.",
            "definition_yaml": seed_workflow_yaml(title),
        },
    )
    if isinstance(response, dict):
        for key in ("workflow_id", "id"):
            if key in response:
                return str(response[key])
    raise RuntimeError(f"Could not read seed workflow id from response: {response!r}")


def build_agent_prompt(
    *,
    case: Case,
    workspace_id: str,
    run_prefix: str,
    seed_workflow_id: str | None,
) -> str:
    seed_text = (
        f"\nExisting seeded workflow id: {seed_workflow_id}\n"
        if seed_workflow_id
        else ""
    )
    return f"""Use the local tracecat-automation-best-practices skill.

You are running a local-only Tracecat automation authoring eval.

Workspace id: {workspace_id}
Workflow title prefix: {run_prefix}-{case.id}
Case id: {case.id}
{seed_text}
Important constraints:
- Use the Tracecat MCP tools for workspace discovery, authoring context, action schemas, workflow creation or edits, validation, and safe execution inspection when requested.
- Use only the MCP server configured for this eval. Do not use globally configured Tracecat MCP servers, remote Tracecat workspaces, or platform.tracecat.com.
- Use synthetic data only. Use example.com for placeholder endpoints. Do not use real people, real tokens, or provider-specific integrations unless the case names them.
- Do not inspect the Tracecat source repository. This temporary workspace intentionally contains only the generic automation skill.
- Do not inspect environment variables or print credential-bearing process state.
- Final response may be concise prose or JSON, but it must include the workflow IDs you created or edited when a workflow was created or edited.

Task:
{case.prompt}
"""


def codex_config_args(mcp_url: str) -> list[str]:
    args = ["-c", f'mcp_servers.tracecat.url="{mcp_url}"']
    if os.getenv("TRACECAT_MCP_BEARER_TOKEN"):
        args.extend(
            [
                "-c",
                'mcp_servers.tracecat.bearer_token_env_var="TRACECAT_MCP_BEARER_TOKEN"',
            ]
        )
    return args


def agent_model(agent: str) -> str:
    if agent == "codex":
        return os.getenv("TRACECAT_EVAL_MODEL", "default")
    if agent == "claude-code":
        return os.getenv("TRACECAT_EVAL_CLAUDE_MODEL", "claude-opus-4-7")
    return "n/a"


def agent_timeout_seconds() -> float:
    raw = os.getenv("TRACECAT_EVAL_AGENT_TIMEOUT_SECONDS", "900")
    try:
        timeout = float(raw)
    except ValueError:
        raise ValueError(
            "TRACECAT_EVAL_AGENT_TIMEOUT_SECONDS must be numeric"
        ) from None
    if timeout <= 0:
        raise ValueError("TRACECAT_EVAL_AGENT_TIMEOUT_SECONDS must be positive")
    return timeout


def codex_bypass_approvals_enabled() -> bool:
    return os.getenv("TRACECAT_EVAL_CODEX_BYPASS_APPROVALS") == "1"


def run_agent(
    *,
    case: Case,
    workspace: Path,
    prompt: str,
    transcript_path: Path,
    final_path: Path,
    schema_path: Path,
    mcp_url: str,
    agent: str,
    agent_cmd: str | None,
) -> AgentRun:
    model = os.getenv("TRACECAT_EVAL_MODEL")
    if agent_cmd:
        replacements = {
            "workspace": str(workspace),
            "transcript": str(transcript_path),
            "final": str(final_path),
            "schema": str(schema_path),
            "mcp_url": mcp_url,
            "case_id": case.id,
            "prompt": prompt,
        }
        rendered = agent_cmd.format(**replacements)
        cmd = shlex.split(rendered)
        if "{prompt}" not in agent_cmd:
            cmd.append(prompt)
    elif agent == "codex":
        cmd = [
            "codex",
            "--ask-for-approval",
            "never",
            "exec",
        ]
        if codex_bypass_approvals_enabled():
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.extend(["--sandbox", "workspace-write"])
        cmd.extend(
            [
                "--ignore-user-config",
                "--skip-git-repo-check",
                "--ephemeral",
                "--cd",
                str(workspace),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(final_path),
                "--json",
                *codex_config_args(mcp_url),
            ]
        )
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)
    elif agent == "claude-code":
        mcp_config_path = workspace / "claude-mcp.json"
        write_json(mcp_config_path, claude_mcp_config(mcp_url))
        claude_model = os.getenv("TRACECAT_EVAL_CLAUDE_MODEL", "claude-opus-4-7")
        cmd = [
            "claude",
            "-p",
            "--no-session-persistence",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            claude_model,
            "--mcp-config",
            str(mcp_config_path),
            "--strict-mcp-config",
            prompt,
        ]
    else:
        raise ValueError(f"Unsupported agent {agent!r}")

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with transcript_path.open("w") as transcript:
        try:
            completed = subprocess.run(
                cmd,
                cwd=workspace,
                stdout=transcript,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=agent_timeout_seconds(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Agent exceeded timeout of {exc.timeout:.0f}s") from exc
    duration_seconds = time.monotonic() - started
    if completed.returncode != 0:
        raise RuntimeError(f"Agent exited with code {completed.returncode}")
    analysis = analyze_transcript(transcript_path)
    if final_path.exists():
        raw_final_response = extract_json_object(final_path.read_text())
    else:
        raw_final_response = extract_final_response_from_transcript(transcript_path)
    final_response = normalize_final_response(
        raw_final_response,
        case_id=case.id,
        analysis=analysis,
    )
    write_json(final_path, final_response)
    return AgentRun(
        final_response=final_response,
        duration_seconds=duration_seconds,
    )


def claude_mcp_config(mcp_url: str) -> dict[str, Any]:
    server: dict[str, Any] = {"type": "http", "url": mcp_url}
    token = os.getenv("TRACECAT_MCP_BEARER_TOKEN")
    if token:
        server["headers"] = {"Authorization": "Bearer ${TRACECAT_MCP_BEARER_TOKEN}"}
    return {"mcpServers": {"tracecatlocal": server}}


def extract_final_response_from_transcript(transcript_path: Path) -> dict[str, Any]:
    candidates: list[str] = []
    for line in transcript_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for value in iter_text_candidates(event):
            candidates.append(value)
    for candidate in reversed(candidates):
        try:
            return extract_json_object(candidate)
        except Exception:
            continue
    if candidates:
        return {"notes": [candidates[-1]]}
    raise RuntimeError("Could not extract final response from transcript")


def iter_text_candidates(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_text_candidates(item)
    elif isinstance(value, dict):
        if value.get("type") in {"text", "assistant"} and isinstance(
            value.get("text"), str
        ):
            yield value["text"]
        for key in ("result", "content", "message"):
            if key in value:
                yield from iter_text_candidates(value[key])


def transcript_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(errors="replace")


def transcript_contains(transcript: str, *needles: str) -> bool:
    lowered = transcript.lower()
    return all(needle.lower() in lowered for needle in needles)


def transcript_count(transcript: str, needle: str) -> int:
    return transcript.lower().count(needle.lower())


def transcript_json_events(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def normalize_tool_name(name: str) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return parts[2]
    if name.startswith("mcp__tracecat__"):
        return name.removeprefix("mcp__tracecat__")
    if name.startswith("tracecat__"):
        return name.removeprefix("tracecat__")
    return name


TRACECAT_MCP_TOOL_NAMES = {
    "add_case_tag",
    "add_workflow_tag",
    "create_agent_preset",
    "create_case",
    "create_case_comment",
    "create_case_field",
    "create_case_tag",
    "create_case_task",
    "create_column_index",
    "create_table",
    "create_workflow",
    "create_workflow_folder",
    "create_workflow_tag",
    "delete_case_comment",
    "delete_case_tag",
    "delete_workflow_tag",
    "drop_column_index",
    "edit_workflow",
    "export_csv",
    "get_action_context",
    "get_agent_preset",
    "get_agent_preset_authoring_context",
    "get_case",
    "get_case_task",
    "get_case_trigger",
    "get_secret_metadata",
    "get_table",
    "get_variable",
    "get_webhook",
    "get_workflow",
    "get_workflow_authoring_context",
    "get_workflow_execution",
    "insert_table_row",
    "list_actions",
    "list_agent_presets",
    "list_case_comment_threads",
    "list_case_comments",
    "list_case_events",
    "list_case_fields",
    "list_case_tags",
    "list_case_tasks",
    "list_cases",
    "list_integrations",
    "list_secrets_metadata",
    "list_tables",
    "list_tags_for_case",
    "list_tags_for_workflow",
    "list_variables",
    "list_workflow_executions",
    "list_workflow_tags",
    "list_workflow_tree",
    "list_workflows",
    "list_workspaces",
    "move_workflows",
    "prepare_template_file_upload",
    "publish_workflow",
    "remove_case_tag",
    "remove_workflow_tag",
    "run_agent_preset",
    "run_case_task",
    "run_draft_workflow",
    "run_published_workflow",
    "search_cases",
    "search_table_rows",
    "sync_custom_registry",
    "update_agent_preset",
    "update_case",
    "update_case_comment",
    "update_case_field",
    "update_case_tag",
    "update_case_task",
    "update_case_trigger",
    "update_table",
    "update_table_row",
    "update_webhook",
    "update_workflow",
    "update_workflow_tag",
    "upload_skill",
    "validate_template_action",
    "validate_workflow",
}


def tracecat_tool_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = normalize_tool_name(value)
    if name in TRACECAT_MCP_TOOL_NAMES:
        return name
    return None


def dict_value_text(value: Mapping[str, Any], keys: Iterable[str]) -> str:
    return " ".join(str(value[key]).lower() for key in keys if key in value)


def direct_tool_name(value: Mapping[str, Any]) -> str | None:
    for key in ("name", "tool_name", "tool", "function", "recipient_name"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            tool_name = tracecat_tool_name(candidate)
            if tool_name:
                return tool_name
    return None


def iter_tracecat_tool_calls(value: Any) -> Iterable[tuple[str | None, str]]:
    if isinstance(value, list):
        for item in value:
            yield from iter_tracecat_tool_calls(item)
        return
    if not isinstance(value, dict):
        return

    type_text = dict_value_text(value, ("type", "event", "subtype"))
    server_text = dict_value_text(value, ("server", "server_name", "mcp_server"))
    tool_name = direct_tool_name(value)
    is_tool_call = (
        "tool_use" in type_text
        or "tool_call" in type_text
        or "mcp_tool_call" in type_text
        or ("tool" in type_text and "call" in type_text)
        or ("tracecat" in server_text and tool_name is not None)
    )
    if is_tool_call and tool_name is not None:
        call_id = next(
            (
                str(value[key])
                for key in ("id", "call_id", "tool_call_id")
                if isinstance(value.get(key), str)
            ),
            None,
        )
        yield call_id, tool_name

    for item in value.values():
        yield from iter_tracecat_tool_calls(item)


def count_mcp_tool_calls(transcript_path: Path | None) -> int:
    return analyze_transcript(transcript_path).mcp_metrics.total


def source_inspection_detected(transcript: str) -> bool:
    repo = str(REPO_ROOT)
    artifact_root = str(DEFAULT_ARTIFACT_ROOT)
    suspicious_commands = ("sed ", "cat ", "rg ", "grep ", "find ", "ls ")
    for line in transcript.splitlines():
        line = line.replace(artifact_root, "")
        if repo not in line:
            continue
        if not any(command in line for command in suspicious_commands):
            continue
        if re.search(r'"(cmd|command)"\s*:', line) or re.search(
            r'"name"\s*:\s*"(Bash|Read|Grep|Glob|LS|exec_command)"', line
        ):
            return True
    return False


async def fetch_workflow_snapshot(
    mcp: TracecatMCP,
    *,
    workspace_id: str,
    workflow_id: str,
) -> WorkflowSnapshot:
    raw = await mcp.call(
        "get_workflow",
        {
            "workspace_id": workspace_id,
            "workflow_id": workflow_id,
            "include_definition_yaml": True,
        },
    )
    if not isinstance(raw, dict):
        raise RuntimeError(f"get_workflow returned non-object: {raw!r}")

    definition_yaml = raw.get("definition_yaml")
    yaml_payload = None
    definition = None
    layout: dict[str, Any] = {}
    if isinstance(definition_yaml, str):
        loaded = yaml.safe_load(definition_yaml)
        WorkflowYamlPayload.model_validate(loaded)
        if isinstance(loaded, dict):
            yaml_payload = loaded
            if isinstance(loaded.get("definition"), dict):
                definition = DSLInput.model_validate(loaded["definition"])
            if isinstance(loaded.get("layout"), dict):
                layout = loaded["layout"]
    elif isinstance(raw.get("definition"), dict):
        definition = DSLInput.model_validate(raw["definition"])
    return WorkflowSnapshot(
        workflow_id=workflow_id,
        raw=raw,
        yaml_payload=yaml_payload,
        definition=definition,
        layout=layout,
    )


async def fetch_action_schemas(
    mcp: TracecatMCP,
    *,
    workspace_id: str,
    action_names: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not action_names:
        return {}
    try:
        response = await mcp.call(
            "get_workflow_authoring_context",
            {
                "workspace_id": workspace_id,
                "actions": {"action_names": sorted(set(action_names))},
            },
        )
    except Exception:
        return {}
    if not isinstance(response, dict):
        return {}
    schemas: dict[str, dict[str, Any]] = {}
    for item in response.get("actions", []):
        if isinstance(item, dict) and isinstance(item.get("action_name"), str):
            schema = item.get("parameters_json_schema")
            if isinstance(schema, dict):
                schemas[item["action_name"]] = schema
    return schemas


def check_action_args_against_schemas(
    snapshots: Sequence[WorkflowSnapshot],
    schemas: Mapping[str, Mapping[str, Any]],
) -> CheckResult:
    failures: list[str] = []
    checked = 0
    for snapshot in snapshots:
        for action in snapshot.actions:
            schema = schemas.get(action.action)
            if not schema:
                continue
            checked += 1
            errors = sorted(
                Draft202012Validator(schema).iter_errors(action.args),
                key=lambda error: list(error.path),
            )
            if errors:
                failures.append(
                    f"{snapshot.workflow_id}:{action.ref}: {errors[0].message}"
                )
    if failures:
        return CheckResult("action_args_match_live_schemas", False, "; ".join(failures))
    if checked == 0:
        return CheckResult(
            "action_args_match_live_schemas",
            False,
            "No live action schemas were available for authored actions.",
        )
    return CheckResult(
        "action_args_match_live_schemas",
        True,
        f"Validated {checked} action argument sets.",
    )


def all_actions(snapshots: Sequence[WorkflowSnapshot]) -> list[ActionStatement]:
    return [action for snapshot in snapshots for action in snapshot.actions]


def workflow_node_count(snapshots: Sequence[WorkflowSnapshot]) -> int:
    return len(all_actions(snapshots))


def workflow_branch_count(snapshots: Sequence[WorkflowSnapshot]) -> int:
    branch_count = 0
    for snapshot in snapshots:
        fanout: dict[str, int] = {}
        for action in snapshot.actions:
            for dep in action.depends_on:
                dep_ref = dep.split(".", 1)[0]
                fanout[dep_ref] = fanout.get(dep_ref, 0) + 1
        branch_count += sum(1 for count in fanout.values() if count > 1)
    return branch_count


def action_names(snapshots: Sequence[WorkflowSnapshot]) -> set[str]:
    return {action.action for action in all_actions(snapshots)}


def has_action(snapshots: Sequence[WorkflowSnapshot], name: str) -> bool:
    return name in action_names(snapshots)


def has_any_action(snapshots: Sequence[WorkflowSnapshot], names: Iterable[str]) -> bool:
    names_set = set(names)
    return bool(action_names(snapshots) & names_set)


def no_tools_actions(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    return not any(
        action.action.startswith("tools.") for action in all_actions(snapshots)
    )


def no_scatter_gather(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    forbidden = {"core.transform.scatter", "core.transform.gather"}
    return not has_any_action(snapshots, forbidden)


def linear_small_graph(snapshots: Sequence[WorkflowSnapshot], limit: int = 6) -> bool:
    for snapshot in snapshots:
        actions = snapshot.actions
        if len(actions) > limit:
            return False
        branch_fanout: dict[str, int] = {}
        for action in actions:
            for dep in action.depends_on:
                dep_ref = dep.split(".", 1)[0]
                branch_fanout[dep_ref] = branch_fanout.get(dep_ref, 0) + 1
        if any(count > 1 for count in branch_fanout.values()):
            return False
    return True


def layout_refs_match_actions(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    for snapshot in snapshots:
        if not snapshot.layout:
            continue
        layout_actions = snapshot.layout.get("actions", [])
        if not isinstance(layout_actions, list):
            return False
        layout_refs = {
            item.get("ref")
            for item in layout_actions
            if isinstance(item, dict) and isinstance(item.get("ref"), str)
        }
        action_refs = {action.ref for action in snapshot.actions}
        if not layout_refs.issubset(action_refs):
            return False
    return True


def declares_trigger_inputs(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    return all(
        snapshot.definition is not None
        and snapshot.definition.entrypoint.expects is not None
        and bool(snapshot.definition.entrypoint.expects)
        for snapshot in snapshots
    )


def bounded_output(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    haystack = json.dumps(
        [action.model_dump(mode="json") for action in all_actions(snapshots)]
    ).lower()
    return any(token in haystack for token in ("[:5]", "[:10]", "limit", "sample"))


def treats_paginate_result_as_list(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    text = json.dumps(
        [action.model_dump(mode="json") for action in all_actions(snapshots)]
    )
    if "core.http_paginate" not in text:
        return False
    return ".items" not in text and ".data" not in text


def agent_without_broad_http_tool(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    for action in all_actions(snapshots):
        if action.action not in {"ai.agent", "ai.preset_agent"}:
            continue
        args_text = json.dumps(action.args)
        if "core.http_request" in args_text:
            return False
    return True


def deterministic_prep_outside_agent(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    return has_action(snapshots, "core.script.run_python") and has_any_action(
        snapshots, {"ai.agent", "ai.preset_agent"}
    )


def workflow_execute_uses_alias(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    return any(
        action.action == "core.workflow.execute"
        and "workflow_alias" in action.args
        and action.args.get("workflow_alias")
        for action in all_actions(snapshots)
    )


def explicit_batch_loop_options(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    for action in all_actions(snapshots):
        if action.action != "core.workflow.execute":
            continue
        text = json.dumps(action.model_dump(mode="json")).lower()
        if "batch" in text and "batch_size" in text and "isolated" in text:
            return True
    return False


def explicit_wait_strategy(snapshots: Sequence[WorkflowSnapshot]) -> bool:
    return any(
        action.action == "core.workflow.execute"
        and "wait_strategy" in json.dumps(action.model_dump(mode="json")).lower()
        for action in all_actions(snapshots)
    )


def provider_gating_note_or_core_http(
    snapshots: Sequence[WorkflowSnapshot],
    final_response: Mapping[str, Any],
) -> bool:
    notes = " ".join(str(note) for note in final_response.get("notes", [])).lower()
    return has_action(snapshots, "core.http_request") or any(
        token in notes for token in ("provider", "integration", "confirmation")
    )


MCP_SQL_TYPES = {
    "TEXT",
    "INTEGER",
    "NUMERIC",
    "DATE",
    "BOOLEAN",
    "TIMESTAMPTZ",
    "JSONB",
    "SELECT",
    "MULTI_SELECT",
}


def mcp_tool_called(calls: Sequence[McpToolCall], tool_name: str) -> bool:
    return any(call.tool_name == tool_name for call in calls)


def update_workflow_metadata_only(calls: Sequence[McpToolCall]) -> bool:
    for call in calls:
        if call.tool_name != "update_workflow":
            continue
        if "definition_yaml" in call.arguments:
            continue
        if "patch_ops" in call.arguments:
            continue
        if any(
            key in call.arguments
            for key in ("title", "description", "status", "alias", "error_handler")
        ):
            return True
    return False


def no_update_workflow_patch_ops(calls: Sequence[McpToolCall]) -> bool:
    return not any(
        call.tool_name == "update_workflow" and "patch_ops" in call.arguments
        for call in calls
    )


def table_columns_are_valid(columns: object) -> bool:
    if not isinstance(columns, list) or not columns:
        return False
    for column in columns:
        if not isinstance(column, dict):
            return False
        column_type = column.get("type")
        if column_type not in MCP_SQL_TYPES:
            return False
        has_options = "options" in column and column.get("options") is not None
        if column_type in {"SELECT", "MULTI_SELECT"}:
            if not isinstance(column.get("options"), list) or not column["options"]:
                return False
        elif has_options:
            return False
    return True


def created_table_with_valid_columns(calls: Sequence[McpToolCall]) -> bool:
    return any(
        call.tool_name == "create_table"
        and table_columns_are_valid(call.arguments.get("columns"))
        for call in calls
    )


def created_table_index_from_fetched_table(calls: Sequence[McpToolCall]) -> bool:
    return mcp_tool_called(calls, "get_table") and mcp_tool_called(
        calls, "create_column_index"
    )


def case_field_args_are_valid(args: Mapping[str, Any]) -> bool:
    field_type = args.get("type")
    if field_type is not None and field_type not in MCP_SQL_TYPES:
        return False
    kind = args.get("kind")
    if kind == "LONG_TEXT" and field_type not in {None, "TEXT"}:
        return False
    if kind == "URL" and field_type not in {None, "JSONB"}:
        return False
    if kind is not None and kind not in {"LONG_TEXT", "URL"}:
        return False
    options = args.get("options")
    if options is not None and not isinstance(options, list):
        return False
    if field_type in {"SELECT", "MULTI_SELECT"}:
        return isinstance(options, list) and bool(options)
    if field_type is not None and options is not None:
        return False
    return True


def created_and_updated_case_fields(calls: Sequence[McpToolCall]) -> bool:
    create_calls = [call for call in calls if call.tool_name == "create_case_field"]
    update_calls = [call for call in calls if call.tool_name == "update_case_field"]
    return (
        bool(create_calls)
        and bool(update_calls)
        and mcp_tool_called(calls, "list_case_fields")
        and all(case_field_args_are_valid(call.arguments) for call in create_calls)
        and all(case_field_args_are_valid(call.arguments) for call in update_calls)
    )


def evaluate_rubric_item(
    item: str,
    *,
    case: Case,
    snapshots: Sequence[WorkflowSnapshot],
    transcript: str,
    mcp_calls: Sequence[McpToolCall],
    final_response: Mapping[str, Any],
    seed_workflow_id: str | None,
) -> CheckResult:
    workflow_ids = [snapshot.workflow_id for snapshot in snapshots]
    changed_ids = [str(x) for x in final_response.get("changed_workflow_ids", [])]

    checks: dict[str, tuple[bool, str]] = {
        "discovered_workspace": (
            transcript_contains(transcript, "list_workspaces"),
            "Expected list_workspaces in transcript.",
        ),
        "starts_from_live_context": (
            transcript_contains(transcript, "list_workspaces")
            and transcript_contains(transcript, "get_workflow_authoring_context"),
            "Expected list_workspaces and "
            "get_workflow_authoring_context in transcript.",
        ),
        "created_workflow": (
            transcript_contains(transcript, "create_workflow") and bool(workflow_ids),
            "Expected create_workflow and returned workflow id.",
        ),
        "valid_workflow_yaml": (
            bool(snapshots) and all(snapshot.definition for snapshot in snapshots),
            "Expected fetched inline workflow YAML to parse as WorkflowYamlPayload/DSLInput.",
        ),
        "declares_trigger_inputs": (
            declares_trigger_inputs(snapshots),
            "Expected entrypoint.expects to declare trigger inputs.",
        ),
        "uses_core_script_run_python": (
            has_action(snapshots, "core.script.run_python"),
            "Expected core.script.run_python action.",
        ),
        "linear_small_graph": (
            linear_small_graph(snapshots),
            "Expected graph with no branch fan-out and at most six actions.",
        ),
        "bounded_output": (
            bounded_output(snapshots),
            "Expected bounded samples or limits in deterministic output.",
        ),
        "no_scatter_gather": (
            no_scatter_gather(snapshots),
            "Expected no core.transform.scatter/gather for ordinary shaping.",
        ),
        "no_third_party_tools": (
            no_tools_actions(snapshots),
            "Expected no tools.* actions.",
        ),
        "uses_core_http_paginate": (
            has_action(snapshots, "core.http_paginate"),
            "Expected core.http_paginate action.",
        ),
        "treats_paginate_result_as_list": (
            treats_paginate_result_as_list(snapshots),
            "Expected direct list handling of core.http_paginate result.",
        ),
        "fetched_existing_workflow": (
            transcript_contains(transcript, "get_workflow"),
            "Expected get_workflow before editing.",
        ),
        "used_edit_workflow": (
            transcript_count(transcript, "edit_workflow") >= 1,
            "Expected edit_workflow.",
        ),
        "used_validate_only_edit": (
            transcript_contains(transcript, "validate_only")
            and transcript_contains(transcript, "true"),
            "Expected validate_only=true edit pass.",
        ),
        "layout_refs_match_actions": (
            layout_refs_match_actions(snapshots),
            "Expected layout action refs to match definition refs.",
        ),
        "changed_seed_workflow": (
            seed_workflow_id is not None and seed_workflow_id in changed_ids,
            "Expected changed_workflow_ids to include seeded workflow.",
        ),
        "uses_agent_action": (
            has_any_action(snapshots, {"ai.agent", "ai.preset_agent"}),
            "Expected ai.agent or ai.preset_agent.",
        ),
        "small_agentic_graph": (
            linear_small_graph(snapshots, limit=6),
            "Expected agentic graph to stay small.",
        ),
        "deterministic_prep_outside_agent": (
            deterministic_prep_outside_agent(snapshots),
            "Expected deterministic prep/handling outside agent.",
        ),
        "agent_without_broad_http_tool": (
            agent_without_broad_http_tool(snapshots),
            "Expected no broad core.http_request tool granted to agent.",
        ),
        "uses_workflow_execute": (
            has_action(snapshots, "core.workflow.execute"),
            "Expected core.workflow.execute.",
        ),
        "uses_workflow_alias": (
            workflow_execute_uses_alias(snapshots),
            "Expected workflow_alias on core.workflow.execute.",
        ),
        "explicit_batch_loop_options": (
            explicit_batch_loop_options(snapshots),
            "Expected batch loop strategy, batch size, and isolated failure behavior.",
        ),
        "explicit_wait_strategy": (
            explicit_wait_strategy(snapshots),
            "Expected explicit wait_strategy.",
        ),
        "valid_or_clarified": (
            bool(snapshots) or bool(final_response.get("notes")),
            "Expected a valid workflow or final clarification notes.",
        ),
        "provider_gating_note_or_core_http": (
            provider_gating_note_or_core_http(snapshots, final_response),
            "Expected core HTTP scaffolding or provider confirmation note.",
        ),
        "validated_workflow": (
            transcript_contains(transcript, "validate_workflow"),
            "Expected validate_workflow.",
        ),
        "ran_draft_workflow": (
            transcript_contains(transcript, "run_draft_workflow"),
            "Expected run_draft_workflow.",
        ),
        "inspected_execution": (
            transcript_contains(transcript, "get_workflow_execution")
            or transcript_contains(transcript, "list_workflow_executions"),
            "Expected execution inspection.",
        ),
        "used_update_workflow_metadata_only": (
            update_workflow_metadata_only(mcp_calls),
            "Expected update_workflow for metadata only, without definition_yaml or patch_ops.",
        ),
        "no_update_workflow_patch_ops": (
            no_update_workflow_patch_ops(mcp_calls),
            "Expected no patch_ops argument on update_workflow.",
        ),
        "created_table_with_valid_columns": (
            created_table_with_valid_columns(mcp_calls),
            "Expected create_table with valid uppercase column types and select options only on select fields.",
        ),
        "created_table_index_from_fetched_table": (
            created_table_index_from_fetched_table(mcp_calls),
            "Expected get_table followed by create_column_index using real table/column UUIDs.",
        ),
        "created_and_updated_case_fields": (
            created_and_updated_case_fields(mcp_calls),
            "Expected list_case_fields plus valid create_case_field and update_case_field calls.",
        ),
    }
    passed, detail = checks.get(item, (False, f"Unknown rubric item for {case.id}"))
    return CheckResult(item, passed, "" if passed else detail)


async def score_case(
    mcp: TracecatMCP,
    *,
    case: Case,
    agent: str,
    workspace_id: str,
    final_response: Mapping[str, Any],
    transcript_path: Path,
    final_path: Path,
    seed_workflow_id: str | None,
    duration_seconds: float,
) -> CaseResult:
    transcript_analysis = analyze_transcript(transcript_path)
    mcp_metrics = transcript_analysis.mcp_metrics
    checks: list[CheckResult] = []
    checks.append(
        CheckResult(
            "no_failed_mcp_schema_inputs",
            mcp_metrics.schema_input_failures == 0,
            f"{mcp_metrics.schema_input_failures} MCP schema/input failures.",
        )
    )
    checks.append(
        CheckResult(
            "no_failed_mcp_tool_calls",
            mcp_metrics.failed == 0,
            f"{mcp_metrics.failed} failed MCP tool calls.",
        )
    )

    transcript = transcript_text(transcript_path)
    checks.append(
        CheckResult(
            "no_repo_source_inspection",
            not source_inspection_detected(transcript),
            "Transcript shows Tracecat repo source inspection outside eval workspace.",
        )
    )

    workflow_ids = [str(x) for x in final_response.get("workflow_ids", [])]
    changed_ids = [str(x) for x in final_response.get("changed_workflow_ids", [])]
    if not workflow_ids:
        workflow_ids = transcript_analysis.workflow_ids
    if not changed_ids:
        changed_ids = transcript_analysis.changed_workflow_ids
    snapshots: list[WorkflowSnapshot] = []
    snapshot_workflow_ids = list(dict.fromkeys([*workflow_ids, *changed_ids]))
    for workflow_id in snapshot_workflow_ids:
        try:
            snapshots.append(
                await fetch_workflow_snapshot(
                    mcp, workspace_id=workspace_id, workflow_id=workflow_id
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    "fetch_workflow",
                    False,
                    f"{workflow_id}: {type(exc).__name__}: {exc}",
                )
            )

    schemas = await fetch_action_schemas(
        mcp,
        workspace_id=workspace_id,
        action_names=sorted(action_names(snapshots)),
    )
    if snapshots:
        checks.append(check_action_args_against_schemas(snapshots, schemas))

    for item in case.rubric:
        checks.append(
            evaluate_rubric_item(
                item,
                case=case,
                snapshots=snapshots,
                transcript=transcript,
                mcp_calls=transcript_analysis.mcp_calls,
                final_response=final_response,
                seed_workflow_id=seed_workflow_id,
            )
        )

    passed = all(check.passed for check in checks)
    return CaseResult(
        case_id=case.id,
        agent=agent,
        passed=passed,
        model=agent_model(agent),
        workflow_ids=workflow_ids,
        changed_workflow_ids=changed_ids,
        checks=checks,
        transcript_path=str(transcript_path),
        final_response_path=str(final_path),
        duration_seconds=duration_seconds,
        accuracy=accuracy_from_checks(checks),
        mcp_tool_calls=mcp_metrics.total,
        mcp_tool_successes=mcp_metrics.succeeded,
        mcp_tool_failures=mcp_metrics.failed,
        mcp_schema_input_failures=mcp_metrics.schema_input_failures,
        workflow_node_count=workflow_node_count(snapshots),
        branch_count=workflow_branch_count(snapshots),
    )


def accuracy_from_checks(checks: Sequence[CheckResult]) -> float:
    if not checks:
        return 0.0
    return sum(1 for check in checks if check.passed) / len(checks)


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    return [
        (match.group("lang").strip().lower(), match.group("body"))
        for match in re.finditer(
            r"```(?P<lang>[A-Za-z0-9_-]*)\n(?P<body>.*?)```",
            text,
            re.DOTALL,
        )
    ]


def as_patch_ops(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict) and isinstance(value.get("patch_ops"), list):
        value = value["patch_ops"]
    if (
        isinstance(value, list)
        and value
        and all(
            isinstance(item, dict) and "op" in item and "path" in item for item in value
        )
    ):
        return value
    return None


def validate_yaml_block(block: Any, label: str) -> list[CheckResult]:
    checks: list[CheckResult] = []

    def validatable_action(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or "action" not in item:
            return None
        action_name = item["action"]
        if isinstance(action_name, str) and ("<" in action_name or ">" in action_name):
            return None
        return item

    try:
        if isinstance(block, dict) and "definition" in block:
            WorkflowYamlPayload.model_validate(block)
            checks.append(CheckResult(f"{label}:workflow_yaml", True))
        elif isinstance(block, dict) and {"title", "entrypoint", "actions"}.issubset(
            block
        ):
            DSLInput.model_validate(block)
            checks.append(CheckResult(f"{label}:dsl_input", True))
        elif isinstance(block, list):
            for index, item in enumerate(block):
                if validatable_action(item) is not None:
                    action = {"ref": f"snippet_{index}", **item}
                    ActionStatement.model_validate(action)
            if any(validatable_action(item) is not None for item in block):
                checks.append(CheckResult(f"{label}:action_snippets", True))
        elif isinstance(block, dict) and isinstance(block.get("actions"), list):
            for index, item in enumerate(block["actions"]):
                if validatable_action(item) is not None:
                    action = {"ref": f"snippet_{index}", **item}
                    ActionStatement.model_validate(action)
            if any(validatable_action(item) is not None for item in block["actions"]):
                checks.append(CheckResult(f"{label}:action_snippets", True))
        elif validatable_action(block) is not None:
            action = {"ref": "snippet", **block}
            ActionStatement.model_validate(action)
            checks.append(CheckResult(f"{label}:action_snippet", True))
    except ValidationError as exc:
        checks.append(CheckResult(label, False, str(exc.errors()[0])))
    except Exception as exc:
        checks.append(CheckResult(label, False, f"{type(exc).__name__}: {exc}"))
    return checks


def yaml_action_fragments(text: str) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for lang, body in fenced_blocks(text):
        if lang not in {"yaml", "yml"}:
            continue
        try:
            parsed = yaml.safe_load(body)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, list):
            fragments.extend(
                item
                for item in parsed
                if isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("action"), str)
            )
        elif isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            fragments.extend(
                item
                for item in parsed["actions"]
                if isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("action"), str)
            )
        elif (
            isinstance(parsed, dict)
            and isinstance(parsed.get("ref"), str)
            and isinstance(parsed.get("action"), str)
        ):
            fragments.append(parsed)
    return fragments


def validate_prompt_action_signatures(text: str) -> list[CheckResult]:
    from tracecat_registry.core.http import http_paginate, http_request
    from tracecat_registry.core.python import run_python

    action_functions = {
        "core.script.run_python": run_python,
        "core.http_request": http_request,
        "core.http_paginate": http_paginate,
    }
    fragments = yaml_action_fragments(text)
    seen_actions: set[str] = set()
    checks: list[CheckResult] = []

    for fragment in fragments:
        action_name = fragment["action"]
        if action_name not in action_functions:
            continue
        seen_actions.add(action_name)
        action_refs = set(
            re.findall(r"ACTIONS\.([A-Za-z_][A-Za-z0-9_]*)", json.dumps(fragment))
        )
        upstream_stubs = [
            {"ref": ref, "action": "core.transform.reshape", "args": {"value": {}}}
            for ref in sorted(action_refs - {fragment["ref"]})
        ]
        actions = [*upstream_stubs, fragment]
        try:
            DSLInput.model_validate(
                {
                    "title": f"Prompt eval {fragment['ref']}",
                    "description": "Validate MCP prompt action fragment.",
                    "entrypoint": {"ref": actions[0]["ref"], "expects": {}},
                    "actions": actions,
                }
            )
            signature = inspect.signature(action_functions[action_name])
            params = set(signature.parameters)
            args = set(fragment.get("args", {}))
            required = {
                name
                for name, param in signature.parameters.items()
                if param.default is inspect.Parameter.empty
                and param.kind
                in {
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
            }
            unknown = args - params
            missing = required - args
            if unknown or missing:
                checks.append(
                    CheckResult(
                        f"prompt_action_signature:{fragment['ref']}",
                        False,
                        f"unknown={sorted(unknown)} missing={sorted(missing)}",
                    )
                )
            else:
                checks.append(
                    CheckResult(f"prompt_action_signature:{fragment['ref']}", True)
                )
        except Exception as exc:
            checks.append(
                CheckResult(
                    f"prompt_action_signature:{fragment.get('ref', 'unknown')}",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )

    missing_actions = set(action_functions) - seen_actions
    checks.append(
        CheckResult(
            "prompt_action_signature_coverage",
            not missing_actions,
            f"missing={sorted(missing_actions)}",
        )
    )
    return checks


def validate_prompt_result_shapes(text: str) -> CheckResult:
    action_refs = {
        fragment["ref"]: fragment["action"] for fragment in yaml_action_fragments(text)
    }
    list_result_refs = {
        ref
        for ref, action_name in action_refs.items()
        if action_name == "core.http_paginate"
    }
    invalid_dereferences: list[str] = []
    for ref in list_result_refs:
        invalid_dereferences.extend(
            re.findall(
                rf"ACTIONS\.{re.escape(ref)}\.result\.([A-Za-z_][A-Za-z0-9_]*)",
                text,
            )
        )
    return CheckResult(
        "prompt_expression_result_shapes",
        not invalid_dereferences,
        f"Invalid paginate result dereferences: {invalid_dereferences}",
    )


def registered_core_ai_action_names() -> set[str]:
    import tracecat_registry.core as core_pkg

    actions: set[str] = set()
    for module_info in pkgutil.walk_packages(
        core_pkg.__path__, core_pkg.__name__ + "."
    ):
        if "._" in module_info.name:
            continue
        module = importlib.import_module(module_info.name)
        for _, obj in inspect.getmembers(module):
            action_name = getattr(obj, "__tracecat_udf_key", None)
            if isinstance(action_name, str) and action_name.startswith(
                ("core.", "ai.")
            ):
                actions.add(action_name)
    return actions


def action_literals(text: str) -> set[str]:
    pattern = re.compile(
        r"(?<![A-Za-z0-9_.])(?:core|ai|tools)\."
        r"[A-Za-z0-9_<>{}.*-]+(?:\.[A-Za-z0-9_<>{}.*-]+)*"
    )
    return set(pattern.findall(text))


def validate_prompt_action_literals(text: str) -> CheckResult:
    registered = registered_core_ai_action_names()
    unknown_core_ai = sorted(
        literal
        for literal in action_literals(text)
        if literal.startswith(("core.", "ai."))
        and "*" not in literal
        and "<" not in literal
        and literal not in registered
    )
    concrete_tools = sorted(
        literal
        for literal in action_literals(text)
        if literal.startswith("tools.") and "*" not in literal and "<" not in literal
    )
    passed = not unknown_core_ai and not concrete_tools
    return CheckResult(
        "prompt_action_literals_registered",
        passed,
        f"unknown_core_ai={unknown_core_ai} concrete_tools={concrete_tools}",
    )


def registered_action_section_names(text: str) -> set[str]:
    match = re.search(
        r"## Registered Core and AI Actions\n(?P<body>.*?)(?:\n## |\Z)",
        text,
        re.DOTALL,
    )
    if not match:
        return set()
    return {
        literal
        for literal in action_literals(match.group("body"))
        if literal.startswith(("core.", "ai."))
        and "*" not in literal
        and "<" not in literal
    }


def validate_registered_action_section(text: str) -> CheckResult:
    actual = registered_core_ai_action_names()
    listed = registered_action_section_names(text)
    missing = sorted(actual - listed)
    extra = sorted(listed - actual)
    return CheckResult(
        "registered_core_ai_action_list_matches_registry",
        bool(listed) and not missing and not extra,
        f"missing={missing} extra={extra}",
    )


def prompt_facing_sources() -> list[PromptSource]:
    return [
        PromptSource("mcp_instructions", load_mcp_instructions()),
        PromptSource(
            "dsl_reference", load_server_string_literal("_DSL_REFERENCE_TEXT")
        ),
        PromptSource("best_practices_skill", SKILL_SOURCE.read_text(encoding="utf-8")),
    ]


def combine_prompt_sources(sources: Sequence[PromptSource]) -> str:
    return "\n".join(source.text for source in sources)


def validate_prompt_source_budgets(
    sources: Sequence[PromptSource],
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    seen = {source.name for source in sources}
    for source_name, max_chars in PROMPT_SOURCE_MAX_CHARS.items():
        if source_name not in seen:
            checks.append(
                CheckResult(
                    f"prompt_budget:{source_name}",
                    False,
                    "source is missing",
                )
            )
            continue
        source = next(item for item in sources if item.name == source_name)
        checks.append(
            CheckResult(
                f"prompt_budget:{source.name}",
                len(source.text) <= max_chars,
                f"{len(source.text)} chars > {max_chars}",
            )
        )
    return checks


def validate_prompt_fenced_blocks(sources: Sequence[PromptSource]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    parsed_blocks = 0
    for source in sources:
        for index, (lang, body) in enumerate(fenced_blocks(source.text), start=1):
            label = f"{source.name}:fence_{index}_{lang or 'plain'}"
            if lang == "json":
                try:
                    value = json.loads(body)
                    parsed_blocks += 1
                    checks.append(CheckResult(f"{label}:json_parse", True))
                    if patch_ops := as_patch_ops(value):
                        ops = [
                            JsonPatchOperation.model_validate(op) for op in patch_ops
                        ]
                        validate_patch_paths(
                            ops,
                            allowed_top_level_paths=ALLOWED_PATCH_ROOTS,
                        )
                        checks.append(CheckResult(f"{label}:json_patch", True))
                except Exception as exc:
                    checks.append(
                        CheckResult(label, False, f"{type(exc).__name__}: {exc}")
                    )
            elif lang in {"yaml", "yml"}:
                try:
                    value = yaml.safe_load(body)
                    parsed_blocks += 1
                    checks.append(CheckResult(f"{label}:yaml_parse", True))
                    checks.extend(validate_yaml_block(value, label))
                except Exception as exc:
                    checks.append(
                        CheckResult(label, False, f"{type(exc).__name__}: {exc}")
                    )

    checks.append(
        CheckResult(
            "fenced_blocks_parsed",
            parsed_blocks > 0,
            f"parsed {parsed_blocks} JSON/YAML fenced blocks",
        )
    )
    return checks


def validate_prompt_table_upsert_examples(
    sources: Sequence[PromptSource],
) -> CheckResult:
    upsert_pattern = re.compile(r"\bupsert\s*[:=]\s*[Tt]rue\b")
    unique_index_pattern = re.compile(
        r"(?i)(unique index|create_column_index|create_column_index|"
        r"unique\s*[:=]\s*true)"
    )
    unsafe_hits: list[str] = []

    for source in sources:
        lines = source.text.splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not upsert_pattern.search(line):
                continue
            start = max(0, line_number - 8)
            end = min(len(lines), line_number + 8)
            nearby_text = "\n".join(lines[start:end])
            if not unique_index_pattern.search(nearby_text):
                unsafe_hits.append(f"{source.name}:{line_number}:{line.strip()}")

    return CheckResult(
        "prompt_table_upserts_have_unique_index_guidance",
        not unsafe_hits,
        f"unsafe upsert examples={unsafe_hits}",
    )


def validate_prompt_loop_parallelism_guardrails(text: str) -> CheckResult:
    required_phrases = [
        "Prefer `core.script.run_python` loops over action-level `for_each`",
        "Prefer `core.script.run_python` loops over `core.transform.scatter` / `core.transform.gather`",
        "hurt the scheduler",
    ]
    missing = [phrase for phrase in required_phrases if phrase not in text]
    return CheckResult(
        "prompt_loop_parallelism_guardrails",
        not missing,
        f"missing={missing}",
    )


def validate_prompt_mcp_tool_argument_guardrails(text: str) -> CheckResult:
    required_phrases = [
        "not an MCP tool argument reference",
        "MCP tool schemas and tool docstrings are the source of truth",
        "do not pass `patch_ops`",
        "call `get_table`, then `create_column_index`",
        "Case field `type` must be an uppercase SqlType value",
        "under 63 characters",
    ]
    missing = [phrase for phrase in required_phrases if phrase not in text]
    return CheckResult(
        "prompt_mcp_tool_argument_guardrails",
        not missing,
        f"missing={missing}",
    )


def static_prompt_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    sources = prompt_facing_sources()
    text = sources[0].text
    dsl_text = sources[1].text
    prompt_facing_text = combine_prompt_sources(sources)
    checks.extend(validate_prompt_source_budgets(sources))

    secret_pattern = re.compile(
        r"(?i)(sk-[a-z0-9]{20,}|ghp_[a-z0-9]{20,}|xox[baprs]-[a-z0-9-]{20,})"
    )
    email_pattern = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
    secret_hits = secret_pattern.findall(text)
    email_hits = [
        match.group(0)
        for match in email_pattern.finditer(text)
        if not match.group(0).lower().endswith("@example.com")
    ]
    checks.append(
        CheckResult("no_secret_literals", not secret_hits, f"hits={secret_hits[:3]}")
    )
    checks.append(
        CheckResult("no_non_example_emails", not email_hits, f"hits={email_hits[:3]}")
    )

    checks.extend(validate_prompt_fenced_blocks(sources))
    checks.extend(validate_prompt_action_signatures(prompt_facing_text))
    checks.append(validate_prompt_result_shapes(prompt_facing_text))
    checks.append(validate_prompt_action_literals(prompt_facing_text))
    checks.append(validate_prompt_table_upsert_examples(sources))
    checks.append(validate_prompt_loop_parallelism_guardrails(prompt_facing_text))
    checks.append(validate_prompt_mcp_tool_argument_guardrails(prompt_facing_text))
    checks.append(validate_registered_action_section(dsl_text))
    return checks


def load_mcp_instructions() -> str:
    return load_server_string_literal("_MCP_INSTRUCTIONS")


def load_server_string_literal(name: str) -> str:
    server_path = REPO_ROOT / "tracecat/mcp/server.py"
    tree = ast.parse(server_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, str):
            raise TypeError(f"{name} is not a string literal")
        return value
    raise RuntimeError(f"{name} assignment not found")


async def run_agent_cases(args: argparse.Namespace) -> list[CaseResult]:
    cases = select_cases(load_cases(Path(args.cases_file)), args.cases)
    agents = parse_agents(args)
    run_id = timestamp()
    artifact_dir = (
        Path(args.output_dir) if args.output_dir else DEFAULT_ARTIFACT_ROOT / run_id
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    run_prefix = f"eval-tracecat-authoring-{run_id}"

    results: list[CaseResult] = []
    async with TracecatMCP(args.mcp_url, os.getenv("TRACECAT_MCP_BEARER_TOKEN")) as mcp:
        workspace_id = await choose_workspace(mcp)
        for agent in agents:
            for case in cases:
                case_dir = artifact_dir / "cases" / agent / case.id
                final_path = case_dir / "final.json"
                transcript_path = case_dir / "transcript.jsonl"
                workspace = isolated_workspace(case_dir)
                seed_workflow_id = None
                try:
                    if case.seed_workflow:
                        seed_workflow_id = await seed_case_workflow(
                            mcp,
                            workspace_id=workspace_id,
                            run_prefix=f"{run_prefix}-{agent}",
                            case_id=case.id,
                        )
                    prompt = build_agent_prompt(
                        case=case,
                        workspace_id=workspace_id,
                        run_prefix=f"{run_prefix}-{agent}",
                        seed_workflow_id=seed_workflow_id,
                    )
                    run = run_agent(
                        case=case,
                        workspace=workspace,
                        prompt=prompt,
                        transcript_path=transcript_path,
                        final_path=final_path,
                        schema_path=SCRIPT_DIR / "codex_output_schema.json",
                        mcp_url=args.mcp_url,
                        agent=agent,
                        agent_cmd=args.agent_cmd,
                    )
                    result = await score_case(
                        mcp,
                        case=case,
                        agent=agent,
                        workspace_id=workspace_id,
                        final_response=run.final_response,
                        transcript_path=transcript_path,
                        final_path=final_path,
                        seed_workflow_id=seed_workflow_id,
                        duration_seconds=run.duration_seconds,
                    )
                except Exception as exc:
                    mcp_metrics = analyze_transcript(transcript_path).mcp_metrics
                    result = CaseResult(
                        case_id=case.id,
                        agent=agent,
                        passed=False,
                        model=agent_model(agent),
                        transcript_path=str(transcript_path),
                        final_response_path=str(final_path),
                        error=f"{type(exc).__name__}: {exc}",
                        mcp_tool_calls=mcp_metrics.total,
                        mcp_tool_successes=mcp_metrics.succeeded,
                        mcp_tool_failures=mcp_metrics.failed,
                        mcp_schema_input_failures=mcp_metrics.schema_input_failures,
                        workflow_node_count=0,
                        branch_count=0,
                    )
                results.append(result)

    write_reports(artifact_dir, results)
    print(f"Wrote eval artifacts to {artifact_dir}")
    return results


def parse_agents(args: argparse.Namespace) -> list[str]:
    raw = args.agents if args.agents is not None else args.agent
    agents = [agent.strip() for agent in raw.split(",") if agent.strip()]
    if not agents:
        raise SystemExit("At least one agent must be specified.")
    valid = {"codex", "claude-code"}
    invalid = set(agents) - valid
    if invalid:
        raise SystemExit(f"Unsupported agents: {', '.join(sorted(invalid))}")
    return agents


def write_reports(artifact_dir: Path, results: Sequence[CaseResult]) -> None:
    matrix = performance_matrix(results)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "passed": all(result.passed for result in results),
        "performance_matrix": matrix,
        "results": [result.as_dict() for result in results],
    }
    write_json(artifact_dir / "report.json", payload)

    lines = [
        "# Tracecat authoring eval report",
        "",
        f"Generated: {payload['generated_at']}",
        f"Passed: {payload['passed']}",
        "",
        "## Performance matrix",
        "",
        "| Agent | Model | Cases | Pass rate | Avg accuracy | Avg duration | Avg MCP calls | Failed MCP calls | Schema/input failures | Avg workflow nodes | Avg branches | Key failures | Improvements |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in matrix:
        lines.append(
            "| {agent} | {model} | {cases} | {pass_rate:.0%} | "
            "{avg_accuracy:.0%} | {avg_duration_seconds:.1f}s | "
            "{avg_mcp_tool_calls:.1f} | {mcp_tool_failures} | "
            "{mcp_schema_input_failures} | {avg_workflow_nodes:.1f} | "
            "{avg_branch_count:.1f} | {key_failures} | {improvements} |".format(**row)
        )
    lines.append("")
    for result in results:
        lines.append(
            f"## {result.agent} / {result.case_id}: "
            f"{'PASS' if result.passed else 'FAIL'}"
        )
        if result.duration_seconds is not None:
            lines.append(f"- duration: {result.duration_seconds:.1f}s")
        if result.accuracy is not None:
            lines.append(f"- accuracy: {result.accuracy:.0%}")
        if result.mcp_tool_calls is not None:
            lines.append(f"- mcp_tool_calls: {result.mcp_tool_calls}")
        if result.mcp_tool_successes is not None:
            lines.append(f"- mcp_tool_successes: {result.mcp_tool_successes}")
        if result.mcp_tool_failures is not None:
            lines.append(f"- mcp_tool_failures: {result.mcp_tool_failures}")
        if result.mcp_schema_input_failures is not None:
            lines.append(
                f"- mcp_schema_input_failures: {result.mcp_schema_input_failures}"
            )
        if result.workflow_node_count is not None:
            lines.append(f"- workflow_node_count: {result.workflow_node_count}")
        if result.branch_count is not None:
            lines.append(f"- branch_count: {result.branch_count}")
        if result.error:
            lines.append(f"- error: {result.error}")
        if result.workflow_ids:
            lines.append(f"- workflow_ids: {', '.join(result.workflow_ids)}")
        if result.changed_workflow_ids:
            lines.append(
                f"- changed_workflow_ids: {', '.join(result.changed_workflow_ids)}"
            )
        failed = [check for check in result.checks if not check.passed]
        if failed:
            lines.append("- failed checks:")
            for check in failed:
                lines.append(f"  - {check.name}: {check.detail}")
        lines.append("")
    (artifact_dir / "report.md").write_text("\n".join(lines))


def performance_matrix(results: Sequence[CaseResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    model_groups = sorted(
        {(result.agent, result.model or "default") for result in results}
    )
    for agent, model in model_groups:
        agent_results = [
            result
            for result in results
            if result.agent == agent and (result.model or "default") == model
        ]
        cases = len(agent_results)
        passed = sum(1 for result in agent_results if result.passed)
        durations = [
            result.duration_seconds
            for result in agent_results
            if result.duration_seconds is not None
        ]
        mcp_tool_calls = [
            result.mcp_tool_calls
            for result in agent_results
            if result.mcp_tool_calls is not None
        ]
        mcp_tool_failures = [result.mcp_tool_failures or 0 for result in agent_results]
        mcp_schema_input_failures = [
            result.mcp_schema_input_failures or 0 for result in agent_results
        ]
        workflow_nodes = [
            result.workflow_node_count
            for result in agent_results
            if result.workflow_node_count is not None
        ]
        branch_counts = [
            result.branch_count
            for result in agent_results
            if result.branch_count is not None
        ]
        accuracies = [
            result.accuracy for result in agent_results if result.accuracy is not None
        ]
        failures = [
            check.name
            for result in agent_results
            for check in result.checks
            if not check.passed
        ]
        if failures:
            top_failures = sorted(set(failures))[:4]
            key_failures = ", ".join(top_failures)
            improvements = "Improve " + ", ".join(top_failures)
        elif any(result.error for result in agent_results):
            key_failures = "runtime errors"
            improvements = "Fix agent invocation/runtime errors"
        else:
            key_failures = "none"
            improvements = "Maintain current behavior; inspect transcripts for quality."
        rows.append(
            {
                "agent": agent,
                "model": model,
                "cases": cases,
                "pass_rate": passed / cases if cases else 0.0,
                "avg_accuracy": sum(accuracies) / len(accuracies)
                if accuracies
                else 0.0,
                "avg_duration_seconds": sum(durations) / len(durations)
                if durations
                else 0.0,
                "avg_mcp_tool_calls": sum(mcp_tool_calls) / len(mcp_tool_calls)
                if mcp_tool_calls
                else 0.0,
                "mcp_tool_failures": sum(mcp_tool_failures),
                "mcp_schema_input_failures": sum(mcp_schema_input_failures),
                "avg_workflow_nodes": sum(workflow_nodes) / len(workflow_nodes)
                if workflow_nodes
                else 0.0,
                "avg_branch_count": sum(branch_counts) / len(branch_counts)
                if branch_counts
                else 0.0,
                "key_failures": key_failures,
                "improvements": improvements,
            }
        )
    return rows


def discover_cluster_mcp_url() -> str | None:
    cluster_script = REPO_ROOT / "scripts/cluster"
    if not cluster_script.exists():
        return None
    try:
        completed = subprocess.run(
            [str(cluster_script), "ports"],
            cwd=REPO_ROOT,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"^\s*MCP:\s+(\S+)\s*$", completed.stdout, re.MULTILINE)
    if not match:
        return None
    return match.group(1)


def resolve_mcp_url(explicit_url: str | None) -> str:
    if explicit_url:
        return explicit_url
    if env_url := os.getenv("TRACECAT_EVAL_MCP_URL"):
        return env_url
    if cluster_url := discover_cluster_mcp_url():
        return cluster_url
    raise RuntimeError(
        "Could not discover a local Tracecat cluster MCP URL. "
        "Start one with `just cluster up -d`, set TRACECAT_EVAL_MCP_URL, "
        f"or pass --mcp-url {DEFAULT_DIRECT_MCP_URL} when running the MCP "
        "server directly."
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mcp-url",
        default=None,
        help=(
            "Tracecat MCP URL. Defaults to TRACECAT_EVAL_MCP_URL or the MCP URL "
            "reported by ./scripts/cluster ports."
        ),
    )
    parser.add_argument("--agent", default="codex", choices=["codex", "claude-code"])
    parser.add_argument(
        "--agents",
        default=None,
        help="Comma-separated agents to compare, e.g. codex,claude-code.",
    )
    parser.add_argument("--agent-cmd", default=None)
    parser.add_argument("--cases", default="all", help="Case id, group, or 'all'.")
    parser.add_argument(
        "--cases-file",
        default=str(SCRIPT_DIR / "cases.yaml"),
        help="Path to authoring eval cases YAML.",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--static-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.static_only:
        checks = static_prompt_checks()
        results = [
            CaseResult(
                case_id="static_prompt_checks",
                agent="static",
                passed=all(check.passed for check in checks),
                model="n/a",
                checks=checks,
                accuracy=accuracy_from_checks(checks),
            )
        ]
        artifact_dir = (
            Path(args.output_dir)
            if args.output_dir
            else DEFAULT_ARTIFACT_ROOT / f"{timestamp()}-static"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        write_reports(artifact_dir, results)
        print(f"Wrote static eval artifacts to {artifact_dir}")
        return 0 if results[0].passed else 1

    args.mcp_url = resolve_mcp_url(args.mcp_url)
    results = asyncio.run(run_agent_cases(args))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
