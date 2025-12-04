# Alembic Migration Reparent

Rebase alembic migrations to maintain a linear history when multiple heads exist.

## Objective

When multiple alembic heads are detected, do NOT create a merge revision. Instead, reparent the newer migration(s) to create a fully linear history with correct chronological ordering.

## Steps

1. **Detect multiple heads**
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic heads
   ```

2. **Identify the migration chain**
   - List the history to understand the branch structure:
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic history --verbose
   ```
   - Determine which migration is "newer" (the one that should come after the other)
   - Typically, the migration on your current branch is the one to reparent

3. **Reparent the newer migration**
   - Open the newer migration file in `alembic/versions/`
   - Update the `down_revision` to point to the leaf node of the earlier branch
   - Example: If `migration_b` and `migration_c` both have `down_revision = "migration_a"`, and `migration_c` should come after `migration_b`, change `migration_c`'s `down_revision` to `"migration_b"`

4. **Verify linear history**
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic heads
   ```
   - Should now show only ONE head

## Important Notes

- Never create merge revisions - always maintain a linear history
- The chronologically earlier migration should be the parent
- After reparenting, ensure the migration still applies cleanly
- If there are schema conflicts, resolve them in the newer migration
