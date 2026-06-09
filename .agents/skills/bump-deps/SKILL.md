---
name: bump-deps
description: Use when bumping dependencies to patch open Dependabot or other CVE/advisory alerts for this repo's Python (uv/pyproject.toml) and frontend (pnpm/package.json) dependencies. Covers triaging alerts, finding the minimal safe exact upgrade, checking active supply-chain incidents, reading changelogs before classifying risk, validating dependency versions and lockfiles through Socket Firewall, verifying behavior without Socket Firewall, and opening a security PR. Trigger on requests like "patch the Dependabot alerts", "fix the CVE in a package", "bump a package to a safe version", or "resolve the security advisory".
---

# Patch Vulnerable Dependency

Resolve vulnerable dependencies by applying the smallest exact upgrade that clears the advisory, respecting this repo's pinning and lockfile conventions, and proving the change builds and behaves correctly before opening a PR. Default to the minimal patched version, not the latest release.

## Socket Firewall

Use Socket Firewall Free for dependency work. It is wrapper-mode only: prefix package-manager commands with `sfw`. It supports this repo's package managers (`pnpm` and `uv`) with no API key or config.

All package-manager metadata, install, update, lockfile, resolver, and resolved-version verification commands in this workflow must be directly visible as top-level `sfw` commands. The command argv must start with one of these forms:

```bash
sfw pnpm ...
sfw uv ...
sfw --verbose uv ...
```

Do not run package-manager dependency work through `node -e`, `python -c`, shell loops, helper scripts, subshells, or any wrapper whose top-level command is not `sfw`. Do not call `sfw` indirectly from inside another process. Do not use unwrapped `pnpm`, `npm`, `uv`, `pip`, or `uvx` for dependency metadata, artifact download, resolution, installation, update, lockfile, or resolved-version verification work. Do not use direct registry HTTP clients such as `curl`, browser downloads, or custom scripts for package metadata during this workflow.

GitHub advisory reads with `gh api` are not package-manager dependency work, so Socket Firewall does not apply to them. Keep advisory reads separate from registry, resolver, install, lockfile, and resolved-version verification commands.

QA is not Socket Firewall work. Run lint, typecheck, test, build, browser QA, API smoke, and workflow smoke commands without `sfw`, even when they use `uv` or `pnpm` as the command runner. Socket Firewall validates dependency metadata and artifacts; it must not wrap QA execution. Examples:

```bash
uv run ruff check .
uv run basedpyright --warnings --threads 4
uv run pytest tests/unit -n auto
pnpm check
pnpm run typecheck
pnpm test
pnpm build
```

Before any dependency install, update, lockfile, resolver, or package-manager metadata query, verify `sfw` is available:

```bash
sfw --help
```

Then use `sfw` for every package-manager command that can fetch package metadata or artifacts. Install/update targets must be exact versions: no `latest`, dist-tags, ranges, `^`, `~`, `>=`, wildcards, or omitted versions.

Every exact version written into a manifest, package-manager override, uv override, or parent dependency bump must be validated through a directly visible top-level `sfw` command before it is kept in the final diff. Advisory data, GitHub tags, upstream source files, release notes, and compare views can suggest candidate versions, but they do not validate that a package-manager-resolved version is safe to use. This applies to:

- The vulnerable package's patched version.
- Transitive security overrides such as `pnpm.overrides` and `[tool.uv] override-dependencies`.
- Parent package bumps used to pull in a patched transitive version.
- Companion dependency bumps needed to resolve the chosen target.

For frontend candidates, validate the exact version with Socket-wrapped npm metadata before editing or retaining it:

```bash
sfw pnpm view <package>@<exact-version> version --json
sfw pnpm view <package>@<exact-version> repository scripts --json
```

For Python candidates, validate the exact version with the Socket-wrapped uv resolver before retaining it. If the existing manifest pin prevents an exact-target dry run from resolving, apply the candidate only as a temporary working-draft edit, then immediately run a Socket-wrapped uv dry run against the candidate manifest state. If the dry run fails or Socket blocks/warns materially, remove or change that candidate before proceeding.

