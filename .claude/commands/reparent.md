# Alembic Migration Reparent

Rebase alembic migrations to maintain a linear history when multiple heads exist.

## Objective

When multiple alembic heads are detected, do NOT create a merge revision. Instead, reparent migrations to create a fully linear history with correct ordering based on merge status.

## Steps

1. **Detect multiple heads**
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic heads
   ```

2. **Identify which migration is already in main**
   - Check which migrations are already merged into main:
   ```bash
   git log main --oneline -- alembic/versions/
   ```
   - Or check if a specific migration file exists in main:
   ```bash
   git show main:alembic/versions/<migration_file>.py 2>/dev/null && echo "In main" || echo "Not in main"
   ```

3. **Determine correct ordering (CRITICAL)**
   - **Migrations already in main MUST come first** - they are the parent
   - **Migrations on your current branch (not in main) MUST come after** - they get reparented
   - **DO NOT use creation date** - a migration created earlier might still need to come AFTER one created later if the later one is already merged into main

4. **Reparent the unmerged migration**
   - Open the migration file that is NOT in main (in `alembic/versions/`)
   - Update its `down_revision` to point to the head migration that IS in main
   - Example: If `migration_b` (in main) and `migration_c` (on your branch) both have `down_revision = "migration_a"`, change `migration_c`'s `down_revision` to `"migration_b"` - regardless of which was created first

5. **Verify linear history**
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic heads
   ```
   - Should now show only ONE head

## Important Notes

- Never create merge revisions - always maintain a linear history
- **Merge status determines order, NOT creation date** - migrations in main are always the parent
- After reparenting, ensure the migration still applies cleanly
- If there are schema conflicts, resolve them in the migration that is NOT in main
