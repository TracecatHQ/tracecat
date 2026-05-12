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

- Token 1 → `<tag>` (optional). Semver-style version with a prerelease suffix, e.g. `0.20.0-rc.1`, `1.0.0-beta.48-rc.5`. Do not include a leading `v` — tags in this repo are bare versions.
- Token 2 → `<commit>` (optional, default `HEAD`). Anything `git rev-parse` accepts: a SHA, branch, `HEAD`, `HEAD~3`, `origin/main`, etc.

Do not rely on `$1`, `$2` — only `$ARGUMENTS` is reliably substituted in skill markdown.

## Version format

Keep `<tag>` in Tracecat's public release/image tag convention. `just update-version <tag>` writes that value to `__version__`, and writes a separate PEP 440-compatible value to `__pep440_version__` for Hatchling package metadata. This lets Git branches, GitHub releases, and image tags stay as `1.0.0-beta.48-rc.5` while Python builds use `1.0.0b48+rc.5`.

Examples:

| Public `<tag>` | Python package version |
|----------------|------------------------|
| `1.0.0-alpha.1` | `1.0.0a1` |
| `1.0.0-beta.48` | `1.0.0b48` |
| `1.0.0-rc.5` | `1.0.0rc5` |
| `1.0.0-beta.48-rc.5` | `1.0.0b48+rc.5` |
| `1.0.0-dev.3` | `1.0.0.dev3` |
| `1.0.0-post.1` | `1.0.0.post1` |

After running `just update-version <tag>`, verify both fields:

```sh
PUBLIC_VERSION=$(grep -oP '__version__ = "\K[^"]+' tracecat/__init__.py)
PYTHON_VERSION=$(grep -oP '__pep440_version__ = "\K[^"]+' tracecat/__init__.py)
uv run python - "$PYTHON_VERSION" <<'PY'
import sys
from packaging.version import Version

Version(sys.argv[1])
PY
```

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

Validate `<tag>` against `^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z]+\.[0-9]+){1,2}$`. If it is a stable release (e.g. plain `0.20.0`), refuse and point the user at `.github/workflows/create-release.yml` — this skill is for prereleases only.

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

Important: pass the public release tag, e.g. `1.0.0-beta.48-rc.5`. `update-version.sh` keeps that value in `__version__` and writes the PEP 440 equivalent, e.g. `1.0.0b48+rc.5`, to `__pep440_version__` for Python package builds.

Sanity-check the result:

```sh
git diff --quiet && { echo "update-version produced no changes" >&2; exit 1; }
NEW_VERSION=$(grep -oP '__version__ = "\K[^"]+' tracecat/__init__.py)
[ "$NEW_VERSION" = "<tag>" ] || { echo "version mismatch: got $NEW_VERSION, expected <tag>" >&2; exit 1; }
PYTHON_VERSION=$(grep -oP '__pep440_version__ = "\K[^"]+' tracecat/__init__.py)
REGISTRY_PYTHON_VERSION=$(grep -oP '__pep440_version__ = "\K[^"]+' packages/tracecat-registry/tracecat_registry/__init__.py)
[ "$PYTHON_VERSION" = "$REGISTRY_PYTHON_VERSION" ] || { echo "Python version mismatch: tracecat=$PYTHON_VERSION registry=$REGISTRY_PYTHON_VERSION" >&2; exit 1; }
uv run python - "$PYTHON_VERSION" <<'PY'
import sys
from packaging.version import Version

Version(sys.argv[1])
PY
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

Build release notes that (a) only cover changes since the **previous published release or prerelease** and (b) are grouped into the same categories the `release-drafter` GitHub Action uses on `main`. Then publish.

#### 8a. Resolve the previous release

```sh
PREV_TAG=$(gh release list --exclude-drafts --limit 1 --json tagName --jq '.[0].tagName')
```

`gh release list` orders by created-at desc and includes prereleases, so this picks the most recent published release of any kind. If empty, fall back to the most recent reachable tag:

```sh
PREV_TAG=${PREV_TAG:-$(git describe --tags --abbrev=0 "${COMMIT_SHA}^")}
```

Stop and ask if neither resolves.

#### 8b. Pull raw notes from GitHub, scoped to PREV_TAG..\<tag\>

```sh
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
RAW_NOTES=$(gh api "repos/$REPO/releases/generate-notes" \
  --method POST \
  -f tag_name="<tag>" \
  -f previous_tag_name="$PREV_TAG" \
  -f target_commitish="$COMMIT_SHA" \
  --jq .body)
```

This is the same auto-generated body `gh release create --generate-notes` would produce, but with `previous_tag_name` pinned so the diff window is exactly `PREV_TAG..<tag>` (prereleases included as PREV) instead of "latest stable release".

#### 8c. Extract PRs and look up labels

```sh
PR_NUMS=$(printf '%s\n' "$RAW_NOTES" | grep -oE 'pull/[0-9]+' | grep -oE '[0-9]+' | sort -un)
```

For each PR number, fetch metadata once:

```sh
gh pr view "$N" --json number,title,labels,author,url
```

#### 8d. Categorize per `.github/release-drafter.yml`

Drop any PR carrying an `exclude-labels` value (`skip changelog`, `release`).

For the rest, bucket each PR into the **first** matching category in this order. The list mirrors `.github/release-drafter.yml` exactly — if that file changes, update this list:

| # | Category title           | Labels that match                                                                                  |
|---|--------------------------|----------------------------------------------------------------------------------------------------|
| 1 | Breaking changes         | `breaking`, `breaking ui`, `breaking frontend`, `breaking engine`, `breaking app`, `breaking infra` |
| 2 | Deprecations             | `deprecation`                                                                                       |
| 3 | Security                 | `security`                                                                                          |
| 4 | Playbooks                | `playbook`                                                                                          |
| 5 | Integrations             | `integrations`                                                                                      |
| 6 | Agents                   | `agents`                                                                                            |
| 7 | Performance improvements | `performance`                                                                                       |
| 8 | Enhancements             | `enhancement`                                                                                       |
| 9 | Bug fixes                | `fix`                                                                                               |
| 10| Infrastructure           | `infra`                                                                                             |
| 11| Documentation            | `documentation`                                                                                     |
| 12| Dependencies             | `dependencies`                                                                                      |
| 13| Build system             | `build`                                                                                             |
| 14| Other improvements       | `internal`                                                                                          |

Anything left with no matching label goes under a trailing **Other** section. Do not silently drop PRs.

Format each entry as `- <cleaned-title> (#<number>)` (matches release-drafter's `change-template`). Strip conventional-commit prefixes from the title using the same replacer regex the config uses:

```
^(build|chore|ci|depr|deps|docs|feat|fix|helm|infra|perf|refactor|release|revert|security|style|test)(\(.*\))?(\!)?:\s
```

#### 8e. Assemble the body

```
## <Category title>

- <title> (#<number>)
- ...
```

Only emit a category header if it has at least one entry. End the body with:

```
**Full changelog**: https://github.com/<owner>/<repo>/compare/<PREV_TAG>...<tag>
```

#### 8f. Publish

Write the body to a temp file and create the release with `--notes-file` (not `--generate-notes`):

```sh
BODY_FILE=$(mktemp)
# write categorized markdown to "$BODY_FILE"
gh release create "<tag>" \
  --target "release/<tag>" \
  --prerelease \
  --title "Tracecat <tag>" \
  --notes-file "$BODY_FILE"
rm -f "$BODY_FILE"
```

If there are zero PRs between `PREV_TAG` and `<tag>` (rare — usually means you tagged the same commit), publish with a single-line body: `No changes since \`<PREV_TAG>\`.`

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