```bash
sfw --verbose uv lock --dry-run -P <package>==<exact-version>
sfw --verbose uv lock --dry-run
```

Do not keep an override, parent bump, or companion bump merely because GitHub metadata showed that version exists. If a required Python parent or override version cannot be validated by a directly visible `sfw uv ...` resolver command, stop and ask for guidance.

```bash
# Frontend: run from frontend/. Do not pass pnpm directory flags through sfw.
sfw pnpm view <package> versions --json
sfw pnpm update <package>@<exact-version>
sfw pnpm install

# Python: run from the repo root.
sfw --verbose uv lock --dry-run -P <package>==<exact-patched-version>
sfw uv lock -P <package>==<exact-patched-version>
sfw uv sync
```

Always pin `-P` to the exact patched version (`-P <package>==<exact-patched-version>`), not the bare `-P <package>` form. Bare `-P <package>` is uv's "upgrade to highest compatible" directive: for a transitive package with no exact manifest pin to bound it, uv jumps to the latest release in range, not the advisory's `first_patched_version`. That violates the smallest-exact-upgrade rule. Pinning `==<exact-patched-version>` keeps uv at the minimal fix. For a direct dependency, also set the exact pin in `pyproject.toml` (Step 7) so the manifest already bounds the target. If a transitive package cannot be constrained to the minimal version with `-P ...==...` alone, add a temporary `[tool.uv] override-dependencies` entry pinning the exact patched version, validate it through `sfw`, then lock.

For frontend commands, set the shell/tool working directory to `frontend/` first or `cd frontend` before running the command. Do not use `sfw pnpm --dir frontend ...` or `sfw pnpm -C frontend ...`; in this environment those forms can be forwarded incorrectly and return `undefined` or run the wrong command.

For Python, Socket Firewall Free's uv wrapper form is `sfw uv ...`. Use `sfw --verbose uv lock --dry-run -P <package>` to ask uv what it would update without writing `uv.lock`; uv prints a recommendation such as `Update idna v3.11 -> v3.18`. Treat that as resolver input, not as permission to skip the minimal patched version from the advisory or to keep an unvalidated override/parent version.

Socket Firewall Free blocks confirmed malware, warns on AI-detected potential malware, and does not block unknown or unscanned package versions. Treat warnings or unknown/high-risk packages as supply-chain review inputs; do not ignore them because install succeeded.

If `sfw` blocks a package, stop that package's ready PR path and create a draft PR with blocker analysis. If `sfw` is missing, stop and surface the environment issue; do not run unwrapped package-manager commands.

If you accidentally run a package-manager metadata, install, update, lockfile, resolver, or resolved-version verification command without a directly visible `sfw` top-level command, stop immediately, report the mistake, discard any untrusted dependency result from that command, and rerun the step only through the allowed `sfw` forms above. This rule does not apply to QA commands; QA commands should be rerun plainly, without `sfw`.

`sfw` intercepts network requests. If artifacts are already cached, there may be no network request for Socket to inspect. For high-risk or suspicious packages, use a clean runner/cache or explicitly note that cache state limited firewall coverage.

## Workflow

1. Enumerate open alerts and deduplicate to one row per `(ecosystem, package)`. The same GHSA can appear across `package.json` and lockfiles; a package can have several GHSAs; and the same name can exist in different ecosystems as unrelated packages.
2. For each package, find the minimal patched version and where it is declared (direct vs. transitive).
3. Check current supply-chain incident context for touched ecosystems and packages.
4. Read the actual changelog/release/commit delta from current version to target version.
5. Plan the PR split (see Step 5): exactly two PRs, one per package manager (Python/uv, frontend/pnpm).
6. Present the plan for a single review gate and wait for approval. This is the only prompt.
7. After approval, execute each PR unattended: apply the upgrade, regenerate the lockfile, re-check the resolved version is out of range, verify, QA if risky, open the PR.

