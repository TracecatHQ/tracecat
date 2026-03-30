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

## Input and select focus ring consistency

- All interactive inputs must use the same focus ring style: `ring-1`, `ring-inset`, `ring-ring`. This matches the standard `Input` and `SelectTrigger` components.
- Do not use `ring-2`, `ring-offset-2`, or other outset ring variants on form inputs — they create visually inconsistent borders in dialogs.
- Container-based inputs (e.g., `MultiTagCommandInput`) should use `focus-within:` instead of `focus-visible:` since the focusable element is a child, but the ring width and position must still match the standard pattern.

## Review checklist for settings work

- Closed modal: no authenticated network requests should fire.
- Public/logged-out routes: no avoidable 401s from inactive UI.
- Workspace/org settings entrypoints: confirm the feature is reachable from the routes called out in the change.
- Role/entitlement gating: verify both visible and blocked states, not just the happy path.
