---
name: gh-release
description: Cut a stable GitHub release or prerelease directly from a Tracecat release branch, including the version bump, tag, image verification, and categorized release notes.
argument-hint: "[<tag>] [<commit>]"
---

# gh-release

Cut a release directly from a release branch. Do not open a release PR, merge the
release branch into `main`, or advance `main` to record a release. Follow the
version policy in `CONTRIBUTING.md`.

## Choose the version and branch

Parse `$ARGUMENTS` as `<tag> [<commit>]`. Tags are bare public versions without
`v`: `1.2.0-alpha.1`, `1.2.0-alpha.1.1`, `1.2.0-rc.1`, `1.2.0`, or `1.2.1`.
Validate numeric components without leading zeros. Prereleases use `alpha.N`,
`alpha.N.M` (a hotfix of alpha `N`), or `rc.N` and only attach to a `.0`.
Stable patches increment the patch component. If no tag was given, inspect
published releases and ask which version to cut. Do not infer permission to
publish from a request to inspect or prepare a release.

- Before freeze, a new alpha `X.Y.0-alpha.N` starts from the chosen merged `main`
  commit (default `origin/main`) on `release/X.Y.0-alpha.N`.
- An alpha hotfix `X.Y.0-alpha.N.M` reuses `release/X.Y.0-alpha.N`. Require the
  existing branch and a published alpha on that line; no stable release is
  required. Cherry-pick fixes onto it and add successive immutable tags. Never
  create a branch per hotfix or start the hotfix from newer `main` code.
  For example, `1.1.0-alpha.1.1` and `1.1.0-alpha.1.2` both use
  `release/1.1.0-alpha.1`; `1.1.0-alpha.2` starts a new alpha line from `main`.
- At freeze, create `release/<major>.<minor>` from the chosen merged `main`
  commit. RCs, stable minors, and subsequent patches use that train branch.
- For an existing alpha line or frozen train, default to its current remote
  tip. An explicit commit must be that tip; prepare any required cherry-picks
  separately first. Never reset a release branch to `main` or create a patch
  branch from newer trunk code.
- A stable patch requires an existing train branch and a published stable
  release on that train.
- Both alpha hotfixes and stable patches contain fixes that landed on `main`
  first and were cherry-picked onto the release branch. Verify the full diff
  from the previous release: no migration changes, database-schema changes,
  backfills, or new registry actions. If a fix depends on those changes,
  exclude it rather than pulling its feature dependencies into the patch.

Resolve the selected commit to an immutable `COMMIT_SHA` and the branch to
`BRANCH`. A new alpha line's base or a new train's base must be reachable from
`origin/main`; hotfix commits instead descend from their release branch and
retain the source commits' cherry-pick provenance. Do not release an unmerged
PR. Check the selected commit's CI and stop on missing or failing evidence
unless the user explicitly accepts it.
Verify the selected branch contains the stable-only image guard and the current
release workflow before cutting its tag; old train branches may need those
changes cherry-picked first.

## Preflight and confirmation

Fetch branches and tags, inspect the working tree, and verify the public tag is
absent locally, remotely, and from GitHub releases. Distinguish a missing release
from an API/authentication error. If the tag exists, stop; never move it.

A new alpha line's branch must be absent remotely and locally. For an alpha
hotfix or an existing frozen train, reuse the existing release branch; create
a train branch only at its initial freeze. Preserve any prepared local
cherry-picks and verify they fast-forward the remote tip before pushing.
Use an isolated worktree if the branch is checked out elsewhere. Do not
stash, reset, or overwrite unrelated working changes.

### Strict invariant: patches cannot contain database migrations

Every patch release, including alpha hotfixes (`X.Y.0-alpha.N.M`) and stable
patches (`X.Y.Z`, where `Z > 0`), must pass this gate before any version bump,
release commit, push, tag, image build/rebuild, or publication. Prerelease
status does not exempt a patch. Never deploy a patch that violates this
invariant; deployment remains outside this skill's scope.

Resolve and pin `PREV_TAG` using the published-release baseline rules in
**Release notes** below, before mutation. Require it to be an ancestor of
`COMMIT_SHA`. Compare the complete release trees, not just the latest commit
or the cherry-picked fixes:

```sh
git diff --name-status "$PREV_TAG" "$COMMIT_SHA" -- alembic/versions/
git diff "$PREV_TAG" "$COMMIT_SHA"
```

Any added, modified, deleted, or renamed migration is a hard blocker, including
edits to an already-published migration. Inspect the full diff for migrations
outside that directory, database-schema changes, and data backfills; those
also block the patch. Existing migrations unchanged from the baseline are
allowed. If the baseline or inspection cannot be verified, stop.

On a blocker, refuse the patch release and report the baseline, candidate SHA,
and offending files. Release approval, urgency, successful CI, or claims that
a migration is safe do not waive this invariant. Exclude the change and its
dependencies from the patch, or propose a separately approved non-patch
release. Never silently change the requested version to bypass the gate.

