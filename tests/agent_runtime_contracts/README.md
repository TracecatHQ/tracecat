# Claude callable readiness contract

ENG-1884 tests the real `ClaudeAgentRuntime`, installed Python SDK, bundled Claude
CLI, local FastMCP servers, and a scripted Anthropic Messages endpoint. The tests
record the CLI's actual model requests. They do not infer readiness from the
runtime's option objects.

## Tested pair

- `claude-agent-sdk==0.2.160` (unchanged)
- Bundled Claude Code `2.1.283`
- `fastmcp==3.2.0` (unchanged)

`test_pinned_runtime_versions` deliberately fails if the SDK/CLI pair changes.
Update it and this matrix only after running the compatibility suite on the new
pair. No dependency upgrade or production feature enablement is needed here.

## Results

The selected tool is `required_lookup`; `unrelated_lookup` is a control. Both
are permitted by the test agent's configuration. Only the selected tool carries
`_meta["anthropic/alwaysLoad"] = true` in the MCP listing.

| Agent scope | MCP transport | Runtime provider configuration | First request and direct call |
| --- | --- | --- | --- |
| Root | HTTP and stdio | Anthropic; custom-model-provider; Ollama; vLLM; LiteLLM; OpenRouter | Selected schema present and callable without ToolSearch. Unrelated schema deferred. |
| Root | HTTP and stdio | Bedrock | Both schemas present and callable. ToolSearch absent, preserving existing Bedrock behavior. |
| Direct child | HTTP and stdio | All seven configurations above | Both schemas present and selected tool callable. ToolSearch absent for this explicit child tool set. |

These 28 cases are **synthetic protocol evidence**. All model requests terminate
at the local fixture. They exercise provider-dependent runtime options and route
names with managed routing (`passthrough=False`); they do not contact Anthropic,
AWS, a gateway, or a real model. Live-provider compatibility, direct passthrough,
production Linux/nsjail isolation, and full workflow/broker execution remain
unqualified by this fixture. The separate CI job runs the protocol suite on Linux.

Additional controls run over both HTTP and stdio:

- Without tool or server metadata, both root tools remain deferred. A real
  ToolSearch call loads only the selected definition, which can then execute.
- Server configuration `alwaysLoad: true` loads both schemas. It is too broad
  for selectively loading a mentioned tool on a shared server.
- Tool metadata selectively preloads the root tool and preserves the unrelated
  tool's deferred behavior. Current CLI requests omit deferred schemas and
  advertise their names in a ToolSearch reminder; tests account for that wire
  format instead of requiring a `defer_loading` field on an absent schema.

Direct-child results show existing eager behavior for the explicit child tool
set; they do not establish selective deferral within that child set. Do not
advertise child selectivity based on the root result.

## Runtime behavior covered

The tests preserve runtime option construction, approval hooks, tool-name
mapping, streaming, session persistence, and the SDK control protocol. They
replace the trusted HTTP endpoint with a local server and disable sandbox
isolation for the local subprocess. Stdio servers enter through the existing
`SandboxAgentConfig.mcp_servers` path. No permission bypass is installed.

The suite verifies that eager loading still emits an approval request instead
of executing an approval-gated tool, emits stream events, resumes real CLI JSONL
history with its prior tool result, and cancels a pending model request using the
runtime interrupt API (stdio fixture). Existing unit tests separately cover
approval continuation and hook/cancellation edge cases. These tests do not run
an approved action through the production broker.

This CLI launches explicit children asynchronously. The scripted parent waits
for the child's tool result before ending its turn. Ending the synthetic parent
immediately closes the SDK control stream and can cancel the child's permission
hook during stdio startup; that would test premature teardown, not readiness.

## Run

```sh
uv sync --frozen
uv run --no-sync python -m pytest \
  --confcutdir=tests/agent_runtime_contracts tests/agent_runtime_contracts -q
```

Run serially. Each case creates temporary HOME/config/session directories and
loopback-only model and MCP endpoints, with synthetic authentication. No provider
credentials or database services are required. The narrow conftest boundary
avoids unrelated repository-wide service fixtures. CI runs this command in its
own read-only job.

For the existing runtime regressions:

```sh
uv run --no-sync python -m pytest --confcutdir=tests/unit \
  tests/unit/test_agent_runtime.py tests/unit/test_agent_mcp_metadata.py \
  tests/unit/test_agent_mcp_utils.py -q
```

## Implementation decision for ENG-1906

Use per-tool MCP metadata for required root tools in the agent's scoped listing.
Do not mutate the shared registry or disable tool search globally. Preserve the
current eager behavior of non-search and explicit-child configurations. Treat
schema availability, authorization, approval, and successful execution as
separate checks. Keep reference activation gated until runtime binding and the
remaining preparation/replay work are implemented and qualified.
