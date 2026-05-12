---
name: gh-prerelease
description: Cut a GitHub prerelease off a specific commit — branch, bump version with `just update-version`, tag, push, and publish a prerelease
disable-model-invocation: true
argument-hint: "[<tag>] [<commit>]"
---

# gh-prerelease

Cut a GitHub prerelease for Tracecat off a specific commit. Mirrors `.github/workflows/create-release.yml` + `publish-release.yml`, but tags an arbitrary commit (not just `main`'s HEAD) and publishes a GitHub prerelease directly instead of going through the draft-release flow.

The long-lived `release/<tag>` branch is left in place so further hotfix commits can be cherry-picked onto it later.

## Argument parsing

Full argument string: `$ARGUMENTS`.

Split `$ARGUMENTS` on whitespace into tokens:

- Token 1 → `<tag>` (optional). Semver with a prerelease suffix, e.g. `0.20.0-rc.1`, `1.0.0-beta.48-rc.5`. Do not include a leading `v` — tags in this repo are bare semver.
- Token 2 → `<commit>` (optional, default `HEAD`). Anything `git rev-parse` accepts: a SHA, branch, `HEAD`, `HEAD~3`, `origin/main`, etc.

Do not rely on `$1`, `$2` — only `$ARGUMENTS` is reliably substituted in skill markdown.

If `<tag>` is missing, suggest the next logical RC tag by inspecting recent tags:

```sh
git fetch --tags --prune
LATEST_RC=$(git tag --sort=-v:refname | rg -m1 -- '-rc\.[0-9]+$' || true)
```

- If `LATEST_RC` matches `<base>-rc.<N>`, the suggested tag is `<base>-rc.<N+1>`. For example, `1.0.0-beta.48-rc.4` → `1.0.0-beta.48-rc.5`.
  ```sh
  BASE="${LATEST_RC%-rc.*}"
  N="${LATEST_RC##*-rc.}"
  SUGGESTED="${BASE}-rc.$((N + 1))"
  ```
- If no `-rc.<N>` tag exists, stop and ask — there is no unambiguous "next" without a precedent.

Present the suggestion in one line and wait for explicit confirmation (`y` to accept, or have the user supply an alternative). Do not proceed silently. If the user accepts, use the suggestion as `<tag>` for the rest of the workflow.

Validate `<tag>` against `^[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z0-9][A-Za-z0-9.-]*$` (semver core + a prerelease suffix). If it lacks a prerelease suffix (e.g. plain `0.20.0`), refuse and point the user at `.github/workflows/create-release.yml` — this skill is for prereleases only.

## Workflow

### 1. Preflight

Stop and report on any failure.

```sh
git rev-parse --is-inside-work-tree
git fetch --all --tags --prune
```

Resolve the commit:

```sh
COMMIT_SHA=$(git rev-parse --verify "<commit>^{commit}")
git log -1 --format='%h %s' "$COMMIT_SHA"
```

Verify the tag does not already exist anywhere:

```sh
git rev-parse --verify "refs/tags/<tag>" 2>/dev/null      # must fail
git ls-remote --tags origin "refs/tags/<tag>"             # must print nothing
gh release view "<tag>" --json tagName 2>/dev/null        # must fail
```

If any of these succeed, stop. Ask the user whether to pick a different tag or remove the existing one first.

Verify the release branch does not already exist:

```sh
git rev-parse --verify "refs/heads/release/<tag>" 2>/dev/null     # must fail
git ls-remote --heads origin "release/<tag>"                       # must print nothing
```

**Working-tree state.** Run `git status --porcelain`.

- **Tracked modifications** — stop and ask the user how to proceed: **stash**, **drop**, or **abort**. Never auto-stash or auto-restore.
- **Untracked files** — only flag if a path collides with one `just update-version` will touch (`tracecat/__init__.py`, `packages/tracecat-registry/tracecat_registry/__init__.py`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, plus matches under `docker-compose*.yml`, `docs/**`, `deployments/**`).

### 2. Confirm the plan

In **one** message, present:

- Base commit: `<short-sha>` + subject.
- Tag to create: `<tag>`.
- Branch to create: `release/<tag>`.
- The exact sequence of commands that will run (steps 3–7 below, with `<tag>` and `<commit>` substituted).
- Note that pushing the tag will trigger `build-push-images.yml` (matches `tags: '*.*.*'` in its `push` trigger).

Stop and wait for `y`.

### 3. Create the release branch off the commit

```sh
git switch --create "release/<tag>" "$COMMIT_SHA"
```

### 4. Bump the version

Match `.github/workflows/create-release.yml`:

```sh
yes | just update-version <tag>
```

`update-version.sh` prompts before overwriting files; `yes |` answers `y`.

Sanity-check the result:

```sh
git diff --quiet && { echo "update-version produced no changes" >&2; exit 1; }
NEW_VERSION=$(grep -oP '__version__ = "\K[^"]+' tracecat/__init__.py)
[ "$NEW_VERSION" = "<tag>" ] || { echo "version mismatch: got $NEW_VERSION, expected <tag>" >&2; exit 1; }
```

If it fails: `git restore .` (after confirming with the user) and abort.

### 5. Commit the bump

Stage **only** the files `update-version` modified, individually. Do not use `git add -A` or `git add .`.

```sh
git status --porcelain
# Then for each modified path shown:
git add -- <path>
```

Then:

```sh
git commit -m "release: <tag>"
```

(Matches the `release: ${VERSION}` message used by `create-release.yml`.)

### 6. Push the branch

```sh
git push -u origin "release/<tag>"
```

### 7. Tag the release commit and push the tag

The annotated tag points to the version-bump commit (the new `HEAD`), mirroring `publish-release.yml` tagging the merge commit.

```sh
git tag -a "<tag>" -m "Release <tag>"
git push origin "refs/tags/<tag>"
```

This `push` event for a `*.*.*` tag triggers `.github/workflows/build-push-images.yml`, which builds and publishes `ghcr.io/tracecathq/tracecat:<tag>` and `ghcr.io/tracecathq/tracecat-ui:<tag>`. (Because the prerelease tag has a `-suffix`, neither image will be retagged `:latest` — that branch in the workflow is guarded on `!startsWith(..., 'nightly-')` **and** non-prerelease semver via the matrix tags.)

### 8. Publish the GitHub prerelease

```sh
gh release create "<tag>" \
  --target "release/<tag>" \
  --prerelease \
  --title "Tracecat <tag>" \
  --generate-notes
```

`--generate-notes` autopopulates the changelog from PRs since the previous tag, consistent with how `release-drafter` builds the draft release on `main`.

### 9. Report

Print:

- Branch: `release/<tag>` (pushed, long-lived — do not delete).
- Tag: `<tag>` → `<short-sha>` of the version-bump commit.
- Release URL: `gh release view <tag> --json url --jq .url`.
- Image build status: `gh run list --workflow build-push-images.yml --branch <tag> --limit 1` (the workflow run shows up under the tag ref).

## Rules

- Refuse if `<tag>` is a stable release (no prerelease suffix). Stable releases go through `create-release.yml` → PR → `publish-release.yml`.
- Wait for explicit `y` at step 2. Never push branches or tags before confirmation.
- Never force-push the tag or branch. If something is wrong post-push, stop and ask — do not `--force`.
- Never use `git add -A` or `git add .`. Stage paths individually.
- Never bypass commit signing (`--no-gpg-sign`) or hooks (`--no-verify`). If signing fails, stop and ask the user to fix it.
- Do not include AI/agent attribution in the commit or tag messages.
- The release branch is **long-lived**. Do not delete it after the prerelease is cut, even if the prerelease is later promoted or abandoned.
- If the user later wants to cut another prerelease against the same base, suggest using the existing `release/<previous-tag>` branch as the `<commit>` argument so the version-bump history stays linear.
