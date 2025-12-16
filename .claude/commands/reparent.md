# Alembic Migration Reparent

Rebase alembic migrations to maintain a linear history when multiple heads exist.

## Objective

When multiple alembic heads are detected, do NOT create a merge revision. Instead, reparent migrations to create a fully linear history with correct ordering based on merge status. You must find the Lowest Common Ancestor (LCA) of the heads, then reparent the base of the unmerged branch onto the leaf of the merged branch.

## Steps

1. **Detect multiple heads**
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic heads
   ```

2. **Find the Lowest Common Ancestor (LCA)**
   - View the full migration history to understand the branch structure:
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic history
   ```
   - For each head, trace back through the `down_revision` chain until you find a revision that appears in both chains
   - You can inspect a migration's `down_revision` by reading the file:
   ```bash
   grep "down_revision" alembic/versions/<revision_id>*.py
   ```
   - The LCA is the first common ancestor where the branches diverge

3. **Identify which branch is already in main**
   - For each head, check if it exists in main:
   ```bash
   git show main:alembic/versions/<migration_file>.py 2>/dev/null && echo "In main" || echo "Not in main"
   ```
   - Walk back from each head toward the LCA, checking each migration
   - The branch containing migrations already merged into main is the "merged branch"
   - The branch containing migrations NOT in main is the "unmerged branch"
   - Alternatively, list all migrations in main:
   ```bash
   git log main --oneline -- alembic/versions/
   ```

4. **Determine the reparenting points**
   - **Merged branch leaf**: The head of the branch that is already in main
   - **Unmerged branch base**: The first migration in the unmerged branch (the one whose `down_revision` points to the LCA)

   Example with deeper branches:
   ```
   Before:                          After:
       LCA                             LCA
      /   \                             |
     A     X                            A
     |     |                            |
     B     Y  (heads)                   B (merged branch leaf)
                                        |
                                        X
                                        |
                                        Y  (single head)
   ```
   - If `A -> B` is in main and `X -> Y` is on your branch
   - Find the unmerged branch base: `X` (its `down_revision` points to LCA)
   - Reparent `X` to point to the merged branch leaf: `B`

5. **Reparent the unmerged branch base**
   - Open the first migration in the unmerged branch (in `alembic/versions/`)
   - Update its `down_revision` from the LCA to the merged branch's leaf (head)
   - Only ONE migration needs to be modified - the base of the unmerged branch

6. **Verify linear history**
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic heads
   ```
   - Should now show only ONE head
   - Optionally verify the full chain:
   ```bash
   TRACECAT__DB_URI=postgresql+psycopg://postgres:postgres@localhost:5432/postgres uv run alembic history
   ```

## Important Notes

- Never create merge revisions - always maintain a linear history
- **Merge status determines order, NOT creation date** - migrations in main are always the parent branch
- Only modify ONE migration file: the base of the unmerged branch
- After reparenting, ensure the migrations still apply cleanly in sequence
- If there are schema conflicts, resolve them in the migrations that are NOT in main
