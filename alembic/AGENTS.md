# Alembic migration guidance

These rules apply to new revisions under `alembic/versions/`.

This repo currently deploys database migrations as a Helm `pre-upgrade` job that
runs `alembic upgrade head`. Kubernetes and Helm can roll application manifests
back, but they do not provide transactional database rollback. Treat database
rollback as a backup or snapshot restore unless a specific migration series was
explicitly designed and tested for downgrade.

## Release model

Prefer expand/contract migrations.

1. Expand:
   - Add new tables, columns, indexes, nullable fields, or new enum values.
   - Keep existing schema and semantics working for the old app version.
   - Ship app code that can read or write both old and new shapes when needed.
2. Migrate:
   - Backfill data in place, or copy into new structures.
   - Keep old and new representations compatible during the transition window.
   - Avoid changing the meaning of an existing column or metadata field in the
     same release that introduces the new shape.
3. Contract:
   - Drop old columns, tables, indexes, values, or constraints only after the
     new app version has been deployed and proven stable.
   - Put destructive cleanup in a later release, not in the first migration.

Do not collapse expand, data rewrite, and destructive cleanup into one revision
unless the change is trivially reversible and the old app version does not need
to run against the migrated schema.

## What is safe in an upgrade migration

Usually safe:

- Creating a new table.
- Adding a nullable column.
- Adding a column with a temporary server default for backfill.
- Creating a new index or constraint that does not invalidate old writes.
- Backfilling a new column while old code still uses the original one.
- Adding a new enum value.

Usually not safe as a one-step change:

- Rewriting a column type in place with `ALTER COLUMN ... TYPE`.
- Renaming a column, table, enum value, or semantic identifier that old code
  still expects.
- Dropping a column or table in the same release that introduces its
  replacement.
- Making a nullable column `NOT NULL` before the new write path is fully live.
- Enabling strict policies or constraints immediately after adding new support
  columns, without a compatibility window.
- Destructive data cleanup that removes information needed to restore the old
  behavior.

## Downgrade expectations

For additive migrations, implement a real `downgrade()` where it is cheap and
obvious.

For destructive or one-way migrations:

- Do not write a fake downgrade that silently leaves partial state behind.
- Raise `NotImplementedError` with a precise explanation of why downgrade is not
  safe.
- State the expected operator recovery path, usually restoring the database from
  backup or snapshot and then rolling the app back.

If a migration is intended to support rollback, the downgrade path must be
tested before relying on it operationally.

## Dynamic schemas and metadata-backed storage

Tracecat stores schema metadata in public tables and also materializes physical
tables in workspace schemas. For these migrations:

- Keep physical schema changes and metadata changes compatible across at least
  one release.
- Prefer introducing a new physical column or metadata version over rewriting an
  existing type in place.
- If a temporary rename is needed, store enough metadata to reverse it during a
  downgrade.
- Do not update metadata to point only at the new shape until the app can
  safely operate without the old one.

## Operational assumptions

- Application rollback and database rollback are separate operations.
- `kubectl rollout undo` or `helm rollback` only solve the application side.
- If a production migration is bad, the default database recovery path is
  restore-from-backup, not `alembic downgrade`.
- Before merging a destructive migration, make sure operators have a backup or
  snapshot plan for the target environment.

## Author checklist

Before opening a PR with a migration, verify:

- Can the previous app version still run after this migration?
- If not, can this be split into expand first and contract later?
- Does `downgrade()` actually restore the previous state, or should it raise an
  explicit error?
- If data is rewritten or deleted, is the restore procedure documented in the
  migration comment or PR description?
- Are metadata rows and physical schema kept compatible during rollout?

When in doubt, prefer one more release step over one more clever migration.
