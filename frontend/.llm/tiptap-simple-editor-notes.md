# Tiptap Simple Editor Adoption Notes

## What was removed compared to `pnpm dlx @tiptap/cli@latest add simple-editor`

The CLI template installs a large set of UI primitives, hooks, and extensions. In this migration we kept only the essentials.

Removed pieces:

- `src/components/tiptap-ui-primitive/*` (buttons, cards, dropdowns, toolbar, tooltip, popover, spacer, etc.)
- `src/components/tiptap-ui/*` (mark buttons, heading dropdown, list dropdown, code block button, undo/redo controls, link popover, highlight popover, image upload button, etc.)
- `src/components/tiptap-node/*` (image upload node, image node, blockquote node, list node, heading node, horizontal rule node, paragraph node, code block node styles)
- `src/components/tiptap-icons/*` (icon wrappers used by the above components)
- `src/hooks/use-mobile`, `use-window-size`, `use-cursor-visibility`, `use-scrolling`, `use-composed-ref`, `use-element-rect`, `use-menu-navigation`, `use-throttled-callback`, `use-unmount`, `use-tiptap-editor`
- `src/lib/tiptap-utils.ts` and associated helpers (upload handling, schema checks, shortcut parsing)
- `src/components/tiptap-templates/simple/theme-toggle.tsx`, `src/components/tiptap-templates/simple/data/*`
- Theme assets and SCSS variables the template expects (we replaced with lean equivalents in `src/styles/_variables.scss` and `_keyframe-animations.scss`)

What we kept / rewired:

- Core editor built with `@tiptap/starter-kit`, `@tiptap/extension-placeholder`, `@tiptap/extension-code-block-lowlight`, and `tiptap-markdown-3`
- Custom toolbar implemented directly in `simple-editor.tsx` using existing UI primitives (`<Button/>`, `<DropdownMenu/>` from our design system)
- Markdown sync glue to integrate with existing case logic

## Re-enabling optional template features

Each section below outlines exactly which files/changes are needed to restore a specific feature from the CLI template.

### 1. Theme toggle + dark mode switcher

1. Restore `src/components/tiptap-templates/simple/theme-toggle.tsx` from the CLI scaffold.
2. Restore the supporting hooks and primitives used by that component (`src/components/tiptap-ui-primitive/button`, `toolbar`, `dropdown-menu`, etc.).
3. Update `simple-editor.tsx` to import and render `<ThemeToggle />` inside the toolbar (usually as the last `ToolbarGroup`).
4. Ensure the SCSS variables from the template (`src/styles/_variables.scss`, `_keyframe-animations.scss`) match the CLI version; reinstate any CSS variables referenced by the theme toggle.

### 2. Image upload button & node

1. Restore `src/components/tiptap-ui/image-upload-button/*` and `src/components/tiptap-node/image-upload-node/*`.
2. Restore `src/lib/tiptap-utils.ts` (specifically `handleImageUpload`, `MAX_FILE_SIZE`, and helper functions).
3. Add `ImageUploadNode` extension back into the editor configuration:
   ```ts
   import { ImageUploadNode } from "@/components/tiptap-node/image-upload-node/image-upload-node-extension"

   extensions: [
     ...,
     ImageUploadNode.configure({
       accept: "image/*",
       maxSize: MAX_FILE_SIZE,
       limit: 3,
       upload: handleImageUpload,
     }),
   ]
   ```
4. Add the `<ImageUploadButton />` component to the toolbar exactly as in the CLI template (requires `ToolbarGroup`, `ButtonGroup`, etc.).
5. Include the SCSS files for the node (`image-upload-node.scss`, `image-node.scss`).
6. Ensure any environment variables or API endpoints required by `handleImageUpload` are configured.

### 3. Link popover & highlight picker

