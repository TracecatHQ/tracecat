# Frontend agent notes

Use these rules for work in `frontend/`.

## TypeScript and file conventions

- Use kebab-case for file names.
- Use camelCase for functions and variables, and `UPPERCASE_SNAKE_CASE` for
  constants.
- Prefer `function foo()` over `const foo = () =>`.
- Prefer named exports over default exports.
- Avoid `any`; use precise types or `unknown`.
- Avoid nested ternaries. Use `if`/`else` or `switch`.
- Write JSDoc for exported functions, hooks, components, and types.

## Frontend structure

- Put shared hooks in `frontend/src/hooks/`.
- If a new frontend type can be generated from backend contracts, prefer
  `just gen-client-ci` over hand-maintained duplicate types.
- When handling generated frontend types, do not import `$`-prefixed variables
  unless you intentionally need the schema object.

## Frontend type generation

- Prefer generated types over hand-maintained duplicates when backend schemas
  already define the contract.
- Regenerate the client with `just gen-client-ci` before introducing a new
  handwritten type that mirrors backend API data.

## Global UI shells

- Treat anything mounted from `frontend/src/app/layout.tsx` as globally live on
  every route, including logged-out and public pages.
- Do not add unconditional authenticated hooks to global shells such as modals,
  toasters, providers, and persistent sidebars.
- If a global modal only needs data when opened, either lazily mount the
  data-owning content only when `open === true` or gate every query with an
  explicit `enabled` condition tied to that state.

## Reachability after refactors

- Before deleting a route-based settings page, verify there is still a
  user-visible path to the same functionality on every intended surface.
- Do not assume "mounted globally" means "reachable globally". Trace the actual
  opener through `setOpen(...)`, links, buttons, and layouts.
- When a fallback depends on client state such as cookies or last-viewed
  workspace IDs, test the empty and stale cases explicitly.

## UI design rules

- Keep the UI flat and minimal. Do not add shadows unless the change explicitly
  calls for them.
- Avoid nested card-on-card container stacks.
- Prefer neutral colors unless product requirements call for color.
- Do not add child backgrounds that cut across a parent container's rounded
  border corners.
- Use "Title case example" style for UI copy rather than full title case.
- Do not use all-caps for titles, headings, or field labels, and do not reach
  for the `uppercase` utility on them. Use sentence case instead.
- Represent agents with `MousePointerClickIcon` (lucide), the same icon as the
  Agents entry in the sidebar. Do not use `BotIcon` or any robot glyph for
  agents; reserve `BotIcon` for non-agent machine identities such as service
  accounts.

## Input and focus consistency

- Interactive inputs should use the standard inset focus ring:
  `ring-1 ring-inset ring-ring`.
- Do not use outset ring patterns such as `ring-2` with `ring-offset-2` on
  form inputs.
- Container-based inputs such as `MultiTagCommandInput` should use
  `focus-within:` while keeping the same visual ring treatment as standard
  inputs.
- For shortcut UI, render keys with `Kbd` and prefer
  `parseShortcutKeys` from `frontend/src/lib/tiptap-utils.ts`.

## Dialogs

New dialogs follow the flat panel convention in
`frontend/src/components/ui/dialog.tsx`. The agent preset "Add tools" dialog
(`components/tools/tool-picker-dialog.tsx`) is the reference look.

```tsx
<Dialog open={open} onOpenChange={setOpen}>
  <DialogContent size="md">
    <DialogHeader>
      <DialogTitle>Create thing</DialogTitle>
      <DialogDescription>One line on what this does.</DialogDescription>
    </DialogHeader>
    <DialogBody>{/* fields */}</DialogBody>
    <DialogFooter>
      <Button size="sm" type="submit" form={FORM_ID}>
        Create
      </Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

- Always set `size` on `DialogContent` (`sm` 320px, `md` 520px, `lg` 680px,
  `xl` 840px). Heights are fixed and capped to the viewport, so the dialog does
  not jump as content changes.
- Put scrollable content in `DialogBody`, which wraps the shadcn `ScrollArea`.
  Do not add another `ScrollArea` or `overflow-auto` wrapper inside it unless
  it is a distinct list or table viewport.
- Keep `DialogHeader` to the title and description. Put alerts, tabs, and
  fields in `DialogBody`.
- Put primary actions in `DialogFooter` with `size="sm"` buttons. If the form
  lives in the body, give it an `id` and point the footer submit button at it
  with `form={FORM_ID}`. Otherwise, wrap body and footer in one form with
  `className="flex min-h-0 flex-1 flex-col"`.
- Do not duplicate dismiss controls. Regular dialogs already render an X, so do
  not add Cancel or Close buttons. `AlertDialog` confirmations have no X and
  keep `AlertDialogCancel`.
- JSON and code editors in a dialog need a minimum height that fills most of
  the body on open (for example `[&_.cm-editor]:min-h-[220px]`). Let them grow
  with content and scroll inside `DialogBody`.
- Jest mocks of `@/components/ui/dialog` must export `DialogBody`.

## Standard settings/admin layout

Use this layout for settings and admin pages:

```tsx
<div className="size-full overflow-auto">
  <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
    <div className="flex w-full">
      <div className="items-start space-y-3 text-left">
        <h2 className="text-2xl font-semibold tracking-tight">Title</h2>
        <p className="text-base text-muted-foreground">Subtitle</p>
      </div>
    </div>
  </div>
</div>
```

- Keep the outer `size-full overflow-auto` wrapper.
- Keep the inner container width at `max-w-[1000px]`.
- Use `space-y-12` between major sections and `space-y-3` between title and
  subtitle.
- Use `h2` for the page title and `text-base text-muted-foreground` for the
  subtitle.
- If the page has a back link, place it above the header row.

## Review checklist for settings work

- Closed modal: no authenticated network requests should fire.
- Public or logged-out routes: no avoidable 401s from inactive UI.
- Workspace and org settings entrypoints: confirm the feature is reachable from
  the intended routes.
- Role and entitlement gating: verify both visible and blocked states, not just
  the happy path.
