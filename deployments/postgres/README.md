# Application database vector support

Semantic search will use the PostgreSQL `vector` extension (pgvector). This
deployment prerequisite does not enable semantic search or add its schema.
The Temporal database does not need this extension.

## Supported setup

The three base Compose configurations keep their existing `postgres:16` image.
To use pgvector, explicitly layer `docker-compose.pgvector.yml` after the base
Compose file. CI uses this override as well. It pins
`pgvector/pgvector:0.8.6-pg16-trixie` by its multi-platform image digest, supports
amd64 and arm64, and preserves the existing data volume mapping.

The Trixie variant matches the distribution used by the previous floating
`postgres:16` default. The shorter `pgvector/pgvector:pg16` tag currently points
to Bookworm, whose different libc/ICU libraries can invalidate existing text
ordering and prevent database creation. PostgreSQL major-version compatibility
alone is insufficient. Older volumes initialized on Bookworm also need a
matching image or a deliberate collation migration; do not blindly apply this
override to them.

For managed PostgreSQL, the baseline is **pgvector 0.8.0 or newer**, installed in
the `public` schema of the **application database**. The server package/image
and the database extension are separate: changing an image makes the extension
available, but does not install it in an existing database.

Fargate's checked-in RDS default is PostgreSQL 16.10. The separate Kubernetes
repository uses external PostgreSQL; its EKS application RDS default is 16.13.
Both versions have pgvector support in the
[AWS extension matrix](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html).
No Terraform engine change is needed for these defaults. Check the actual
server when an installation overrides them:

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name = 'vector';
```

The Kubernetes review covered `helm/tracecat/values.yaml`, `eks/main.tf`,
`eks/variables.tf`, and `eks/modules/eks/{rds,variables}.tf` at
[`45c2c1825b7efe5ccbb4c56d8798720e3847c5b7`](https://github.com/TracecatHQ/k8s/tree/45c2c1825b7efe5ccbb4c56d8798720e3847c5b7).
The Helm chart has no bundled application PostgreSQL image to replace. External
database provisioning is still required before its migration job runs; this
review does not assert that a live database already has the extension enabled.

## Provision once, then check without elevated permissions

Run commands from the repository root. Use your normal libpq connection
configuration (`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, TLS settings and a
password file), targeting the same database as the application. Do not put
credentials in command history.

An authorized database administrator installs the extension explicitly:

```bash
psql -X -v install=true -f scripts/postgres/pgvector.sql
```

On self-managed PostgreSQL this normally needs a database superuser. On RDS,
use an appropriately authorized administrator, commonly `rds_superuser`, and
check any `rds.allowed_extensions` restriction. Do not grant these privileges
to the normal application role. See
[AWS extension permissions](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Extensions.html).

Then connect with the normal application/migration role and check readiness:

```bash
psql -X -f scripts/postgres/pgvector.sql
```

Before installation or validation, the script rejects recorded collation
version mismatches in the application database, template1, and explicit
collations. This is a guard, not an index-integrity audit or a repair tool.
Without `install=true`, it checks the installed extension version/schema and
exercises the vector type and cosine operator. It works in a read-only
transaction. It fails clearly when the extension is missing, too old or in an
unexpected schema. Installation mode is idempotent but never upgrades or
relocates an existing extension; those changes require administrator review.

For a fresh local worktree database (or after reviewing the upgrade below),
include the override in every cluster command and use the printed cluster number:

```bash
just cluster --compose-override docker-compose.pgvector.yml up -d --no-seed --skip-dependency-sync postgres_db
just cluster 2 --compose-override docker-compose.pgvector.yml exec -T postgres_db \
  psql -X -U postgres -d postgres -v install=true < scripts/postgres/pgvector.sql
```

Replace `2` with your cluster number and use the configured database username
if it is not `postgres`. The same command without `-v install=true` checks
readiness. For a direct Compose deployment, use `docker compose exec -T` with
the corresponding Compose file/project and `-f docker-compose.pgvector.yml`
override instead. Keep this override on subsequent starts once vector columns
exist; reverting to the base image would remove the required server library.

CI performs explicit provisioning before tests or application migration
containers start. Ordinary API startup never attempts privileged installation.
Fresh databases created separately by a test or operator need their own
extension provisioning; it is not inherited from another application database.

## Existing installations and rollback

1. Take and verify a backup/snapshot. Record the current PostgreSQL and
   extension versions, OS release, libc/ICU versions, image digest, volume and
   connection settings. Rehearse against a restored backup before changing the
   live database. Choose an image matching its existing library family.
2. For Compose, stop the application writers and database cleanly during a
   maintenance window. Explicitly include the compatible image override and
   recreate only `postgres_db`
   against its existing volume. Do not remove volumes or initialize a new
   empty data directory. This is a PostgreSQL 16 minor update, not a major
   version migration. If the current server is newer than 16.15, select a
   compatible newer image instead of downgrading it to this pin. If the check
   reports a collation mismatch, keep writers stopped and restore compatible
   libraries or have a DBA rebuild all affected objects (including indexes),
   then refresh the recorded collation versions. Refreshing versions alone
   does not repair indexes. See the
   [PostgreSQL collation guidance](https://www.postgresql.org/docs/16/sql-altercollation.html).
3. Provision `vector` in the application database, then run the check with the
   migration role before running any vector-dependent migrations. The storage
   PR must use this same prerequisite and provide an actionable migration
   error rather than assume permission to install extensions.
4. Deploy compatible row writers and indexing workers before enabling the
   future semantic-search feature and its backfill. This PR has no search
   feature switch to enable.

An application rollback can retain the pgvector-enabled database image and
extension. Once vector columns exist, a plain PostgreSQL image lacks the
extension library needed to read them. Do not drop the extension with CASCADE
or switch back to a plain image as an application rollback procedure. Database
rollback is separate and may require restoring a backup; an image change alone
does not reverse database changes.

## Reproduce the image upgrade check

```bash
bash scripts/tests/test_pgvector.sh
bash scripts/tests/test_pgvector.sh postgres:16.14-bookworm --expect-collation-mismatch
```

The default test writes synthetic data and a text index using PostgreSQL 16.14
on Trixie (the previous default image distribution), stops it,
and reopens the same volume using the pinned image. It verifies data retention,
missing-extension errors, idempotent provisioning, unprivileged read-only
checks, table/vector writes and cosine ranking. It also checks that enabling
the extension in one database does not enable it in a second database.
The second invocation exercises an incompatible Bookworm volume and verifies
that both installation and read-only validation reject the mismatch before
any extension is created.
Containers are removed after the test; its uniquely named synthetic volume is
retained for inspection. Existing clusters and volumes are not modified.
