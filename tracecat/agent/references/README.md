# Reference contracts (v1)

This package defines mention syntax and internal messages. It does not resolve
resources, change an agent's permissions, or enable mention execution.

A mention is a Markdown link. Its destination identifies the resource; its label
is presentation text:

```markdown
[HTTP request](tracecat-ref://v1/tool/core.http_request)
```

The seven destinations are:

| Kind | Identity segments after `tracecat-ref://v1/` |
| --- | --- |
| Registry tool | `tool/<canonical-action-key>` |
| MCP integration | `mcp-server/<integration-uuid>` |
| MCP tool | `mcp-tool/<integration-uuid>/<tool-name>` |
| Table | `table/<table-uuid>` |
| Workflow | `workflow/<workflow-uuid>` |
| Skill | `skill/<skill-uuid>` |
| Agent | `agent/<agent-uuid>` |

UUIDs use lowercase, hyphenated spelling. URI segments are decoded once. Encoded
separators, double encoding, controls, queries, fragments, unsupported versions,
and invalid identities are errors. Serialization returns the canonical spelling.
Registry action keys and MCP tool names are limited to 255 characters.
No URI is fetched as a network address.

`parse_markdown_references` uses CommonMark link tokens, including reference-style
links and autolinks. It ignores code, images, YAML frontmatter, raw HTML blocks,
and links inside inline HTML spans. CommonMark determines HTML block boundaries;
a blank line can end a type-7 HTML block. Plain `@name` text grants nothing.
`scan_markdown_files` reads every UTF-8 `.md` or `.markdown` manifest member,
including supporting files, and reports invalid text files.

Occurrences retain their source path and one-based line and column. CRLF and CR
are normalized and an initial BOM is removed. Columns count Unicode code points,
not UTF-16 units or display cells. Syntax failures produce typed diagnostics;
consumers must reject executable preparation when diagnostics are present.
Resource lookup, authorization, cycle detection, and graph limits are separate
preparation work.

## Data flow

1. A trusted admission service constructs `AuthoredReferenceInput` and
   `ExecutionAuthority`. A claimed origin in untrusted JSON is not authority.
   Manual declarations and authorized overrides remain separate contributions.
2. `ReferencePreparationInput` carries those inputs and the selected backend and
   harness. `PromptContext` travels separately and cannot seed the graph.
3. A future resolver produces `ResolvedReferenceSnapshot`, with selected versions,
   scoped graph edges, callable schemas, registry locks, and logical artifacts.
   The preparation implementation must validate graph consistency and policy;
   model validation alone does not do that work.
4. Durable orchestration carries the compact `ReferenceSnapshotRef`.
5. A native runtime installs the snapshot and returns `RuntimeReferenceBinding`
   as readiness evidence. That binding cannot grant new authority.

These messages contain no credentials, Claude SDK objects, native history, or
sandbox installation paths. Their wire schema version is explicit; unknown
fields and unsupported versions are rejected. Logical artifact paths are relative.
Callable schemas and snapshot registry maps are recursively immutable in memory;
serialization produces ordinary JSON objects and arrays. `ReferenceRegistryLock`
reuses registry binding validation without retaining a mutable execution lock.
An `AgentBackend` advertises optional capabilities per harness. The inherited
empty capability tuple means unsupported, including for existing backends.

The shared corpus lives in `tests/fixtures/agent_references/conformance.json`.
Python tests verify syntax, diagnostics, and locations. Jest runs the TypeScript
URI implementation and bundles the actual TipTap Markdown lexer against the same
corpus. The editor is not wired to these helpers in this change.

```sh
uv run python -m pytest --confcutdir=tests/unit/agent_references tests/unit/agent_references
pnpm -C frontend test --runInBand agent-reference
```

The narrow pytest invocation avoids the repository's unrelated autouse database,
Redis, and object-store fixtures. These contracts need none of those services.