This skill runs autonomously after the plan is approved. Do not prompt per package, per bump, or per PR. Only the plan in Step 5 is gated, plus true blockers: no patch exists, `sfw` missing or blocking, signing broken, tests fail, or required QA cannot run.

## Step 1: Enumerate and deduplicate alerts

List open Dependabot alerts with the GitHub CLI:

```bash
gh api repos/:owner/:repo/dependabot/alerts --paginate \
  -q '.[] | select(.state=="open") | {number, severity: .security_advisory.severity, ghsa: .security_advisory.ghsa_id, package: .dependency.package.name, ecosystem: .dependency.package.ecosystem, manifest: .dependency.manifest_path, vulnerable_range: .security_vulnerability.vulnerable_version_range, patched: .security_vulnerability.first_patched_version.identifier}'
```

Deduplicate to one row per `(ecosystem, package)`, never by package name alone. The raw list double-counts: the same GHSA appears once for `package.json` and once for `pnpm-lock.yaml`, and one package can have many GHSAs. Collapse to a table keyed by `(ecosystem, package)`, carrying the highest severity, set of GHSAs, and the set of vulnerable ranges. When a row has several GHSAs, retain every `first_patched_version` rather than a single value: the minimal safe target is the **highest** of those patched versions (the lowest version that clears every vulnerable range at once). Picking any one GHSA's patched version can leave another advisory unresolved. Record both the per-GHSA patched versions and the computed minimal target. Set `patched: null` rows aside immediately; they have no fix.

The ecosystem must stay in the key: the same package name can exist in both PyPI and npm as unrelated packages with different version lines, vulnerable ranges, and patched versions (e.g. `redis` on both). Merging them on name alone would compute a target valid for neither and would route one ecosystem's fix into the wrong package-manager PR (Step 5) or drop it entirely. Compute the minimal safe target **within** each `(ecosystem, package)` row, never across ecosystems.

If the user names a specific package, CVE, or GHSA, filter to it. If the repo has no GitHub-hosted alerts or the user pastes an advisory directly, use the advisory's affected range and first patched version as the source of truth.

## Step 2: Find the minimal patched version

The goal is the lowest version that is no longer in the vulnerable range. From the alert, that is `first_patched_version`. Cross-check the advisory before bumping:

```bash
gh api /advisories/<GHSA-ID> -q '{summary, severity, vulnerabilities: [.vulnerabilities[] | {ecosystem: .package.ecosystem, package: .package.name, vulnerable_version_range, first_patched_version}]}'
```

A single GHSA can list several `vulnerabilities[]` entries for different packages or ecosystems (e.g. a core package plus a plugin, or the same advisory spanning npm and PyPI). Each entry carries its own `package.ecosystem`, `package.name`, vulnerable range, and `first_patched_version`. Use only the entry matching the alert's `(ecosystem, package)` when picking the target; never take a vulnerable range or patched version from the advisory output without confirming which package it belongs to. If no entry matches the alert's package, stop and re-check the GHSA ID rather than guessing.

Always verify the target against published package metadata before deciding the target. Never reason about version ordering from memory or assumptions. Version schemes are not always obvious: a package can have both a `0.x` and a `1.x` line, calendar versioning, or pre-releases.

For frontend packages, use `sfw pnpm view` to list published versions. For Python packages, do not use direct PyPI JSON, `curl`, browser downloads, or custom scripts. Use the GitHub advisory's `first_patched_version` as source of truth and use Socket-wrapped `uv` dry-run/lock commands to verify the target is resolvable. If resolving the Python target requires a full PyPI version list that cannot be obtained through a directly visible `sfw uv ...` command, stop and ask for guidance instead of using an unwrapped registry lookup.

If a transitive fix requires a parent bump or an override, validate the exact parent or override version with `sfw` before putting it in the plan and before keeping it in the manifest. Do not select a parent bump only by reading GitHub tags, package source metadata, or release notes. Use those sources to find candidates, then prove the exact candidate through `sfw pnpm view <package>@<version> ...` or `sfw --verbose uv lock --dry-run` in the candidate manifest state.

