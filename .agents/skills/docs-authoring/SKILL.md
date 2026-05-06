---
name: docs-authoring
description: Use when adding or updating documentation pages in an existing docs site. Covers matching nearby docs tone and structure, planning navigation and page content, running product services and docs previews, capturing supporting UI screenshots with Chrome DevTools, suppressing Next.js floating dev indicators before screenshots, taking full-height Mintlify docs screenshots for PR descriptions, keeping PR-only artifacts out of committed files, and creating or updating GitHub PRs with verified documentation labels and screenshot links.
---

# Docs Authoring

Use this workflow for documentation PRs where the page needs to read like it belongs in the existing docs, land in the right navigation, include accurate supporting screenshots when useful, and ship with a clear PR description.

## Workflow

1. Read the closest docs instructions first: repo `AGENTS.md`, docs-specific `AGENTS.md`, the docs navigation file, and 2-4 adjacent pages in the same section.
2. Match the existing docs exactly: frontmatter shape, title style, icon naming, heading depth, sentence length, callouts, screenshots, alt text, related links, and navigation placement.
3. Plan the docs change before writing: target reader, page goal, nav location, related pages, prerequisite state, and whether screenshots are needed.
4. Write the page in the existing style, then update navigation and adjacent links as needed.
5. Start the product stack only when real UI state is needed for screenshots or content verification. If the repo has cluster guidance, check for an existing stack before starting a new one.
6. Capture supporting product screenshots through Chrome DevTools. Do not use OS screenshots when the user asked for Chrome DevTools.
7. Hide Next.js dev UI before every capture. Re-run the suppression after navigation, reloads, or hot updates.
8. Consider an env-driven Next.js dev UI suppression path if the project already supports one, or if the user approves adding a local dev-only switch.
9. Add only documentation content screenshots to the repo. Save PR-only full-page docs screenshots in a local ignored artifact folder or `/tmp`, never in the commit unless the user explicitly asks.
10. Run the docs preview with `mint`, capture full-height screenshots of every added or materially changed docs page, and upload or link those screenshots in the PR description.
11. Create or update the PR with the appropriate docs label, usually `documentation`.
12. Verify the docs, git diff, screenshot links, labels, and PR body before calling the task done.

## Tone And Style

Inspect existing pages before writing. Prefer the vocabulary and structure already in the section over introducing a new documentation voice.

Check for:

- Frontmatter fields and ordering.
- Whether pages use direct imperatives, conceptual explanation, or task steps.
- Heading capitalization and depth.
- Short paragraphs versus bullets.
- Screenshot placement and captions.
- Callout style and frequency.
- How Enterprise, beta, permissions, and related-page notes are phrased.

Keep the page focused on the user task. Avoid explaining UI that is already obvious from the screenshot, and avoid adding conceptual background if adjacent pages do not do that.

## Docs Content

Write documentation as a product surface, not as implementation notes.

Before editing, identify:

- The exact user workflow or concept the page must support.
- Where the page belongs in the docs hierarchy.
- Whether the page should be task-oriented, conceptual, reference-style, or a mix, based on neighboring pages.
- Which screenshots, examples, callouts, or related links are expected by the existing section.

While writing:

- Use concrete product nouns and visible UI labels from the app.
- Prefer short, scannable sections with direct headings.
- Keep setup, permissions, Enterprise notes, and limitations phrased the same way adjacent pages do.
- Add screenshots only where they clarify state, placement, or a workflow step.
- Update navigation and related links in the same change.

## Product Screenshots

Use Chrome DevTools or the available browser DevTools MCP to navigate the local app, set a stable viewport, seed realistic demo data when needed, and capture screenshots.

For Tracecat-style local work:

```bash
docker compose ls --filter name=tracecat
just cluster up -d
just cluster ps
```

Follow the repo's cluster instructions if they differ. Prefer existing service helpers such as `just cluster logs`, `just cluster restart`, and `just cluster ports`.

Screenshot rules:

- Capture the real UI state that the docs describe.
- Use stable filenames under the docs image directory, usually `docs/img/<section>/<descriptive-name>.png`.
- Embed committed screenshots from the docs page with clear alt text.
- Retake screenshots when overlays, loading states, scrollbars, toasts, or dev tools obscure the UI.
- Verify the image dimensions and file type with `file` or the browser screenshot metadata.

## Hide Next.js Dev UI

Before screenshots, inject a temporary browser-session style/script through Chrome DevTools. Prefer this over cropping, manual UI hiding, or committed app code.

Use a snippet like this in the page context:

```js
(() => {
  const id = "docs-screenshot-hide-next-dev-ui";
  document.getElementById(id)?.remove();

  const style = document.createElement("style");
  style.id = id;
  style.textContent = `
    nextjs-portal,
    [data-nextjs-dialog],
    [data-nextjs-dialog-overlay],
    [data-nextjs-toast],
    [data-nextjs-dev-tools-button],
    [data-nextjs-dev-tools-indicator],
    [aria-label="Open Next.js Dev Tools"],
    button[aria-label*="Next.js"],
    iframe[src*="nextjs"] {
      display: none !important;
      visibility: hidden !important;
      pointer-events: none !important;
      opacity: 0 !important;
    }
  `;
  document.documentElement.appendChild(style);
  document.querySelectorAll("nextjs-portal").forEach((el) => el.remove());
})();
```

After injecting it, visually inspect the page or take a quick screenshot to confirm the floating dev bubble is gone. If the dev UI reappears after navigation or hot reload, inject it again and retake the screenshot.

