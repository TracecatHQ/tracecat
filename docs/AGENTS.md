# Prefect Documentation

Structural context for any agent working in `docs/`. For the full writing guide (page types, components, code testing, style), use the `/write-docs` skill.

## Platform

[Mintlify](https://mintlify.com/) docs published to [docs.tracecat.com](https://docs.tracecat.com). All files use `.mdx` (Markdown + JSX). Site config lives in `docs/docs.json`.

## File format

Every `.mdx` file starts with YAML frontmatter:

```yaml
---
title: Page title
description: Brief description for SEO and navigation
sidebarTitle: Optional shorter sidebar label   # optional
icon: icon-name                                # optional, Mintlify icon
mode: wide                                     # optional
keywords: ["keyword1", "keyword2"]             # optional, for search
---
```

All titles and headers should have first letter capitalized only, not title case.
Frontmatter keys are always lowercase. The `title` renders as the page's H1, so start body content at `##` — do not add another H1.

## Navigation

All pages must be registered in `docs/docs.json` under `navigation.tabs`. When adding a page, add its path (without `.mdx` extension) to the appropriate group.

## Links

Use absolute paths from the docs root without `.mdx`:

```mdx
See the [automations documentation](/automations/overview) for details.
```

Do not use relative paths or include `.mdx` in links.

## Redirects

When renaming or moving a page, add a redirect in `docs/docs.json` `redirects` array so existing links continue to work. Never remove existing redirects unless you are certain the old URL has no inbound traffic. Paths should not include `.mdx`.

## Tone and style

- Always use active voice.
- Always address the reader directly.
- Clarity over cleverness and verbosity: always keep sentences concise, do not add unnecessary words.
- Be consistent: always use terminology and spelling consistent with the codebase and the rest of the docs.
- Be skimmable: avoid more than 2 sentences per paragraph.
- Write in second person: referring to the rader makes it easier to follow instructions and makes the docs feel more personal.

## Key rules

1. **Register new pages in `docs/docs.json`.** An unregistered page won't appear in navigation.
2. **Use `.mdx` extension** for all new documentation files.
3. **Use Mintlify components** (`<Note>`, `<Tabs>`, `<Steps>`, etc.) rather than Markdown-native admonition syntax.
4. **Use absolute link paths** without file extensions (e.g., `/automations/overview`).
5. **Check for existing snippets** in `snippets/` before duplicating content.
6. **Start body content at `##`.** The frontmatter `title` renders as H1; do not add another H1 in the body.