```bash
# Python: ask uv what it would resolve without mutating uv.lock.
# Pin the exact patched version; bare -P <package> resolves to the highest in-range release.
sfw --verbose uv lock --dry-run -P <package>==<exact-patched-version>

# Frontend: run from frontend/.
sfw pnpm view <package> versions --json
```

Get the currently resolved version from the lockfile, not the manifest constraint. For example, the `version =` line under the package in `uv.lock`, or the `<package>@x.y.z:` key in `pnpm-lock.yaml`.

Confirm against Socket-wrapped registry or resolver output:

- Current resolved version is inside the vulnerable range.
- For frontend packages, patched version exists in `sfw pnpm view` output, is reachable from current version (same major line if possible), and is the smallest published version clearing the vulnerable range.
- For Python packages, advisory `first_patched_version` is target, and directly visible `sfw uv ...` resolver output confirms target can resolve without direct PyPI metadata.
- For every override, parent bump, or companion bump, the exact version is validated by a directly visible `sfw` command before it appears in the final plan or retained manifest diff.

Watch for version-scheme traps:

- `0.x` line below a `1.x` release, e.g. resolved `0.49.3`, vulnerable `< 1.0.0`, patched `1.0.1`.
- Major-version jump disguised by a caret range, e.g. resolved `10.0.0` from `^10`, patched `11.1.1`.
- Large multi-minor jump on a library the repo uses.

These traps may carry breaking-change surface. Confirm against Step 4's change delta before deciding whether the bump is risky and needs targeted QA.

Prefer the smallest version that clears the range. Only jump to a major version when no patch exists on the current major line, and call that out explicitly. If the registry shows no patched version at all (`patched: null`), do not invent one.

## Step 3: Check active supply-chain incidents

Before planning PRs or installing anything, check current public supply-chain incident context. Do not rely on memory: this info changes daily.

Search current sources for each touched ecosystem and package:

- Socket package pages, Socket blog/incidents, and Socket Firewall output.
- GitHub Security Advisories and advisory references.
- npm/PyPI package registry metadata visible through `sfw`-wrapped package-manager commands.
- Maintainer release notes, changelogs, issues, PRs, and discussions.
- Public reports for active npm/PyPI supply-chain attacks.

Use focused searches such as:

- `<package> compromised maintainer`
- `<package> malware postinstall token theft`
- `<package> typosquat dependency confusion`
- `<package> protestware credential exfiltration`
- `npm supply chain attack <current month>`
- `PyPI supply chain attack <current month>`

Record the incident check in the plan and PR body: sources checked, relevant findings, and whether any active incident affects package, maintainer, dependency tree, or install scripts. If active compromise is plausible, do not open a ready PR; create a draft blocker PR.

## Step 4: Read the actual change delta

Before classifying a bump as mechanical or risky, read what changed between the currently resolved version and target version. Do not classify from package name, ecosystem topic, or vibes.

Use the best available primary source:

- Package changelog or release notes.
- Compare view between old and target tags.
- Commits between old and target tags when release notes are absent.
- Upstream PRs/issues linked from the release.

Record a one-line delta summary per package in the plan, such as:

- `authlib 1.6.11 -> 1.6.12: one bugfix commit; no public API or migration notes`
- `package 1.62 -> 1.99: 37 minor releases; migration notes mention API changes`

Classification rules:

- Bugfix/patch delta with no migration notes, no public API change, no install-script change, and no relevant code usage change is mechanical even if package belongs to a sensitive domain such as auth, cloud, crypto, AI, or database.
- Direct repo usage is context, not a risk signal by itself.
- Mark a bump risky (needs targeted QA in Step 10) only when the actual delta shows breaking-change surface, version distance is large enough that release notes cannot be confidently reviewed, tests fail in package-affected code, or targeted QA is needed. Risk classification drives QA, not PR count — the bump still ships in its ecosystem's single PR.
- Never mark a bump risky solely because package name/category sounds sensitive.

