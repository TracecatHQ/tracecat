# Tracecat Documentation

Structural context for any agent working in `docs/`. For the full writing guide (page types, components, code testing, style), use the `/write-docs` skill.

## Platform

The docs site is published to [docs.tracecat.com](https://docs.tracecat.com). All files use `.mdx` (Markdown + JSX). Site config lives in `docs/docs.json`.

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
- If a user explicitly asks for a different voice, follow that request instead of the default.
- Clarity over cleverness and verbosity: always keep sentences concise, do not add unnecessary words.
- Prefer the shortest explanation that preserves the behavioral contract.
- Write for builder action, not product explanation. If a sentence does not change how the reader configures, references, or runs the feature, cut it.
- Do not write circular definitions such as "X is a template for X" or abstract definitions that do not say what the feature does.
- Do not add justification, rationale, or benefits unless they change a build decision.
- Lead with what the product does. Do not lead with parser details, implementation detail, or edge-case detail unless it changes how the reader uses the feature.
- Do not restate the same idea in multiple sentences.
- Avoid “for example” clauses unless the example removes ambiguity.
- Prefer compact mappings when documenting input or payload formats, such as `JSON: becomes TRIGGER` and `Form: keys become TRIGGER fields`.
- If a sentence only adds precision that most readers do not need, cut it or move it to a note.
- Be consistent: always use terminology and spelling consistent with the codebase and the rest of the docs.
- Be skimmable: avoid more than 2 sentences per paragraph.
- Do not split a short thought into two paragraphs or two one-line sentences when one sentence reads more naturally.
- Capitalize the first word of every bullet point.
- Write in second person: referring to the reader makes it easier to follow instructions and makes the docs feel more personal.
- Use product terms that match the app and codebase. For example, use `workflow definition`, `schema`, `subflow`, `upstream`, and `downstream`. Do not introduce alternatives such as `workflow file`, `shape`, `child workflow`, or `child action` unless the code itself uses that term in a user-facing name.
- Use `Action inputs` for user-facing docs. Use `action.args` only when you are explicitly referring to the `args` field in a workflow definition.
- Use `Input schema` and `Output schema` for user-facing docs. Use `expects` and `returns` only when you are explicitly referring to those workflow definition fields.
- Use `Workflow environment` for user-facing docs. Use `config.environment` only when you are explicitly referring to the workflow definition field.
- Use full-sentence bullets in `Related pages` sections. Start each bullet with `See [Page]...` and explain why the reader should open it.
- Do not document `retry_until` or `wait_until` in user-facing docs. Treat them as internal or transitional fields unless the product direction changes.

## Key rules

1. **Register new pages in `docs/docs.json`.** An unregistered page won't appear in navigation.
2. **Use `.mdx` extension** for all new documentation files.
3. **Use the built-in docs components** (`<Note>`, `<Tabs>`, `<Steps>`, etc.) rather than Markdown-native admonition syntax.
4. **Use absolute link paths** without file extensions (e.g., `/automations/overview`).
5. **Check for existing snippets** in `snippets/` before duplicating content.
6. **Start body content at `##`.** The frontmatter `title` renders as H1; do not add another H1 in the body.
