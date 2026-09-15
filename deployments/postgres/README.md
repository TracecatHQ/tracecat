# Application database vector support

Semantic search will use the PostgreSQL `vector` extension (pgvector). This
deployment prerequisite does not enable semantic search or add its schema.
The Temporal database does not need this extension.

## Supported setup

The base Compose files default to `postgres:16` and read
`TRACECAT__PGVECTOR_IMAGE` directly. Build a derived image from the
**immutable image actually used by the deployed container**, then save its image
reference in the existing Compose project’s `.env`. Ordinary `docker compose up`
uses that image automatically; no additional Compose file or startup argument is
needed. The old `docker-compose.pgvector.yml` remains compatible but is redundant.
No particular Debian release is selected: Bookworm remains Bookworm and Trixie
remains Trixie. The build helper never pulls a floating tag or changes a running
container.

`deployments/postgres/Dockerfile` builds pgvector 0.8.6 from a pinned source
commit in a separate build stage. The final stage starts from the exact base
digest and copies only extension artifacts. Package installations occur only
in the discarded builder; PostgreSQL, libc, ICU, installed packages, entrypoint,
and configuration remain inherited from the base image. Build dependencies are
resolved from that Debian release's repositories, so builds are not guaranteed
bit-for-bit reproducible. Validate the resulting image before deployment.

This build currently supports official Debian-based PostgreSQL 16 images. It
is not a universal installer for Alpine, custom PostgreSQL builds, other major
versions, or managed databases. Build on the deployment's architecture and test
the image there; local validation does not establish production readiness.

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

For an existing Docker deployment, identify its application database container
and build without stopping it:

```bash
bash scripts/postgres/build-pgvector-image.sh \
  --container YOUR_POSTGRES_CONTAINER tracecat-postgres-pgvector:local
```

The helper reads the container's image ID, resolves its registry digest, and
checks that the digest identifies the same local image. It fails if no digest
is available; publish that exact base image to a registry first. Keep the build
output tag distinct from the base image. To use a locally available base for a
fresh database, pass `--image LOCAL_IMAGE` instead of `--container`.

After testing against a restored backup, add or update this line in the existing
Compose project’s `.env` (do not replace the rest of that file):

```dotenv
TRACECAT__PGVECTOR_IMAGE=tracecat-postgres-pgvector:local
```

Use the tested registry digest instead for remote deployment. Keep this setting
in the same environment file used by the deployment, and remove any stale shell
export that would override it. Then use the same Compose project and normal
command, such as `docker compose up -d postgres_db`, to recreate the database.
Subsequent `docker compose up` commands need no extra flags. For a worktree
managed by `just cluster`, which also loads the repository `.env`:

```bash
just cluster 2 up -d --no-seed --skip-dependency-sync postgres_db
just cluster 2 exec -T postgres_db \
  psql -X -U postgres -d postgres -v install=true < scripts/postgres/pgvector.sql
```

Replace `2` with the existing cluster number and use its configured username.
Keep the image setting in `.env` on subsequent starts once vector columns exist;
unsetting it selects the plain PostgreSQL default. For remote deployment, publish
the tested derived image to your registry and select its digest. A database
container restart is still required, even though existing libraries are preserved.

CI derives its image from the freshly pulled PostgreSQL base, then performs
explicit provisioning before tests or application migration
containers start. Ordinary API startup never attempts privileged installation.
Fresh databases created separately by a test or operator need their own
extension provisioning; it is not inherited from another application database.

## Existing installations and rollback

1. Take and verify a backup/snapshot. Record the current PostgreSQL and
   extension versions, OS release, libc/ICU versions, image digest, volume and
   connection settings. Rehearse against a restored backup before changing the
   live database. Build from the container image as described above; do not substitute a
   current floating tag for its deployed digest.
2. For Compose, stop the application writers and database cleanly during a
   maintenance window. Save the compatible image selection in `.env` and
   recreate only `postgres_db`
   against its existing volume. Do not remove volumes or initialize a new
   empty data directory. This does not upgrade PostgreSQL or its runtime libraries. If the check
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
bash scripts/tests/test_pgvector_compose.sh
bash scripts/tests/test_pgvector.sh
bash scripts/tests/test_pgvector.sh postgres:16.14-bookworm
```

Each invocation builds from its source image's immutable digest, compares the
PostgreSQL binary, libc/ICU checksums and installed package versions, and tests
an existing synthetic volume. It verifies text data retention, extension
provisioning, unprivileged read-only checks, vector writes/ranking, and creation
of a separate database. Run both Trixie and Bookworm cases: each must retain its
own libraries. `PGVECTOR_TEST_IMAGE` can select a previously built image for
validation against a matching source. With an intentionally incompatible image,
pass `--expect-collation-mismatch` as the second argument to exercise the guard.
Containers are removed after the test; uniquely named synthetic volumes are
retained for inspection. Existing clusters and volumes are not modified.