## Step 5: Plan the PR split

Group deduplicated packages into PRs, then present the plan for a single review gate. This is the only prompt in the run. After approval, execute every PR unattended.

Grouping rules:

- Open exactly two PRs, split by package manager: one for Python/uv (`pyproject.toml` + `uv.lock`) and one for frontend/pnpm (`frontend/package.json` + `frontend/pnpm-lock.yaml`). All of an ecosystem's bumps — mechanical and risky alike — go in that ecosystem's single PR. Do not peel risky bumps into their own PR.
- If an ecosystem has no open alerts, skip its PR; you may end up with one PR. Never produce more than one PR per package manager.
- Step 4's risk classification still matters: it decides the QA each PR gets (Step 10), not how many PRs there are. A PR that contains any risky bump must pass that bump's targeted QA before it is marked ready.
- Set aside `patched: null` packages; surface them, do not PR.

Present a short plan table: the two PRs, package `old -> new`, severity, delta summary, incident check summary, and one-line QA note per PR. Note which bumps in each PR are risky and what targeted QA they require. Keep QA high-level in the plan, e.g. `frontend: typecheck + tests, plus browser QA for <risky package>`, `python: ruff + basedpyright + unit tests, plus focused tests for <risky package>`. Wait for approval, then run each PR through Steps 6-11.

## Step 6: Locate the declaration

Determine whether package is direct (declared in a manifest) or transitive.

```bash
# Python direct declaration?
rg -n "<package>" pyproject.toml packages/*/pyproject.toml

# Frontend direct declaration?
rg -n "\"<package>\"" frontend/package.json frontend/*/package.json
```

- Direct dependency: edit manifest constraint, then regenerate lockfile.
- Transitive dependency: prefer bumping parent that pulls it in. If parent already allows a patched version, regenerating lockfile alone may fix it. If not, add exact constraint/override and explain why. Before choosing or retaining any parent bump or exact override version, validate that exact version through Socket-wrapped package-manager metadata or resolver output as described in Step 2.

Repo manifests:

- Root Python: `pyproject.toml` + `uv.lock`
- Registry/admin/EE packages: `packages/*/pyproject.toml`
- Frontend: `frontend/package.json` + `frontend/pnpm-lock.yaml`

## Step 7: Apply the upgrade

### Python (uv)

Pin to exact versions. Never switch a pin to a range-based constraint. Edit `pyproject.toml` so direct constraint is patched exact version, e.g. `package==1.2.4`. Do not install or lock a Python target unless manifest or override names an exact version, and do not retain that exact version unless a directly visible `sfw uv ...` resolver command has validated the candidate.

For a transitive package with no direct declaration, pin it under project constraints/overrides mechanism (for example `[tool.uv]` override-dependencies or an added direct pin) rather than hand-editing `uv.lock`, and comment why it exists. The exact override version must be validated by `sfw --verbose uv lock --dry-run` in the candidate manifest state before it is kept.

### Frontend (pnpm)

Use `pnpm`, never `npm`. Run commands from `frontend/`. For a direct dependency:

```bash
sfw pnpm update <package>@<exact-version>
```

Manifest entry for touched direct dependency must become exact version string. Remove existing `^`, `~`, ranges, or tags for that package.

For a transitive dependency, add an exact `pnpm.overrides` entry in `frontend/package.json`:

```jsonc
"pnpm": {
  "overrides": {
    "<package>": "<exact-version>"
  }
}
```

Overrides must be exact versions. Do not use `>=`, `^`, `~`, dist-tags, or wildcards for security overrides. Validate the exact override target with `sfw pnpm view <package>@<exact-version> version --json` before adding or retaining the override, then regenerate the lockfile through `sfw pnpm install` or `sfw pnpm update`.

## Step 8: Regenerate the lockfile

Never hand-edit a lockfile. Regenerate with package manager so resolution is consistent.

Python:

