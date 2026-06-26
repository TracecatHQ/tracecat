# Core concepts

Product-level meaning of the building blocks. (Workflow DSL syntax lives in
`tracecat-manage-workflows`; this is about what each thing *is*.)

## Secrets

- Sensitive values: API keys, bot tokens, SSH keys, certs.
- Stored under **Credentials** (`/credentials`), scoped to an **environment**
  (`default`, `staging`, etc.).
- Referenced as `${{ SECRETS.<name>.<KEY> }}` in action args and preset instructions.
- Resolved at execution time. Never exposed to an LLM by the secure injection path.

## Variables

- Non-secret config: base URLs, project IDs, queue names, routing rules.
- Stored under **Variables** (`/variables`), also scoped per environment.
- Referenced as `${{ VARS.<name>.<key> }}`.
- Use for tenant-specific defaults and shared config — anything you'd otherwise hardcode.

## Environments

- Both secrets and variables are scoped to a named environment, with `default` as the
  fallback.
- An action can override its environment to use a different set of credentials for the
  same integration (e.g. prod vs staging API keys).

## Expressions

- Wrapped in `${{ ... }}`. Resolve against:
  - `TRIGGER.<field>` — the data that started the workflow.
  - `ACTIONS.<ref>.result` — output of an upstream action.
  - `SECRETS.<name>.<KEY>` / `VARS.<name>.<key>` — credentials / config.
  - `FN.<func>(...)` — built-in functions.
- Used in action args, `run_if` conditions, and `for_each` loops.
- Missing fields resolve to `None` rather than erroring — guard with `run_if` or ternary
  fallbacks when a value may be absent.

## Cases vs tables (don't confuse them)

- A **case** is an investigation record a human works: status, severity, owner, comments,
  attachments, tasks, custom fields.
- A **table** is structured data storage: rows and columns, queried by lookup/search,
  with optional unique indexes for upsert. Use tables for assets, allowlists, indicators —
  not for investigation state.
