# Lookup index rollout

Deploy the unique lookup index discovery fix to every application instance
before provisioning full-text, trigram, ordering, or other auxiliary table
indexes. Older versions can mistake these indexes for unique lookup columns,
which breaks upserts and column metadata. The supported rollback version must
also contain this fix.

Lookup metadata and upserts support one immediate, valid, ready, live unique
index over one plain column. Partial, expression, composite, covering, primary,
and deferrable indexes are excluded from lookup metadata. They are left intact
when a lookup index is removed. A deferrable unique constraint on the lookup
column also prevents upserts: PostgreSQL can infer it alongside the immediate
index and rejects it as an `ON CONFLICT` arbiter.

This change does not provision indexes or install PostgreSQL extensions.
