# GitHub Actions Guidance

These instructions apply to all files under `.github/`, especially `.github/workflows/**`.

## Banned Patterns
- `pull_request_target` is banned in this repository. Do not add it to any workflow.
- Do not check out, execute, or otherwise evaluate untrusted PR code in any privileged context, including jobs with secrets, write-scoped tokens, private network access, or protected environments.
- Do not accept arbitrary refs in CI entrypoints. Ban manual inputs such as raw SHAs, `refs/pull/*`, or free-form `git-ref` values that can be checked out and executed.
- Do not use top-level broad write permissions or `write-all`. Grant write permissions only to the specific job that needs them.
- Do not add `pull-requests: write`, `packages: write`, or `id-token: write` unless a step in that job demonstrably requires that permission.
- Do not use self-hosted runners for untrusted PRs.
- Do not use `secrets: inherit` in reusable workflows unless the callee is fully trusted and the secret flow is documented in the workflow.
- Do not log, upload, cache, or otherwise persist secret values or material derived from secrets.

## Required Controls
- Prefer `push`, `pull_request`, and protected branch or tag triggers.
- Treat `workflow_dispatch` as a privileged operator path, not a convenience default. Add it only when a human-triggered run is genuinely required.
- Guard privileged manual workflows with the `TRUSTED_CI_ACTORS_JSON` Actions variable. Store it as a JSON array of GitHub usernames.
- If a guarded `workflow_dispatch` is triggered by another workflow, explicitly allow `github-actions[bot]` for that path instead of weakening the actor guard globally.
- Declare explicit `permissions:` on every workflow or job. Default to read-only and scope writes to the minimum required permission.
- Use `persist-credentials: false` on `actions/checkout` unless the job must push with `GITHUB_TOKEN`.
- Put high-value credentials in protected environments with required reviewers. In this repo:
  - `CROSS_REPO_AUTOMATION_APP_PRIVATE_KEY` belongs in the `release` environment.
  - `CUSTOM_REPO_SSH_PRIVATE_KEY` belongs in the `internal-registry-ci` environment.
- Ensure external fork PRs cannot reach secret-backed or private-infrastructure jobs.
- For sensitive PR workflows, require `github.event.pull_request.head.repo.full_name == github.repository` before using secrets or private credentials.
- Add `concurrency` to release, tag, publish, deploy, and downstream-dispatch workflows so duplicate runs cannot race each other.
- Validate branch names, versions, tags, and other trusted inputs before mutating releases, tags, registries, or downstream repositories.
- Set `timeout-minutes` on jobs so privileged workflows cannot run indefinitely.
- Pin third-party actions to immutable SHAs.

## Testing Policy
- Tests that require real third-party credentials must be marked with `@pytest.mark.live_secret`.
- Register new secret-dependent markers in `pyproject.toml`.
- Exclude secret-dependent tests from default CI commands with `-m "not live_secret"`.

## Review Checklist
- Review every workflow change for trust boundaries, trigger type, secret exposure, environment usage, repo-owned ref checks, permissions, concurrency, and unnecessary manual triggers.
- Do not treat a successful YAML parse as a security review.