Before mutation, show:

- Base commit and CI evidence; branch to create or reuse.
- For a patch, the pinned baseline and evidence that the migration gate passed.
- Public tag and whether the GitHub release is stable or a prerelease.
- Version-bump, branch-push, tag-push, and release-publication commands.
- Stable image tags update `latest`; prereleases leave `latest` unchanged.
  This policy permits an older train's stable hotfix to become `latest`.

Wait for explicit confirmation unless the user already approved this exact
release plan. Approval to merge or fix a PR is not release authorization.

## Bump, commit, and tag

Create or switch to `BRANCH` at the verified commit. Run
`just update-version <tag>` and answer its overwrite prompt only for the
approved release. It writes the public tag to `__version__` and its PEP 440
value to `__pep440_version__` (for example, `1.2.0-alpha.1` becomes `1.2.0a1`,
and `1.2.0-alpha.1.2` becomes `1.2.0a1.post2`).
Verify both application and registry versions agree, and validate the Python
version with `packaging.version.Version` through `uv run python`.

Review the generated diff and, for a patch, repeat the migration gate against
the final release tree (including all version-bump edits) before committing or
pushing. Stage only the changed files individually, and
create a signed `release: <tag>` commit. Never bypass hooks or signing. Push
`BRANCH`. For a prerelease, record both current `latest` image digests before
pushing the tag. Create an annotated `<tag>` on the version-bump commit and push
it. No merge to `main` is involved. Leave the branch in place.

## Verify the images before publication

Tag push triggers `.github/workflows/build-push-images.yml`. Find the run whose
tag and `headSha` match the new release commit, then watch it to successful
completion. Inspect both multi-platform manifests and their version/revision
labels. For a prerelease, compare both `latest` digests before and after the
build; for a stable release, verify `latest` points to the new image manifests.

If image publication fails, stop before creating the GitHub release. Report the
existing branch/tag and failed run; never move the tag or automatically cut a
replacement. To rebuild, use a separate trusted, updated `main` checkout and run:

```sh
uv run python scripts/rebuild_release_images.py '<tag>'
```

The helper requires the remote tag's workflow to exactly match the committed
publisher in that checkout before dispatching with the tag as both ref and
input. An older tag runs its own historical workflow, so guards on `main`
cannot protect a direct dispatch or rerun. On a mismatch or lookup failure,
stop; never bypass the helper or move the tag. A new release containing the
current publisher requires a separately approved release plan. For a retry,
verify the run ID, event, and release commit rather than accepting an older
successful run.

## Release notes

Resolve `PREV_TAG` from published releases, paginating the full list. For an
alpha hotfix, use the previous published hotfix on the same `alpha.N` line,
or its original alpha tag for the first hotfix. For `1.1.0-alpha.1.2`, the
baseline is `1.1.0-alpha.1.1`, even if `1.1.0-alpha.2` has already shipped.
Verify the baseline is an ancestor of the selected release commit.
For other releases, use the previous version on the same major/minor train,
ordered by semantic version (alpha before RC before stable, then patches).
For the first alpha of a new train, use the preceding stable version. If a
train skips alphas, its first
release also uses the preceding stable version. Never pick an unrelated train
merely because it was published most recently. If there is no unambiguous
baseline, ask before publication.

Pin the range to `PREV_TAG..<tag>`. The draft maintained on `main` is not a
release branch's changelog; do not publish or consume that draft.

Generate raw notes with explicit `tag_name`, `previous_tag_name`, and the
version-bump commit as `target_commitish`:

```sh
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
RAW_NOTES=$(gh api "repos/$REPO/releases/generate-notes" \
  --method POST \
  -f tag_name='<tag>' \
  -f previous_tag_name="$PREV_TAG" \
  -f target_commitish='<release-commit>')
```

Extract PR numbers from the returned body and retrieve their titles and labels.
For cherry-picked fixes, verify that the notes include the original merged PRs
represented in the commit range, even if GitHub omits their associations.

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

## Publish and report

Write the categorized Markdown and a `PREV_TAG...<tag>` comparison link to a
temporary notes file. Publish the existing tag using `--verify-tag`, so a typo
cannot create a tag on the default branch:

```sh
# Stable release:
gh release create '<tag>' --verify-tag --latest \
  --title 'Tracecat <tag>' --notes-file "$BODY_FILE"

# Prerelease:
gh release create '<tag>' --verify-tag --prerelease --latest=false \
  --title 'Tracecat <tag>' --notes-file "$BODY_FILE"
```

Run only the command for the approved release type. If the range contains no
changes, state `No changes since <PREV_TAG>.` rather than reusing older notes.

Report the branch, immutable tag/commit, GitHub release URL, image-build run,
and manifest verification. Never delete the alpha-line or train branch,
force-push, amend existing release commits, or deploy as part of this skill.
If signing or a publication step fails, stop and report the concrete state
before attempting further external mutations.
