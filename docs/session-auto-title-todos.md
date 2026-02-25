# Session auto-title todos

Last updated: 2026-02-25

## Findings to address

- [x] F1: First-prompt auto-title overwrites seeded session title immediately.
- [x] F2: Applies to all `AgentSession` entity types (no type gating).
- [x] F3: Uses direct PydanticAI call (no executor/sandbox path for title generation).
- [x] F4: Workflow-created sessions attempt auto-title from initial prompt.

## Implementation tasks

- [x] Add direct title generator module at `tracecat/agent/session/title_generator.py`.
- [x] Add session service methods:
  - [x] `_is_first_prompt_for_session(...)`
  - [x] `auto_title_session_on_first_prompt(...)`
- [x] Hook `run_turn(...)` to attempt auto-title before workflow spawn.
- [x] Extend `CreateSessionInput` with `initial_user_prompt`.
- [x] Pass workflow prompt into `CreateSessionInput.initial_user_prompt`.
- [x] Call auto-title from `create_session_activity(...)` when prompt exists.
- [x] Add structured logs:
  - [x] `session_auto_title_attempt`
  - [x] `session_auto_title_skip`
  - [x] `session_auto_title_success`
  - [x] `session_auto_title_failure`

## Credential plumbing

- [x] Ensure direct `get_model(...)` can read org/workspace credentials from registry secret context.
- [x] Keep existing env sandbox context behavior.

## Timeout tuning

- [x] Increase title-generation timeout to reduce false empty/failure outcomes under real latency.

## Validation

- [x] Unit tests added/updated and passing.
- [x] Ruff checks passing.
- [x] Basedpyright passing.
- [x] Docker compose runtime verification: new user prompt produces `session_auto_title_success`.
