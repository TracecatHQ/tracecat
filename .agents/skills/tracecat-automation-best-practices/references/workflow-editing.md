# Editing existing workflows with MCP

Prefer targeted `edit_workflow` patches over replacing full YAML.

- Patch against the `draft_document` returned by `get_workflow`; action paths start under
  `/definition/actions/...`, not `/actions/...`. Valid targeted paths include
  `/definition/actions/N/args/script`, `/definition/actions/N/args/dependencies`,
  `/definition/actions/N/ref`, `/definition/actions/N/depends_on/...`, and
  `/layout/actions/N/ref`.
- Before patching, call `get_workflow`, use the returned `draft_revision` as `base_revision`,
  and build action indexes from the latest returned action list. Do not reuse stale indexes
  after another edit.
- For nontrivial edits, call `edit_workflow` with `validate_only: true`, then apply the same
  patch with `validate_only: false` against the same `base_revision`.
- Treat `draft_revision` as sequential state. After a successful write, use the returned
  revision before the next patch. Do not run parallel `edit_workflow` calls against the same
  draft — each successful edit advances `draft_revision`; apply chunks sequentially,
  refetching before each chunk when payload size or reviewability argues against one large
  patch.
- When adding or renaming actions, update the action `ref`, every dependent `depends_on` edge,
  and the matching layout action `ref` in the same edit.
- JSON Patch operations apply in order. RFC 6902 array semantics: `/-` appends, and indexes
  shift after `add`, `remove`, and `move`.
- Use `update_workflow(definition_yaml=..., update_mode="replace")` only when a targeted patch
  is not sufficient. Never broad-replace from local YAML without comparing against the remote
  draft for production-only values and remote-only improvements.
- After remote edits, run `validate_workflow`, read back the draft to confirm intended refs
  and important fields landed, then run a draft execution for nontrivial changes. Publish only
  when explicitly authorized.

## Definition and trigger inputs

- Complete workflow YAML belongs under top-level `definition:`. A bare top-level `entrypoint:`
  may parse but will not register trigger inputs for runtime expressions.
- Declare `definition.entrypoint.expects` for every trigger input used as `TRIGGER.*`;
  validation can pass even when a later draft run lacks undeclared trigger values.

## Action naming

Name action refs by their externally visible effect: `create_<scope>_tables`, `fetch_<noun>`,
`aggregate_<noun>`, `build_<noun>`, `update_<noun>_table(s)`. Avoid `sync_*` and `persist_*`
for table writes because they do not state the write target clearly.

## Static checks before remote updates

Before remote updates, inspect the workspace draft and run applicable static checks on
generated or exported workflow content: YAML parse, embedded Python `ast.parse`,
ref/dependency resolution, no accidental scatter/gather, no redundant diamonds, and
`git diff --check`.
