# Tracecat Automation Authoring Evals

Local-only eval harness for Tracecat automation authoring through the local
Tracecat MCP server. It runs Codex from an isolated temporary workspace that
contains only the generic automation skill, then scores created or edited
workflows through MCP and parser/schema checks.

## Static prompt checks

```bash
uv run python scripts/evals/tracecat_authoring/run_local.py --static-only
```

Static checks parse fenced JSON/YAML blocks in the MCP instructions, validate
JSON Patch examples structurally, validate complete workflow YAML examples with
`WorkflowYamlPayload`, validate action snippets inside minimal DSL wrappers, and
run budget plus PII/secret regex checks. These checks are parser/schema driven,
not brittle string comparisons.

## Local MCP URL

Live evals should target the active local cluster MCP server:

```bash
MCP_URL="$(./scripts/cluster ports | awk '/MCP:/ {print $2}')"
```

If no local cluster is running, start one with `just cluster up -d`. Use
`http://127.0.0.1:8099/mcp` only when intentionally running the MCP server
directly outside the cluster helper.

## Smoke agent eval

Start a local Tracecat app plus MCP server, then run:

```bash
MCP_URL="$(./scripts/cluster ports | awk '/MCP:/ {print $2}')"
uv run python scripts/evals/tracecat_authoring/run_local.py \
  --mcp-url "$MCP_URL" \
  --cases smoke \
  --agent codex
```

The runner uses `TRACECAT_EVAL_WORKSPACE_ID` when set. Otherwise it uses the
first workspace returned by `list_workspaces`. If the MCP server
requires a bearer token, set `TRACECAT_MCP_BEARER_TOKEN`.

## Codex vs Claude Code

Run both agents against the same case set:

```bash
MCP_URL="$(./scripts/cluster ports | awk '/MCP:/ {print $2}')"
TRACECAT_EVAL_CLAUDE_MODEL=claude-opus-4-7 \
uv run python scripts/evals/tracecat_authoring/run_local.py \
  --mcp-url "$MCP_URL" \
  --cases smoke \
  --agents codex,claude-code
```

`report.md` and `report.json` include a performance matrix with per-agent model,
pass rate, average rubric accuracy, average wall-clock duration, transcript MCP
tool-call count, failed MCP calls, schema/input failures, workflow node count,
branch count, and failed-check-driven improvement notes. Claude Code may return
prose; transcript-derived MCP behavior is the source of truth for tool-call
reliability.

## Full local eval

```bash
uv run python scripts/evals/tracecat_authoring/run_local.py --agent codex
```

Optional environment:

- `TRACECAT_EVAL_WORKSPACE_ID`: workspace id to author into.
- `TRACECAT_MCP_BEARER_TOKEN`: bearer token for local MCP calls.
- `TRACECAT_EVAL_MODEL`: model passed to `codex exec --model`.
- `TRACECAT_EVAL_CLAUDE_MODEL`: model passed to `claude --model`; defaults to
  `claude-opus-4-7` for the comparison path.
- `TRACECAT_EVAL_AGENT_TIMEOUT_SECONDS`: per-case agent subprocess timeout;
  defaults to 900 seconds.
- `TRACECAT_EVAL_CODEX_BYPASS_APPROVALS=1`: run Codex with
  `--dangerously-bypass-approvals-and-sandbox` for local, disposable eval
  sandboxes where MCP calls must execute without interactive approval.

Artifacts are written under ignored
`.tracecat/evals/tracecat_authoring/<timestamp>/`:

- `report.json`: machine-readable case and rubric results.
- `report.md`: concise human-readable summary.
- `cases/<agent>/<case_id>/transcript.jsonl`: agent JSON event stream.
- `cases/<agent>/<case_id>/final.json`: final structured agent response.
- `cases/<agent>/<case_id>/workspace/`: isolated temporary agent workspace.

Use `--agent-cmd` to override the agent command while keeping the same scoring
harness. The command may use `{workspace}`, `{final}`, `{schema}`, `{mcp_url}`,
`{case_id}`, and `{prompt}` placeholders; if `{prompt}` is omitted, the runner
appends the prompt as the final argument. The default Codex invocation is
non-interactive, ephemeral, and pointed only at the supplied local Tracecat MCP
URL.
