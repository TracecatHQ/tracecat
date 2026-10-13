# Application database search extensions

Semantic search uses PostgreSQL's `vector` extension (pgvector). The application
schema migration requires pgvector >= 0.8.0 in `public`, even while search is
disabled. Indexed text search additionally requires `pg_trgm` in `public`; see
the [trigram prerequisite](#trigram-search-prerequisite) below. The Temporal
database does not need either extension.

## Docker Compose POCs: automatic pgvector setup

Run `docker compose up` as usual. All three Compose files default to the versioned
`ghcr.io/tracecathq/tracecat-postgres` image. No extra Compose file, local build,
startup download, or manual SQL command is needed.

```text
GitHub Actions: build pinned PostgreSQL + pgvector + startup files
    -> test native amd64 and arm64 images
    -> publish the tested images and their multi-platform tag to GHCR

docker compose up: pull the prebuilt image
    -> temporary socket-only server: enable and validate pgvector
    -> normal PostgreSQL server: TCP health check passes
    -> migrations: apply application schema
    -> application services
```

The image is the deployment unit: its extension binary, SQL, and entrypoint travel
together. Startup needs only the image and the existing data volume. It does not
contact a package repository, write a download cache, or mount repository files.
The production database stays on its internal network.

`deployments/postgres/images.json` records the pinned PostgreSQL base digests and
published tags. The default is PostgreSQL 16.14 on Debian Trixie with pgvector
0.8.6. A matching Bookworm variant is also published. The Dockerfile builds pinned
pgvector source in a separate stage and copies only extension artifacts into the
original base, then adds the startup files. Build tools and upgraded packages in
the builder never become part of the runtime image.

| Data volume | Provisioning path |
| --- | --- |
| Fresh | The official entrypoint runs the packaged `initdb-pgvector.sql` hook on its temporary socket-only server. |
| Existing | The wrapper starts a temporary socket-only server, runs the same SQL, and stops it before starting the normal server. |

Both paths use the local administrator connection and validate the extension
schema, catalog version, and collation versions. Compose sets `POSTGRES_DB` to
`postgres`, including when the administrator username is customized. Failed
checks prevent TCP readiness and block migrations. The existing-volume wrapper
also stops its temporary server on failure. Each existing-volume boot adds a
short server start/stop cycle.

The Compose startup hook provisions only the bundled database. For an external
application database, the schema migration enables pgvector if its role has
permission; otherwise an administrator must enable it first. Compose retains its
existing bundled-database dependency; this change does not add external-database
routing. See the [pgvector setup guide](https://docs.tracecat.com/self-hosting/pgvector)
for server installation and migration troubleshooting.

### Image publication

`.github/workflows/build-postgres-image.yml` tests both OS variants on native
amd64 and arm64 runners. PR runs have read-only permissions and do not publish.
When image inputs change on `main`, a separate job with package-write permission
publishes the exact tested image artifacts and combines their CPU architectures
into versioned GHCR tags. Application CI builds its own candidate locally so it
can test changes before their tag exists in GHCR.

Publication refuses an existing tag, and registry authentication or network
errors stop publication. Only a confirmed missing manifest permits a new tag.
Documentation, tests, Compose wiring, and publisher-only changes are tested in
PRs but do not trigger publication on `main`. They do not need a new image
revision. Main-branch publication runs are serialized.

Tags use a simple image revision: `:1` for the default and `:1-bookworm` for
Bookworm. PostgreSQL, OS, and pgvector versions are recorded in the build files.
Bump the revision (`1`, `2`, ...) in `images.json` when changing packaged
files, base digests, or build inputs; update all three Compose defaults together. These images
have their own version, independent of the API and UI release versions.

**First release prerequisite:** publish both image tags and make the GHCR package
public before distributing Compose files that reference them. Verify anonymous
pulls for amd64 and arm64. Adding the workflow does not itself create a publicly
pullable image before its first successful trusted `main` run.

## Managed PostgreSQL, RDS and Kubernetes

The prebuilt image applies only to Compose. RDS supplies its own extension binaries.
The schema migration runs `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public`
and requires pgvector >= 0.8.0. If the migration role cannot create extensions, an
authorized administrator must enable it before migration. This PR does not add a
Terraform PostgreSQL-provider resource or grant the application elevated privileges.

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

## Trigram search prerequisite

PostgreSQL provides full-text search directly. Literal fragment matching uses
the [supplied `pg_trgm` extension](https://www.postgresql.org/docs/16/pgtrgm.html)
and its `public.gin_trgm_ops` operator class for `text`. This prerequisite is
shared by case and table search. Provision it once per application database,
before running search-dependent migrations, for both fresh and existing
installations. Keep the pgvector setup and version requirement above.

| Deployment | Application database and provisioning |
| --- | --- |
| Docker Compose | Use the bundled `postgres_db` service, whose PostgreSQL 16.14 image includes `pg_trgm`. The pgvector startup hook does not provision `pg_trgm`; run the explicit command below after PostgreSQL is ready. |
| Fargate / RDS | Use the `core_database` RDS endpoint. The checked-in PostgreSQL 16.10 default supports `pg_trgm`; review the actual engine inventory and `rds.allowed_extensions` before installing. |
| Helm / Kubernetes | Use the external application PostgreSQL connection configured for Tracecat. Provision with an authorized database role before running the Helm migration job; the reviewed EKS PostgreSQL 16.13 default supports `pg_trgm`. |

The [RDS extension version matrix](https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html)
lists `pg_trgm` for these PostgreSQL 16 versions. No custom image, server restart,
or application extension-installation privilege is required. On another
self-hosted distribution, install its PostgreSQL contrib package if `pg_trgm`
is absent. For overridden engine versions, check the installed server rather
than assuming its extension availability matches the defaults.

From the repository root, a Compose administrator can provision the bundled
application database (adjust the username if `POSTGRES_USER` is customized):

```bash
docker compose exec -T postgres_db psql -X -U postgres -d postgres -v install=true < scripts/postgres/pg-trgm.sql
```

For an external application database, use normal libpq connection settings
(`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and authentication). Confirm the host
and database are the **application** connection, not the separate Temporal
connection, before installation:

```sql
SELECT current_database(), inet_server_addr(), version();
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name IN ('vector', 'pg_trgm');
```

An authorized provisioning role then installs and validates `pg_trgm`:

```bash
psql -X -v install=true -f scripts/postgres/pg-trgm.sql
```

Switch the connection settings to the normal application/migration role and
check readiness:

```bash
psql -X -f scripts/postgres/pg-trgm.sql
```

The default check opens a read-only transaction. It verifies the extension
schema, extension ownership of the GIN operator class and similarity function,
schema `USAGE`, function `EXECUTE`, and function usability. Success reports the
database, server version, extension version, and an identical-text similarity
of `1`. Missing server support, extension registration, schema placement, or
permissions fails with a PostgreSQL SQLSTATE and an administrator action hint.
Use `-v VERBOSITY=verbose` to include SQLSTATE in psql error output.

`pg_trgm` is a trusted extension on PostgreSQL 16: an authorized provisioning
role can install it with `CREATE` on the database and target schema. On RDS,
also check `SHOW rds.allowed_extensions`; an authorized administrator must allow
`pg_trgm` when an allowlist is configured. See
[RDS trusted extensions and restrictions](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.FeatureSupport.Extensions.html).
The application, Fargate init container, and Helm migration job must not assume
superuser or extension-installation privileges.

Installation is transactional and idempotent. It does not upgrade, relocate,
or drop an existing extension, and it does not change pgvector or source data.
An extension already installed outside `public` requires administrator review.
Retain `pg_trgm` when rolling back application code while indexes depend on it.
Case and table index migrations consume this shared prerequisite; they must not
add another extension bootstrap. Helm manifest changes belong in the separate
Kubernetes repository.

## Pgvector compatibility and rollback

Provisioning is idempotent. It does not upgrade or relocate an already installed
extension; those changes need administrator review. Read-only mode checks the
extension version/schema and exercises its vector type and cosine operator.

Before installation or validation, the SQL script rejects recorded collation
mismatches in the application database, template1 and explicit collations. This
is a guard, not an index-integrity audit or repair tool. Refreshing recorded
versions alone does not repair affected indexes.

A prebuilt image cannot infer the OS libraries that created an existing data
volume. The Trixie image preserves the pinned Trixie base's runtime; it does not
preserve a Bookworm installation's runtime. Before upgrading an existing POC,
identify its base image and use the matching OS variant. The optional
`TRACECAT__PGVECTOR_IMAGE` setting selects the published Bookworm image when
needed. The default needs no additional configuration for a fresh installation
or a compatible Trixie volume.

For an installation that needs its exact current PostgreSQL and library builds,
the helper below derives an image from the immutable base used by its container.
It does not mutate the running database:

```bash
bash scripts/postgres/build-pgvector-image.sh --container postgres_db local-postgres-pgvector:validated
```

Validate that image before selecting it with `TRACECAT__PGVECTOR_IMAGE`. Back up
existing data and retain the volume. A collation mismatch requires restoring
compatible libraries or a DBA-led index rebuild before continuing. The startup
checks refuse the mismatch; they never refresh versions to hide it.

Once vector columns exist, application rollback must retain the extension's
server files and database registration. Do not drop it with CASCADE or revert to
a plain PostgreSQL image. Database rollback is a separate operation.

## Validation

```bash
PGTRGM_TEST_CONTAINER=synthetic-postgres bash scripts/tests/test_pg_trgm.sh
bash scripts/tests/test_pgvector_compose.sh
bash scripts/tests/test_pgvector_startup.sh trixie existing
bash scripts/tests/test_pgvector_startup.sh bookworm fresh synthetic_admin
bash scripts/tests/test_pgvector.sh
bash scripts/tests/test_pgvector.sh postgres:16.14-bookworm
```

The trigram test uses an explicitly selected disposable PostgreSQL container
with pgvector available and local psql administrator access. It creates and
removes only its uniquely named synthetic databases and role; it never starts
services or removes volumes. Its three scenarios cover fresh provisioning and
GIN use, existing data/pgvector preservation and read-only role permissions,
and incorrect schema or unrelated same-named objects. Do not point it at a
production server or a database used by another test suite.

The configuration test checks all three Compose defaults and dependency ordering.
The startup test builds the candidate and uses the production database wiring
with a small SQL migration probe on internal-only networks. It checks fresh and
existing volumes, source data and runtime preservation, custom administrators,
offline recreation, the TCP readiness gate, failure shutdown, and recovery.
Synthetic volumes are retained; only test containers are removed.

The SQL test deliberately bypasses the automatic wrapper on an existing volume to
verify administrator installation, read-only validation, permissions, and vector
operations independently. CI supplies candidates built from the pinned digests.
