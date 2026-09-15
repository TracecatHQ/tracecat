# Application database vector support

Semantic search uses PostgreSQL's `vector` extension (pgvector). The application
schema migration requires pgvector >= 0.8.0 in `public`, even while search is
disabled. The Temporal database does not need this extension.

## Docker Compose POCs: automatic setup

Use the normal `docker compose up` command. No pgvector-specific YAML file,
image build, environment setting or manual SQL command is required. All three
Compose variants use this dependency chain:

```text
postgres_db: download/cache extension files, then start PostgreSQL
    -> container started (setup waits for the actual database connection)
pgvector_setup: connect to the migration database; enable/check vector
    -> successful exit
migrations: apply application schema
    -> application services
```

`postgres_db` still defaults to the official `postgres:16` image. Its mounted
`scripts/postgres/compose-entrypoint.sh` wrapper downloads the pgvector 0.8.6
binary package for that image's Debian release and CPU architecture. APT verifies
the configured repository's signed metadata and package hashes. The wrapper
extracts only `vector.so`, `vector.control` and the extension SQL files. It does
not install package dependencies, upgrade PostgreSQL, or replace libc/ICU.
The official PostgreSQL entrypoint then initializes or opens the existing data
volume normally and runs PostgreSQL as its usual unprivileged user.

This path supports official Debian-based PostgreSQL 16 images on amd64 and
arm64 where the image's configured package repository provides pgvector 0.8.6.
It requires the image's default root entrypoint to copy extension files. It is
intended for Compose POCs, not arbitrary Alpine/custom images or managed RDS.
An already provisioned image skips the download; SQL setup still validates it.

The first installation requires access to the image's package repositories.
Downloads retry transient failures. Missing packages, failed verification or
missing binary dependencies stop the bundled database and block its migrations;
there is no fallback that silently skips vector provisioning.

A separate `pgvector-cache` volume stores the verified extension bundle. Cache
keys include the extension version, OS release, CPU architecture, PostgreSQL
version/binary and libc version. Recreating the database container with the same
base restores the files from cache without another download. Bundle hashes are
checked before reuse. Keep the cache volume for offline restarts. A different
base image or an empty/damaged cache requires a new download. The cache contains
no database rows or credentials.

`pgvector_setup` connects to the same `TRACECAT__DB_URI` as migrations, preserving
the database name and URI options such as SSL. It retries connections for up to
180 seconds. For the bundled server, it uses the existing Compose administrator
credentials to enable vector in that database, then validates access with the
migration role. This works for both existing volumes and newly initialized ones.
The application role does not need extension-installation privileges.

For an external database, setup only validates pgvector with the migration role;
an administrator must already have enabled it. Setup does not wait for the unused
bundled database to become healthy or finish downloading packages. It identifies
the connected server by its address and port, including URI host overrides.

The production database has a dedicated outbound network for package downloads;
its shared application network remains internal. The setup client also joins the
application's outbound network so it can reach external PostgreSQL servers.

The database health check uses TCP so it does not report healthy during the
image's temporary initialization server. Migrations depend on successful
completion of `pgvector_setup`; API/worker startup retains its existing migration
dependency. CI uses the same automatic setup rather than prebuilding an image
or manually installing the extension.

The optional `TRACECAT__PGVECTOR_IMAGE` setting and legacy
`docker-compose.pgvector.yml` remain compatible with already configured derived
images. Neither is required for normal Compose startup.

## Managed PostgreSQL, RDS and Kubernetes

Startup downloads apply only to Compose. RDS supplies its own extension binaries;
authorized database provisioning must enable pgvector >= 0.8.0 in `public` before
application migrations. This PR does not add a Terraform PostgreSQL-provider
resource or grant the application elevated privileges.

The checked-in Fargate RDS default is PostgreSQL 16.10. The separate Kubernetes
repository's reviewed EKS RDS default is 16.13, and its Helm chart uses external
PostgreSQL. No engine change was needed for those defaults. Check the actual
installation when versions or extension allowlists differ:

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name = 'vector';
SHOW rds.allowed_extensions;
```

The Kubernetes review covered `helm/tracecat/values.yaml`, `eks/main.tf`,
`eks/variables.tf`, and `eks/modules/eks/{rds,variables}.tf` at
[`45c2c1825b7efe5ccbb4c56d8798720e3847c5b7`](https://github.com/TracecatHQ/k8s/tree/45c2c1825b7efe5ccbb4c56d8798720e3847c5b7).
No Kubernetes code or live infrastructure is changed by this PR.

Run the following from the repository root with normal libpq connection settings
pointing at the application database. An authorized administrator provisions it:

```bash
psql -X -v install=true -f scripts/postgres/pgvector.sql
```

Then the normal application/migration role checks readiness:

```bash
psql -X -f scripts/postgres/pgvector.sql
```

On RDS, use an appropriately authorized administrator, commonly `rds_superuser`,
and check `rds.allowed_extensions`. See the
[AWS extension permissions](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Extensions.html)
and [extension version matrix](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html).
Every separate application database needs its own extension registration.

## Compatibility and rollback

Provisioning is idempotent. It does not upgrade or relocate an already installed
extension; those changes need administrator review. Read-only mode checks the
extension version/schema and exercises its vector type and cosine operator.

Before installation or validation, the SQL script rejects recorded collation
mismatches in the application database, template1 and explicit collations. This
is a guard, not an index-integrity audit or repair tool. Refreshing recorded
versions alone does not repair affected indexes.

The startup wrapper preserves the libraries in the image being started. It does
not prevent a separate pull of the floating `postgres:16` tag from changing the
underlying OS or PostgreSQL version. Back up existing data and verify compatibility
before replacing its base image; retain the existing volume. A collation mismatch
requires restoring compatible libraries or a DBA-led rebuild before continuing.

Once vector columns exist, application rollback must retain pgvector's server
files and installed extension. Keep the startup wrapper/cache or use a compatible
pre-provisioned image. Do not drop the extension with CASCADE or revert to a plain
image without the startup wrapper. Database rollback is a separate operation.

For controlled deployments that need an image prepared in advance, the optional
`scripts/postgres/build-pgvector-image.sh` and `deployments/postgres/Dockerfile`
remain available. The helper derives from the immutable image actually used by
an existing container, compiles pinned pgvector source in a separate builder and
copies only extension artifacts into the original base. It never changes the
running database. This is not needed for the default Compose POC flow.

## Validation

```bash
bash scripts/tests/test_pgvector_compose.sh
bash scripts/tests/test_pgvector_startup.sh
bash scripts/tests/test_pgvector_startup.sh postgres:16.14-bookworm fresh
```

The configuration test checks all three Compose variants and dependency ordering.
The live startup test uses the real Compose database/setup wiring with a small
SQL migration probe. It checks automatic provisioning, preserved source data and
runtime packages/binaries, cached recreation, and failure blocking migrations.
It retains uniquely named synthetic volumes and removes only its test containers.

The optional derived-image path has separate tests:

```bash
bash scripts/tests/test_pgvector.sh
bash scripts/tests/test_pgvector.sh postgres:16.14-bookworm
```
