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

- Token 1 → `<tag>` (optional). A prerelease tag in the convention below, e.g. `1.1.0-alpha.1`, `1.1.0-beta.3`, `1.1.0-alpha.2.6`. Do not include a leading `v` — tags in this repo are bare versions.
- Token 2 → `<commit>` (optional, default `HEAD`). Anything `git rev-parse` accepts: a SHA, branch, `HEAD`, `HEAD~3`, `origin/main`, etc.

Do not rely on `$1`, `$2` — only `$ARGUMENTS` is reliably substituted in skill markdown.

## Version format

Keep `<tag>` in Tracecat's public release/image tag convention. The grammar is:

```
<major>.<minor>.<patch>                      stable
<major>.<minor>.<patch>-<label>.<N>          prerelease, label in {alpha, beta}, N >= 1
<major>.<minor>.<patch>-<label>.<N>.<M>      hotfix on top of that prerelease, M >= 1
```

The base is always the **next** stable version, so the first prerelease after
`1.0.0` is `1.1.0-alpha.1` (or `1.0.1-alpha.1` for a patch series). Series
numbers start at 1. The pre-1.0 chained form (`1.0.0-beta.52-rc.22`) and the
`rc` label are retired.

Ordering:

```
1.1.0-alpha.2 < 1.1.0-alpha.2.1 < 1.1.0-alpha.2.6 < 1.1.0-alpha.3 < 1.1.0-beta.1 < 1.1.0
```

`just update-version <tag>` writes the public tag to `__version__`, and writes a
separate PEP 440-compatible value to `__pep440_version__` for Hatchling package
metadata. This lets Git branches, GitHub releases, and image tags stay as
`1.1.0-alpha.2.6` while Python builds use `1.1.0a2.post6`.

| Public `<tag>` | Python package version |
|----------------|------------------------|
| `1.1.0-alpha.2` | `1.1.0a2` |
| `1.1.0-beta.3` | `1.1.0b3` |
| `1.1.0-alpha.2.6` | `1.1.0a2.post6` |
| `1.1.0` | `1.1.0` |

**Hotfixes.** A `<label>.<N>.<M>` tag is a hotfix on an already-cut prerelease.
Cut it from the existing `release/<base>-<label>.<N>` branch (or from
`release/<base>-<label>.<N>.<M-1>` for a follow-up hotfix) by passing that
branch as `<commit>`. `update-version.sh --hotfix` implements the same
semantics: `1.1.0-alpha.2` gives `1.1.0-alpha.2.1`, and `1.1.0-alpha.2.1` gives
`1.1.0-alpha.2.2`.

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

If `<tag>` is missing, suggest the next logical tag from the most recently created tag. Sort by creation date, not `v:refname`: version sort ranks `1.0.0-beta.52` above `1.0.0`, so it would suggest a new beta on an already-shipped base.

```sh
git fetch --tags --prune
LATEST=$(git tag --sort=-creatordate | rg -m1 -- '^[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta)\.[0-9]+(\.[0-9]+)?)?$' || true)
```

- If `LATEST` is a prerelease, bump its **last numeric component**:
  `1.1.0-alpha.2` gives `1.1.0-alpha.3`, and `1.1.0-alpha.2.6` gives `1.1.0-alpha.2.7`.
  ```sh
  PREFIX="${LATEST%.*}"
  LAST="${LATEST##*.}"
  SUGGESTED="${PREFIX}.$((LAST + 1))"
  ```
- If `LATEST` is a stable release, offer the next minor's first alpha:
  `1.0.0` gives `1.1.0-alpha.1`.
  ```sh
  MAJOR="${LATEST%%.*}"
  MINOR="${LATEST#*.}"; MINOR="${MINOR%%.*}"
  SUGGESTED="${MAJOR}.$((MINOR + 1)).0-alpha.1"
  ```
- If no tag matches, stop and ask — there is no unambiguous "next" without a precedent.

Present the suggestion in one line and wait for explicit confirmation (`y` to accept, or have the user supply an alternative). Do not proceed silently. If the user accepts, use the suggestion as `<tag>` for the rest of the workflow.

Validate `<tag>` against `^[0-9]+\.[0-9]+\.[0-9]+-(alpha|beta)\.[0-9]+(\.[0-9]+)?$`. If it is a stable release (e.g. plain `1.1.0`), refuse and point the user at `.github/workflows/create-release.yml` — this skill is for prereleases only.

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

Important: pass the public release tag, e.g. `1.1.0-alpha.2`. `update-version.sh` keeps that value in `__version__` and writes the PEP 440 equivalent, e.g. `1.1.0a2`, to `__pep440_version__` for Python package builds.

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

Read `.github/release-drafter.yml` and derive the buckets from it. Do not copy
the category list into this skill: the last copy drifted, and a stale copy
silently files changes under headings the real release notes do not have.

From that file you need:

- `exclude-labels`: drop any PR carrying one of these.
- `categories`, in order: bucket each remaining PR into the **first** category
  whose `labels` intersect the PR's labels. Use the category's `title`
  verbatim.

Anything left with no matching label goes under a trailing **Other** section. Do
not silently drop PRs.

Format each entry as `- <title> (#<number>)`, matching the config's
`change-template`, then apply every rule in the config's `replacers:` to the
assembled body, in order. Read them from the file the way you read
`categories:`; do not restate them here.

They are not cosmetic, and they are not only scope aliases. They rewrite the
type prefix too -- `chore(deps)` and `fix(deps)` become `build(deps)`, a scope
that merely repeats its type is dropped, `feat!(api)` moves the bang to
`feat(api)!` -- and they capitalize the first letter of most descriptions.
Three shapes are spared, and the config lists them: a first word holding `_` or
`.`, a first word with a capital after its first letter, and an explicit list of
lowercase names that must not be Title-cased. Read the guard from the file
rather than reasoning about which of the three applies.

Skip the replacers and this skill's output disagrees with the stable release
notes for the same commits.

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

- Refuse if `<tag>` is a stable release (no `-alpha.<N>` or `-beta.<N>` suffix). Stable releases go through `create-release.yml` → PR → `publish-release.yml`.
- Wait for explicit `y` at step 2. Never push branches or tags before confirmation.
- Never force-push the tag or branch. If something is wrong post-push, stop and ask — do not `--force`.
- Never use `git add -A` or `git add .`. Stage paths individually.
- Never bypass commit signing (`--no-gpg-sign`) or hooks (`--no-verify`). If signing fails, stop and ask the user to fix it.
- Do not include AI/agent attribution in the commit or tag messages.
- The release branch is **long-lived**. Do not delete it after the prerelease is cut, even if the prerelease is later promoted or abandoned.
- If the user later wants to cut another prerelease against the same base, suggest using the existing `release/<previous-tag>` branch as the `<commit>` argument so the version-bump history stays linear.
