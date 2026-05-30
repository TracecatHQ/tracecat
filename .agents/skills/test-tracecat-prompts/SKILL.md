---
name: test-tracecat-prompts
description: Use when evaluating Tracecat MCP prompts, automation-authoring instructions, or agent behavior with the local Tracecat prompt eval harness, including static MCP prompt checks, live local workflow-authoring evals, and Codex vs Claude Code performance comparisons.
---

# Test Tracecat Prompts

## Workflow

Use the local harness at `scripts/evals/tracecat_authoring/run_local.py`. Keep
these evals local-only; do not wire them into CI unless the user explicitly asks.

1. For prompt/schema regressions that do not need LLM calls, run:

```bash
uv run python scripts/evals/tracecat_authoring/run_local.py --static-only
```

2. For live generation evals, use the active local cluster MCP URL. Check it
with `./scripts/cluster ports` and use the `MCP:` URL. If no local cluster is
running, start one with `just cluster up -d`. Use `http://127.0.0.1:8099/mcp`
only when you are intentionally running the MCP server directly outside the
cluster helper.

```bash
MCP_URL="$(./scripts/cluster ports | awk '/MCP:/ {print $2}')"
uv run python scripts/evals/tracecat_authoring/run_local.py \
  --mcp-url "$MCP_URL" \
  --cases smoke \
  --agent codex
```

3. To compare Claude Code against Codex, run both agents against the same cases:

```bash
MCP_URL="$(./scripts/cluster ports | awk '/MCP:/ {print $2}')"
TRACECAT_EVAL_CLAUDE_MODEL=claude-opus-4-7 \
uv run python scripts/evals/tracecat_authoring/run_local.py \
  --mcp-url "$MCP_URL" \
  --cases smoke \
  --agents codex,claude-code
```

Use `TRACECAT_EVAL_WORKSPACE_ID` to pin the workspace,
`TRACECAT_MCP_BEARER_TOKEN` when the local MCP server requires auth,
`TRACECAT_EVAL_MODEL` for Codex, and `TRACECAT_EVAL_CLAUDE_MODEL` for Claude
Code.

## Interpretation

Static and unit tests are fast because they parse prompts and local schemas only.
They do not call live LLMs. Live agent evals are slower, create or edit workflows
in the selected local workspace, and spend model credits.

Read `.tracecat/evals/tracecat_authoring/<timestamp>/report.md` first. The
report starts with a performance matrix:

- Speed is wall-clock duration per agent run.
- Accuracy is the fraction of structural/rubric checks passed.
- MCP efficiency and reliability come from on-disk transcripts: total MCP tool
  calls, successful calls, failed calls, and schema/input failures.
- Improvements are derived from failed rubric checks, invocation errors, and
  failed transcript-derived MCP checks.

Claude Code may return prose instead of strict structured JSON. Inspect
`transcript.jsonl` and `final.json` under the case artifact directory; the
transcript is the source of truth for MCP tool-call behavior.

## Guardrails

- Keep fixtures synthetic: `example.com`, placeholder ids, no real people, no
  real tokens.
- Do not treat existing unit tests as live generation coverage.
- Do not inspect or score unrelated dirty repo changes while running the evals.
- For existing workflow edit cases, expect the agent to use `get_workflow`,
  validate-only `edit_workflow`, then apply the edit using the original
  `draft_revision` returned by `get_workflow`.
