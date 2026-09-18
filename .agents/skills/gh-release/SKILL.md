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
`v`: `1.2.0-alpha.1`, `1.2.0-rc.1`, `1.2.0`, or `1.2.1`. Validate numeric
components without leading zeros. Prereleases use `alpha.N` or `rc.N` and only
attach to a `.0`; patches are stable. If no tag was given, inspect published
releases and ask which version to cut. Do not infer permission to publish from
a request to inspect or prepare a release.

- Before freeze, an alpha starts from the chosen merged `main` commit (default
  `origin/main`) on a new `release/<tag>` snapshot branch.
- At freeze, create `release/<major>.<minor>` from the chosen merged `main`
  commit. RCs, stable minors, and subsequent patches use that train branch.
- For an existing train, default to its current remote tip. An explicit commit
  must be that tip; prepare any required cherry-picks separately first. Never
  reset a train to `main` or create a patch train from newer trunk code.
- A patch requires an existing train branch and a published stable release on
  that train. Fixes land on `main` first and are cherry-picked onto the train;
  exclude new migrations and registry actions.

Resolve the selected commit to an immutable `COMMIT_SHA` and the branch to
`BRANCH`. An alpha base or a new train's base must be reachable from
`origin/main`; do not release an unmerged PR. Check the selected commit's CI
and stop on missing or failing evidence unless the user explicitly accepts it.
Verify the selected branch contains the stable-only image guard and the current
release workflow before cutting its tag; old train branches may need those
changes cherry-picked first.

## Preflight and confirmation

Fetch branches and tags, inspect the working tree, and verify the public tag is
absent locally, remotely, and from GitHub releases. Distinguish a missing release
from an API/authentication error. If the tag exists, stop; never move it.

A new snapshot branch must be absent remotely and locally. For a train branch,
reuse its remote tip or create it at the verified base if this is the initial
freeze. Use an isolated worktree if the branch is checked out elsewhere. Do not
stash, reset, or overwrite unrelated working changes.

Before mutation, show:

- Base commit and CI evidence; branch to create or reuse.
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
value to `__pep440_version__` (for example, `1.2.0-alpha.1` becomes `1.2.0a1`).
Verify both application and registry versions agree, and validate the Python
version with `packaging.version.Version` through `uv run python`.

Review the generated diff, stage only the changed files individually, and
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
replacement. A rebuild uses the existing tag as both ref and input:

```sh
gh workflow run build-push-images.yml --ref '<tag>' --field 'tag=<tag>'
```

Do not dispatch from `main` with an unrelated tag input. The image workflow
rejects ref/tag mismatches. For a retry, verify the run ID, event, and release
commit rather than accepting an older successful run.

## Release notes

Resolve `PREV_TAG` from published releases, paginating the full list. Use the
previous version on the same major/minor train, ordered by semantic version
(alpha before RC before stable, then patches). For the first alpha of a new
train, use the preceding stable version. If a train skips alphas, its first
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

Stable GitHub publication triggers `notify-release.yml`, which requests the
existing Kubernetes version-bump automation. Prereleases do not trigger that
notification. Verify the notification run separately from image publication;
its success does not prove a deployment occurred.

Report the branch, immutable tag/commit, GitHub release URL, image-build run,
manifest verification, and notification status when applicable. Never delete
the train branch, force-push, amend existing release commits, or deploy as part
of this skill. If signing or a publication step fails, stop and report the
concrete state before attempting further external mutations.
