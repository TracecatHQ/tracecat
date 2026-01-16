# Actionables (from 2026-01-14 findings, rechecked 2026-01-15)

This is a condensed, "do the work" checklist extracted from `2026-01-14-local-command-caveatcaveat-the-messages-below-w.txt`, with the 2026-01-15 double-check corrections applied.

## P1 — correctness / likely user-visible

1) Fix wrong `asyncio` import — **ADDRESSED** (40f60e6f2)
- Where: `tracecat/dsl/action.py:9`
- Change: replace `from aiocache.backends.memory import asyncio` with stdlib `import asyncio`.
- Why: current import is from aiocache internals; `asyncio.Runner()` should come from stdlib.

2) Fix nested template execution in the in-process template runner path — **ADDRESSED** (3d40c5ac2)
- Where: `tracecat/executor/service.py:322-327`
- Change: when `action.is_template`, do **not** call `_run_template_steps(..., context)` with the *parent* `TemplateExecutionContext`.
  - Instead, execute the nested template with a fresh context whose `inputs` are the nested template's validated args, and whose `steps` starts empty (but reuse parent `SECRETS`/`ENV`/`VARS`).
- Why: nested templates currently evaluate against the parent template's `inputs`, not their own.
- Note: service-layer backend orchestration (`_execute_template_action` / `_invoke_step`) already uses per-template contexts; this bug is specific to the in-process runner path.

3) Materialize operand before building agent args (and preset agent args) — **ADDRESSED** (830e59ab6)
- Where: `tracecat/dsl/action.py:480-495`
- Change: `build_agent_args_activity` should mirror `resolve_return_expression_activity` by calling `materialize_context()` before `eval_templated_object()`.
  - Do the same for `build_preset_agent_args_activity`.
- Why: agent args expressions need raw values when prior results are `ExternalObject`/`CollectionObject`.

## P2 — semantics / consistency issues

4) Respect `limit=0` in `get_collection_page` — **ADDRESSED** (c8e657cc9)
- Where: `tracecat/storage/collection.py:271`
- Change: replace `limit = limit or (collection.count - offset)` with explicit `None` handling (so `0` means "return 0 items").
- Acceptance: `limit=None` returns remaining items; `limit=0` returns `[]`.

5) Fix `TaskResult.is_externalized()` for `CollectionObject` — **ADDRESSED** (e31a7eed8)
- Where: `tracecat/dsl/schemas.py:217-219`
- Change: treat `result.type == "collection"` as externalized too (since it's stored in blob storage).

6) Fix scatter `result_typename` handling (was mis-classified previously) — **ADDRESSED** (308c59931)
- Where: `tracecat/storage/utils.py:177-182` and scatter creation in `tracecat/dsl/scheduler.py:770-775`
- Current behavior:
  - Scatter items are created with `result_typename="collection_item"` (not a Python type).
  - `resolve_execution_context()` overwrites `result_typename` to `type(resolved_data).__name__`, and does not apply `collection_index`, so scatter items end up looking like `result_typename="list"` even though the *logical* result is the indexed item.
- Decide and implement one consistent semantic:
  - If `result_typename` should reflect the *logical item* type for scatter: apply `collection_index` during `resolve_execution_context()` and set typename from the extracted item; consider also setting a meaningful `item_typename` when creating scatter TaskResults.
  - If it's meant to reflect the *stored envelope* type, rename/introduce a separate field (bigger schema change).
- **Resolution**: Applied `collection_index` to extract actual item and set `result_typename` from the extracted item type.

## P3 — cleanup / docs / maintainability

7) Remove unreachable code after `raise RuntimeError(...)` — **ADDRESSED** (a3f7b6f1c)
- Where: `tracecat/dsl/workflow.py:395-406`
- Change: remove the dead block after the raise, or implement schedule trigger inputs and move the raise accordingly.

8) Fix config docstrings vs defaults (pick one source of truth) — **ADDRESSED** (dec8a77bb)
- Where: `tracecat/config.py:276-295`
- Mismatches:
  - `TRACECAT__RESULT_EXTERNALIZATION_ENABLED` defaults to `"true"`, doc says default false.
  - `TRACECAT__RESULT_EXTERNALIZATION_THRESHOLD_BYTES` defaults to `0`, doc says default `262144`.
- Action: decide intended defaults, update code or docstrings, and add/adjust a regression test if behavior is relied upon.
- **Resolution**: Updated docstrings to match actual code defaults (true, 0).

9) Remove or use `_cache` — **ADDRESSED** (4f2309c7e)
- Where: `tracecat/dsl/action.py:60`
- Change: `_cache` is currently unused; remove it or wire it into an actual caching path.
- **Resolution**: Removed unused `_cache` and the `aiocache.Cache` import.
