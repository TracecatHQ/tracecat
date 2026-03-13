# Frontend agent notes

## Global UI shells

- Treat anything mounted from `frontend/src/app/layout.tsx` as globally live on every route, including logged-out and public pages.
- Do not add unconditional authenticated hooks (`useQuery`, `useEntitlements`, workspace/org lookups, scope lookups) to global shells such as modals, toasters, providers, and persistent sidebars.
- If a global modal only needs data when opened, either:
  - lazily mount the data-owning content only when `open === true`, or
  - gate every query with an explicit `enabled` condition tied to that state.

## Reachability after refactors

- Before deleting a route-based settings page, verify there is still a user-visible path to the same functionality on every intended surface.
- Do not assume "mounted globally" means "reachable globally". Confirm the actual opener exists by tracing `setOpen(...)`, links, buttons, and layouts.
- When a fallback depends on client state like cookies or last-viewed workspace IDs, test the empty/stale case explicitly.

## Review checklist for settings work

- Closed modal: no authenticated network requests should fire.
- Public/logged-out routes: no avoidable 401s from inactive UI.
- Workspace/org settings entrypoints: confirm the feature is reachable from the routes called out in the change.
- Role/entitlement gating: verify both visible and blocked states, not just the happy path.
