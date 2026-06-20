---
name: tracecat-qa
description: QA Tracecat product features in a real local cluster. Use when Codex is asked to QA, browser-test, smoke test, or manually verify Tracecat UI behavior, feature flows, or PR changes using the repo's `just cluster` stack and browser tooling.
---

# Tracecat QA

## Workflow

1. Build a best-effort understanding of the branch, PR, or diff before testing. Inspect the user's request, current branch name, changed files, `git diff`, and, when available, the PR title/body/comments with `gh pr view`.
2. Follow linked work items when they are discoverable. If the branch, PR body, commits, or changed files mention Linear issue keys, search or open those issues with available Linear tools; use them to understand intent, acceptance criteria, and target user roles. Treat unavailable GitHub or Linear access as a limitation, not a reason to stop.
3. Infer the most relevant user flows from that intent plus the touched routes, components, services, permissions, and data shapes. Choose a focused QA path and proceed without asking the user to confirm the plan unless the next step is destructive, requires credentials the agent cannot obtain, or would affect external production systems.
4. Start or reuse a local Tracecat cluster with `just cluster up`; prefer `just cluster up -d` when background services are enough. If command syntax is unclear, inspect the command reference in `scripts/cluster`.
5. Run `just cluster ports` and use the UI URL it prints, especially the Caddy/public app URL. Do not manually build or browse to `localhost:<port>`; using a raw localhost port can hit CSRF/session-origin issues.
6. Open the UI with browser tooling:
   - In the Codex app, use the built-in in-app browser.
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