Only modify application source to suppress the dev UI if the user explicitly asks for a durable code change. Keep that change dev-only and reversible.

### Env-Driven Suppression

Next.js supports hiding dev indicators through `devIndicators: false` in `next.config.*`; projects can also choose to drive that config from environment variables. Do not assume a universal Next.js env var exists. Inspect the project's Next.js version, `next.config.*`, package scripts, and local docs first.

If env-driven suppression is available or approved:

- Prefer a local, screenshot-session env var such as `NEXT_HIDE_DEV_INDICATORS=1`, `DOCS_SCREENSHOT_MODE=1`, or the project's existing equivalent.
- Wire it into `next.config.*` or dev-only UI code so it sets `devIndicators: false` or hides the custom devtools entry point only during local screenshot runs.
- Set it from the shell command, cluster env, or uncommitted `.env.local`; do not commit `.env.local`.
- Restart the Next.js dev server after changing env values.
- Reconfirm with Chrome DevTools that the floating dev bubble is gone before taking final screenshots.

Example pattern:

```js
const hideDevIndicators =
  process.env.NEXT_HIDE_DEV_INDICATORS === "1" ||
  process.env.DOCS_SCREENSHOT_MODE === "1";

const nextConfig = {
  devIndicators: hideDevIndicators ? false : undefined,
};

module.exports = nextConfig;
```

## Mint Docs Preview

Run the docs preview from the directory containing `docs.json` or `mint.json`.

Before starting, confirm `mint` is available:

```bash
mint --version
```

If `mint` is missing, broken, or too old for the docs site, stop and ask the user to install or update it. Give concise guidance, such as checking the repo docs for the expected Mintlify CLI version or installing/updating the Mintlify CLI with the package manager they use locally. Do not silently install or replace `mint`; yield to the user because this changes their local toolchain.

```bash
cd docs
mint dev
```

Let Mint choose its default or next available port. Read the preview URL from the `mint dev` output and use that exact URL for screenshots. Only pass `--port` when the repo, user, or another running service requires a fixed port.

Keep the server running until screenshots and visual checks are complete.

Open each added or materially changed page at the URL Mint prints, for example `<mint-preview-url>/<path>`, and check:

- Navigation entry appears in the expected section.
- Frontmatter title, description, and icon render correctly.
- Images load without broken paths.
- Text does not wrap awkwardly on desktop or mobile widths.
- Related links and cards point to existing pages.

## Full-Height PR Screenshots

Take a full-height scrolling screenshot of every added or materially changed docs page after the Mint preview is correct.

Use the browser's full-page screenshot support when available. If the tool only captures the viewport, use Chrome DevTools Protocol full-page capture, such as `Page.captureScreenshot` with `captureBeyondViewport: true`, after reading the page layout metrics.

Save PR-only screenshots in a local artifact location that will not be committed. Prefer a repo-local dot folder excluded through `.git/info/exclude` so the screenshots stay near the worktree without appearing in the PR:

```bash
mkdir -p .docs-pr-artifacts
grep -qxF ".docs-pr-artifacts/" .git/info/exclude || printf "\n.docs-pr-artifacts/\n" >> .git/info/exclude

# Example output path:
# .docs-pr-artifacts/skills-docs-full-page.png
```

For disposable artifacts, use `/tmp` instead:

```bash
mkdir -p /tmp/docs-pr-artifacts
# Example output path:
# /tmp/docs-pr-artifacts/skills-docs-full-page.png
```

Do not use user desktop folders such as `~/Screenshots` for this workflow. Do not stage or commit PR-only full-page screenshots. Confirm before committing:

```bash
git status --short
git diff --name-only --cached
```

## PR Image Links

The full-page docs screenshot is a PR description artifact, not docs source. Keep it out of the PR files.

Preferred GitHub-hosted options:

- Upload the PNG in the GitHub PR description or comment editor so GitHub creates a `https://github.com/user-attachments/assets/...` URL, then use that URL in the PR body.
- If browser upload is not automatable, use an approved internal artifact host or a private/secret GitHub gist and embed the raw image URL.

Do not commit the full-page screenshot just to get a raw GitHub URL unless the user explicitly approves that tradeoff.

When writing or editing a PR body, use a body file for Markdown:

```bash
cat > /tmp/docs-pr-body.md <<'EOF'
## Summary
- Add ...

## Screenshots
![Full page docs screenshot](https://github.com/user-attachments/assets/...)

## Validation
- `mint dev`
- `uv run ruff check .`
EOF

gh pr edit <number> --body-file /tmp/docs-pr-body.md
gh pr view <number> --json body --jq .body
```

For a new PR, use `gh pr create --body-file` rather than inline Markdown.

## PR Labels

Tag docs PRs with the repository's existing documentation label. In most repos this is `documentation`; if the repo uses a different docs label, use that exact existing label instead of creating a new one unless the user asks.

## Validation

Use the repository's required checks. For Python repos with docs-only changes, still run the required lint command if the repo asks for it.

Common checks:

```bash
mint broken-links
uv run ruff check .
jq . docs/docs.json >/dev/null
```

If a required tool fails for environmental reasons, record the exact command and failure in the PR notes. Do not claim validation passed when the command never reached content validation.

Before finalizing:

- Confirm the docs page follows the section's existing tone and structure.
- Confirm navigation and related links are updated.
- Confirm committed docs screenshots are referenced by the docs page.
- Confirm PR-only full-page screenshots are not staged or committed.
- Confirm the PR body renders image Markdown, contains validation notes, and has the expected docs label such as `documentation`.
- Confirm the working tree contains no unrelated changes.
