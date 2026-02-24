# GitHub workflow sync setup

Use this checklist to configure GitHub workflow sync in Tracecat.

1. In Tracecat, go to `Organization settings -> VCS -> GitHub` and click `Connect`.
2. Configure app credentials:
   - `Create new`: use the GitHub App manifest flow from Tracecat.
   - `Use existing`: provide `App ID` and `Private key (PEM)` (optional: `Webhook secret`, `Client ID`).
3. In GitHub, install the app under the **same owner (user/org) that owns the target repository**.
4. In the GitHub app installation settings, grant repository access to the target repo (or all repos if intended).
5. In Tracecat, go to `Workspace settings -> Git repository settings` and set:
   - `Remote repository URL`: `git+ssh://git@github.com/<owner>/<repo>.git`
6. Save settings and run sync operations:
   - Publish: Tracecat -> GitHub
   - Pull workflows: GitHub -> Tracecat

## Common failure case

If publish or pull fails, first verify:

- the app is installed into the correct owner (org/user),
- the installation has access to the target repository.