```bash
sfw uv lock -P <package>==<exact-patched-version>
sfw uv sync
```

Use one `-P <package>==<exact-patched-version>` per package being upgraded, pinning the exact target so uv does not climb to the highest in-range release. Omit `-P` only when the edited manifest pin already forces the exact intended version. Never use the bare `-P <package>` form for an unpinned or transitive package: with no constraint to bound it, uv selects the latest compatible version instead of the minimal patched one. Do not use `uv pip compile ... -o uv.lock`; it writes requirements-format output, not this repo's TOML `uv.lock` format.

Frontend: run from `frontend/`; `sfw pnpm update`/`sfw pnpm install` updates `pnpm-lock.yaml` in place:

```bash
sfw pnpm install
```

Confirm diff is scoped:

```bash
git diff uv.lock frontend/pnpm-lock.yaml
```

If unrelated packages moved, investigate before continuing.

Re-check resolved version after regenerating. Read lockfile and confirm target package is outside vulnerable range. If still vulnerable, parent does not allow patched version; add exact override/constraint (Step 7) and regenerate again.

## Step 9: Verify

Run checks relevant to changed ecosystem. These are QA commands, so do not wrap them with `sfw`. The environment should already have been synchronized through Step 8. If an install or sync is still required, perform that dependency step through `sfw`, then return to plain QA commands.

If a Python dependency can affect the API schema or generated frontend client, regenerate the client before frontend QA. This includes `fastapi`, `starlette`, `pydantic`, `pydantic-settings`, `fastapi-users`, OpenAPI/codegen packages, and any dependency bump that changes generated files under `frontend/src/client/`.

```bash
just gen-client-ci
```

After client generation, inspect `frontend/src/client/` changes. If generated type names, request body types, or multipart form field types changed, fix the frontend call sites in the same PR. Do not report frontend validation until `pnpm run typecheck` has passed after the last `just gen-client-ci`, generated-client diff, dependency edit, or frontend fix. If a later validation hook or pre-commit step regenerates the client, rerun frontend QA again.

Python:

```bash
uv run ruff check .
uv run basedpyright --warnings --threads 4
uv run pytest tests/unit -n auto
```

Frontend: run from `frontend/`.

```bash
pnpm check
pnpm run typecheck
pnpm test
```

If bump changes API the code uses, expect failures and fix call sites in same PR. If a check fails for environmental reasons, record exact command and output instead of claiming pass.

## Step 10: QA risky bumps

Each PR always gets its ecosystem's baseline QA (Step 9). On top of that, any **risky bump inside a PR** needs targeted QA beyond lint, typecheck, and unit tests. A bump is risky when Step 4's actual change delta shows breaking-change surface, version span is too large to review confidently, tests fail in package-affected code, or the package change requires browser/API/workflow QA. Do not classify a bump as risky solely because the package belongs to a sensitive topic area.

Since each ecosystem ships in one PR, a single PR can mix mechanical and risky bumps. Run the targeted QA for every risky bump the PR contains, and do not mark the PR ready until all of them pass. If the frontend PR contains any risky bump, run production build from `frontend/`:

```bash
pnpm build
```

Then perform targeted browser QA for flows that actually use changed package. Do not treat generic frontend smoke suite as comprehensive enough for risky dependency PRs. It can be an extra signal, but cannot replace package-specific browser QA.

Examples:

- Graph or drag/drop packages: create/open workflow editor, drag nodes, connect edges, save, reload.
- Auth/session packages: sign in, refresh, sign out, verify protected route handling.
- Data grid/table packages: load tables/cases, sort, filter, paginate, edit if supported.
- Editor/Markdown packages: open editor, type, render preview, save, reload.
- API/client/query packages: load data-heavy pages, mutate data, verify error/loading states.

If browser QA needs local services, follow repo stack safety before starting them:

```bash
docker compose ls --filter name=tracecat
just cluster up -d
just cluster ps
```

