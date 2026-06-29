---
name: tracecat-qa
description: QA Tracecat product features in a real local cluster. Use for QA, browser tests, smoke tests, or manual verification of Tracecat UI flows and PR changes with `just cluster`. In Codex desktop app sessions, use `browser:control-in-app-browser` first; use Chrome DevTools only after the in-app browser path concretely fails or when the user asks for Chrome.
---

# Tracecat QA

## Browser Tool Selection

For Codex desktop app sessions, always use the `browser:control-in-app-browser`
skill for Tracecat UI QA.

Before opening a Tracecat URL in the Codex app:

1. Read `browser:control-in-app-browser/SKILL.md`.
2. Follow that skill's browser setup and control instructions.
3. If direct in-app browser tools are not visible, follow that skill's tool
   discovery path, including searching for `node_repl js`, before declaring the
   in-app browser unavailable.
4. Use the in-app browser's documented APIs for navigation, DOM inspection,
   screenshots, console, and network checks.

Do not call `mcp__chrome_devtools` just because Chrome DevTools tools are
exposed directly by tool discovery.

Use Chrome DevTools only when one of these is true:

- The user explicitly asks for Chrome.
- The task depends on the user's existing Chrome profile, cookies, session, or
  extensions.
- The in-app browser skill or tool path is unavailable or fails after a concrete
  attempt.

If falling back from the in-app browser to Chrome DevTools, state the reason in
the QA report, including what in-app browser setup step failed.

## Workflow

1. Build a best-effort understanding of the branch, PR, or diff before testing. Inspect the user's request, current branch name, changed files, `git diff`, and, when available, the PR title/body/comments with `gh pr view`.
2. Follow linked work items when they are discoverable. If the branch, PR body, commits, or changed files mention Linear issue keys, search or open those issues with available Linear tools; use them to understand intent, acceptance criteria, and target user roles. Treat unavailable GitHub or Linear access as a limitation, not a reason to stop.
3. Infer the most relevant user flows from that intent plus the touched routes, components, services, permissions, and data shapes. Choose a focused QA path and proceed without asking the user to confirm the plan unless the next step is destructive, requires credentials the agent cannot obtain, or would affect external production systems.
4. Start or reuse a local Tracecat cluster with `just cluster up`; prefer `just cluster up -d` when background services are enough. If command syntax is unclear, inspect the command reference in `scripts/cluster`.
5. Run `just cluster ports` and use the UI URL it prints, especially the Caddy/public app URL. Do not manually build or browse to `localhost:<port>`; using a raw localhost port can hit CSRF/session-origin issues.
6. Open the UI with browser tooling:
   - In the Codex app, use `browser:control-in-app-browser` first.
   - Outside the Codex app, use the Chrome DevTools MCP.
7. Exercise the inferred feature flows through the real UI. Prefer real cluster behavior over mock-only checks unless the user explicitly asks for mocks.
8. Inspect visible UI state, console errors, failed network requests, and relevant service logs. Use `just cluster logs <service>` or `just cluster restart <service>` when needed.
9. Report the inferred intent, cluster URL used, steps performed, pass/fail status, screenshots or observations when useful, and any blockers.

## Cluster Safety

- Before starting a new cluster, check whether an existing Tracecat cluster is already running if doing so could affect the user's environment.
- Do not remove volumes or run destructive cleanup commands such as `just cluster rm` unless the user explicitly asks and confirms data loss is acceptable.
- If QA created a cluster only for this task, either leave it running and say so, or stop it with `just cluster down` when that is clearly appropriate.

## Blockers

If the app shell fails before reaching the feature, treat that as a QA blocker. Capture the exact browser error, relevant console/network details, and service logs instead of switching to a mock flow that no longer proves the requested user experience.