1. Restore `src/components/tiptap-ui/link-popover/*` and `color-highlight-popover/*`.
2. Reintroduce supporting hooks: `use-tiptap-editor`, `use-mark`, `parseShortcutKeys`, etc. from `tiptap-utils` and `tiptap-ui` packages.
3. In `simple-editor.tsx`, replace the simplified inline mark buttons with the CLI’s mark components:
   ```tsx
   import { MarkButton } from "@/components/tiptap-ui/mark-button"
   import { LinkPopover } from "@/components/tiptap-ui/link-popover"
   import { ColorHighlightPopover } from "@/components/tiptap-ui/color-highlight-popover"

   <ToolbarGroup>
     <MarkButton type="bold" />
     ...
     <ColorHighlightPopover />
     <LinkPopover />
   </ToolbarGroup>
   ```
4. Include the associated SCSS files (`color-highlight-button.scss`, `link-popover.scss`) and ensure global variables exist.

### 4. Advanced toolbar layout & keyboard navigation

1. Restore `src/components/tiptap-ui-primitive/toolbar/*`, `button/*`, `dropdown-menu/*`, `tooltip/*`, etc., plus `src/hooks/use-menu-navigation`, `use-composed-ref`, `use-element-rect`, `use-throttled-callback`, and `use-unmount`.
2. Swap the toolbar implementation in `simple-editor.tsx` to use the CLI’s `<Toolbar>` component rather than our simplified `<div>` wrappers.
3. Re-enable `useCursorVisibility` hook to manage scrolling on mobile by restoring `use-mobile`, `use-window-size`, and `use-cursor-visibility`. Attach it as in the original template (pass to `<Toolbar>` and the cursor overlay logic).
4. Reapply the template’s SCSS (`simple-editor.scss` from CLI) to match spacing and mobile layout.

### 5. Mobile paneled toolbar

1. Ensure the hooks above (`use-mobile`, `use-window-size`, `use-cursor-visibility`) are restored.
2. Reintroduce the `MobileToolbarContent` logic from the CLI version of `simple-editor.tsx`.
3. Reinstate the state machine that switches between "main", "highlighter", and "link" views for small screens.

### 6. Horizontal rule, blockquote styling, typography extensions

1. Re-add the relevant extensions in the editor setup:
   ```ts
   import { HorizontalRule } from "@/components/tiptap-node/horizontal-rule-node/horizontal-rule-node-extension"
   import { Typography } from "@tiptap/extension-typography"
   import { Highlight } from "@tiptap/extension-highlight"
   import { Subscript } from "@tiptap/extension-subscript"
   import { Superscript } from "@tiptap/extension-superscript"

   extensions: [
     StarterKit.configure({ horizontalRule: false, ... }),
     HorizontalRule,
     Highlight.configure({ multicolor: true }),
     Typography,
     Superscript,
     Subscript,
   ]
   ```
2. Restore the SCSS for the nodes (`blockquote-node.scss`, `horizontal-rule-node.scss`, etc.).
3. Re-add toolbar buttons that toggle these marks/nodes (e.g., `BlockquoteButton`, superscript/subscript buttons).

### 7. Slash menu & side menu

If we want the BlockNote-style slash/side menus back (not part of the Tiptap template but from our previous implementation):

1. Reintroduce BlockNote or implement equivalent Tiptap slash menu extensions (e.g. `@tiptap/extension-command-menu`).
2. Because this diverges from the simple template, evaluate whether to use Tiptap’s `Suggestion` plugin and build custom menus.

## General reinstatement steps

When adding any of the above features back:

1. Re-run `pnpm dlx @tiptap/cli@latest add simple-editor --overwrite` in a scratch branch to capture the latest template files.
2. Copy the required components/hooks/styles into our codebase.
3. Add missing dependencies to `package.json` (the template expects `@floating-ui/react`, `react-hotkeys-hook`, etc.).
4. Import the components into `simple-editor.tsx` and wire them up exactly as shown in the generated template.
5. Re-run `pnpm lint` and relevant tests.

This document should give future developers a checklist for re-enabling template features while keeping our current lean setup as the base.
