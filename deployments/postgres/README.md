# Application database vector support

Semantic search uses PostgreSQL's `vector` extension (pgvector). The application
schema migration requires pgvector >= 0.8.0 in `public`, even while search is
disabled. The Temporal database does not need this extension.

## Docker Compose POCs: automatic setup

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

Automatic provisioning covers only the bundled Compose database. An external
database or a different database named in `TRACECAT__DB_URI` must be provisioned
separately with the administrator SQL command below. Compose retains its existing
bundled-database dependency; this change does not add external-database routing.

### Image publication

`.github/workflows/build-postgres-image.yml` tests both OS variants on native
amd64 and arm64 runners. PR runs have read-only permissions and do not publish.
After a change reaches `main`, a separate job with package-write permission
publishes the exact tested image artifacts and combines their CPU architectures
into versioned GHCR tags. Application CI builds its own candidate locally so it
can test changes before their tag exists in GHCR.

Bump the image revision (`r1`, `r2`, ...) in `images.json` when changing packaged
files or base digests; update all three Compose defaults together. These images
have their own version, independent of the API and UI release versions.

**First release prerequisite:** publish both image tags and make the GHCR package
public before distributing Compose files that reference them. Verify anonymous
pulls for amd64 and arm64. Adding the workflow does not itself create a publicly
pullable image before its first successful trusted `main` run.

## Managed PostgreSQL, RDS and Kubernetes

The prebuilt image applies only to Compose. RDS supplies its own extension binaries;
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
bash scripts/tests/test_pgvector_compose.sh
bash scripts/tests/test_pgvector_startup.sh trixie existing
bash scripts/tests/test_pgvector_startup.sh bookworm fresh synthetic_admin
bash scripts/tests/test_pgvector.sh
bash scripts/tests/test_pgvector.sh postgres:16.14-bookworm
```

The configuration test checks all three Compose defaults and dependency ordering.
The startup test builds the candidate and uses the production database wiring
with a small SQL migration probe on internal-only networks. It checks fresh and
existing volumes, source data and runtime preservation, custom administrators,
offline recreation, the TCP readiness gate, failure shutdown, and recovery.
Synthetic volumes are retained; only test containers are removed.

The SQL test deliberately bypasses the automatic wrapper on an existing volume to
verify administrator installation, read-only validation, permissions, and vector
operations independently. CI supplies candidates built from the pinned digests.