Record exact browser QA steps and results in PR body. If targeted browser QA cannot run for environment reasons, keep the PR draft and include the blocked command/action, failure, and remaining QA needed. A blocked risky bump keeps its whole ecosystem PR in draft.

If the Python PR contains any risky bump, run focused tests for modules that import or depend on that package, then broaden to unit tests. If the package affects runtime services, start the stack and do live API or workflow smoke.

## Step 11: Open the PR

Do not bypass commit signing with `--no-gpg-sign` or `--no-verify`. If signing is broken, stop and ask user.

Use a conventional-commit title with the `chore(deps)` prefix under 72 chars. Title by ecosystem since each PR bumps several packages, e.g. `chore(deps): patch Python dependency vulnerabilities` or `chore(deps): patch frontend dependency vulnerabilities`. When a PR has a single package, naming it is fine, e.g. `chore(deps): patch <package> vulnerability`.

Write PR body to file with single-quoted heredoc, never inline Markdown with `gh pr create --body "..."`.

Each ecosystem PR bumps several packages at once, so the body lists one row per package, flagging which rows are risky and what targeted QA they received.

```bash
cat > /tmp/sec-pr-body.md <<'EOF'
## Summary
Resolve <N> Dependabot alert(s) for the <Python/uv | frontend/pnpm> dependencies (<highest severity>).

| Package | Old | New | Severity | Advisory | Risk |
| --- | --- | --- | --- | --- | --- |
| `<package>` | `<old>` | `<new>` | <severity> | <GHSA-ID> | mechanical / risky |
| ... | ... | ... | ... | ... | ... |

## Validation
- `sfw <package-manager> ...` lockfile regenerated; diff scoped to bumped packages.
- Resolved versions re-checked outside each vulnerable range.
- Active supply-chain incident check completed; findings documented.
- Changelog/release/commit delta reviewed; mechanical vs risky classification documented.
- Lint / typecheck / unit tests green from plain, unwrapped QA commands (see commands run).
- Risky-bump QA: <per-risky-package targeted browser/API/workflow steps and result, or "no risky bumps in this PR">.
EOF

gh pr create --body-file /tmp/sec-pr-body.md
gh pr view <number> --json body --jq .body
```

Label on best-effort basis from existing label set:

```bash
gh label list
gh pr edit <number> --add-label "<existing-security-or-dependencies-label>"
```

## Guardrails

- Minimal upgrade by default; major bumps only when no patch exists on current major, and called out explicitly.
- Must install/update only exact versions for touched packages and overrides. No `latest`, dist-tags, ranges, `^`, `~`, `>=`, wildcards, or omitted versions.
- Must validate every exact version used in an override, uv override, transitive pin, parent bump, or companion bump through a directly visible top-level `sfw` package-manager command before retaining it in manifests or PRs.
- Exact pins in `pyproject.toml`; never convert a pin to range.
- Regenerate `uv.lock` explicitly; let pnpm regenerate `pnpm-lock.yaml`; never hand-edit lockfile.
- For backend API/schema dependency bumps, run `just gen-client-ci` before frontend QA, fix any generated-client call-site fallout, and rerun frontend typecheck after the final generated-client state.
- Exactly two PRs, split by package manager (Python/uv and frontend/pnpm); all of an ecosystem's bumps go in its one PR, mechanical and risky together. Never peel risky bumps into a separate PR. Keep each PR's lockfile diff scoped.
- `uv` and `pnpm` only; no `pip install` into environment, no `npm`.
- All `uv` and `pnpm` dependency metadata, install, update, lockfile, resolver, and resolved-version verification commands must run through `sfw`; lint, typecheck, test, build, and manual QA commands must run without `sfw`.
- If a PR contains any risky bump, do not mark it ready without that bump's targeted QA (browser QA for frontend; generic smoke tests are not enough).
- Never bypass commit signing or hooks.
- Do not paste real tokens, advisory-internal identifiers, or customer values into committed files.
- If only fix is breaking major bump, or no patch exists yet, surface that and ask how to proceed instead of forcing it.
