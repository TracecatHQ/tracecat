---
name: tracecat-qa
description: QA Tracecat product features in a real local cluster. Use when Codex is asked to QA, browser-test, smoke test, or manually verify Tracecat UI behavior, feature flows, or PR changes using the repo's `just cluster` stack and browser tooling.
---

# Tracecat QA

## Workflow

1. Identify the feature, route, expected behavior, required role/scopes, and any setup data from the user's request and the current diff.
2. Start or reuse a local Tracecat cluster with `just cluster up`; prefer `just cluster up -d` when background services are enough. If command syntax is unclear, inspect the command reference in `scripts/cluster`.
3. Run `just cluster ports` and use the UI URL it prints, especially the Caddy/public app URL. Do not manually build or browse to `localhost:<port>`; using a raw localhost port can hit CSRF/session-origin issues.
4. Open the UI with browser tooling:
   - In the Codex app, use the built-in in-app browser.
   - Outside the Codex app, use the Chrome DevTools MCP.
5. Exercise the feature through the real UI. Prefer real cluster behavior over mock-only checks unless the user explicitly asks for mocks.
6. Inspect visible UI state, console errors, failed network requests, and relevant service logs. Use `just cluster logs <service>` or `just cluster restart <service>` when needed.
7. Report the QA result with the cluster URL used, steps performed, pass/fail status, screenshots or observations when useful, and any blockers.

## Cluster Safety

- Before starting a new cluster, check whether an existing Tracecat cluster is already running if doing so could affect the user's environment.
- Do not remove volumes or run destructive cleanup commands such as `just cluster rm` unless the user explicitly asks and confirms data loss is acceptable.
- If QA created a cluster only for this task, either leave it running and say so, or stop it with `just cluster down` when that is clearly appropriate.

## Blockers

If the app shell fails before reaching the feature, treat that as a QA blocker. Capture the exact browser error, relevant console/network details, and service logs instead of switching to a mock flow that no longer proves the requested user experience.
