# Application database vector support

Semantic search will use the PostgreSQL `vector` extension (pgvector). This
deployment prerequisite does not enable semantic search or add its schema.
The Temporal database does not need this extension.

## Supported setup

The three root Compose configurations pin
`pgvector/pgvector:0.8.6-pg16-bookworm` by its multi-platform image digest.
That image contains PostgreSQL 16.15 and pgvector 0.8.6, supports amd64 and arm64,
and retains `/var/lib/postgresql/data` and the existing `core-db` volume mapping.
Python and full-stack CI use these same Compose images.

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

Without `install=true`, the script only checks the installed version/schema and
exercises the vector type and cosine operator. It works in a read-only
transaction. It fails clearly when the extension is missing, too old or in an
unexpected schema. Installation mode is idempotent but never upgrades or
relocates an existing extension; those changes require administrator review.

For a local worktree database, after starting it with `just cluster`, use the
cluster number printed by that command:

```bash
just cluster up -d --no-seed --skip-dependency-sync postgres_db
just cluster 2 exec -T postgres_db \
  psql -X -U postgres -d postgres -v install=true < scripts/postgres/pgvector.sql
```

Replace `2` with your cluster number and use the configured database username
if it is not `postgres`. The same command without `-v install=true` checks
readiness. For a direct Compose deployment, use `docker compose exec -T` with
the corresponding Compose file/project instead.

CI performs explicit provisioning before tests or application migration
containers start. Ordinary API startup never attempts privileged installation.
Fresh databases created separately by a test or operator need their own
extension provisioning; it is not inherited from another application database.

## Existing installations and rollback

1. Take and verify a backup/snapshot. Record the current PostgreSQL and
   extension versions, image digest, volume and connection settings.
2. For Compose, stop the application writers and database cleanly during a
   maintenance window. Pull the pinned image and recreate only `postgres_db`
   against its existing volume. Do not remove volumes or initialize a new
   empty data directory. This is a PostgreSQL 16 minor update, not a major
   version migration. If the current server is newer than 16.15, select a
   compatible newer image instead of downgrading it to this pin.
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
```

This isolated test writes synthetic data using plain PostgreSQL 16.14, stops it,
and reopens the same volume using the pinned image. It verifies data retention,
missing-extension errors, idempotent provisioning, unprivileged read-only
checks, table/vector writes and cosine ranking. It also checks that enabling
the extension in one database does not enable it in a second database.
Containers are removed after the test; its uniquely named synthetic volume is
retained for inspection. Existing clusters and volumes are not modified.
