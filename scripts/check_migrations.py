#!/usr/bin/env python3
"""Check Alembic topology without running env.py or connecting to a database."""

import argparse
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError

# This merge reconciles the heads that predate the linear-history policy.
# Its ancestors can contain branches, merges and cross-branch dependencies.
LINEAR_HISTORY_BASE = "2f14222e0d12"

# The 1.1.0-alpha.2.1 release commit re-parented 9680c861644a onto
# bc3124ad3437 on its release branch only. Main kept both as siblings of
# a7c3e9f1b2d4, so databases upgraded with alpha.2.1 failed later upgrades with
# column "email_claimed_at" already exists. These one-time rewrites make main
# match the shipped graph. They become inert once the rewrite is on every base
# and may then be deleted.
AUDITED_REWRITES: Mapping[str, tuple[tuple[bytes, bytes], ...]] = {
    # #3941 (c4f1a9d2e7b3) and #3961 (b99074fdcf3a) both revised c4e8a2f6d9b1
    # and merged without a rebase. WebAuthn MFA runs after the session command
    # table.
    "c4f1a9d2e7b3": (
        (b"Revises: c4e8a2f6d9b1\n", b"Revises: b99074fdcf3a\n"),
        (
            b'down_revision: str | None = "c4e8a2f6d9b1"\n',
            b'down_revision: str | None = "b99074fdcf3a"\n',
        ),
    ),
    # #3736 (d9107f689ccf) and #3662 (e379e38b8495) both revised d45011587673
    # and merged without a rebase. Sub-cases runs after skill library installs.
    "d9107f689ccf": (
        (b"Revises: d45011587673\n", b"Revises: e379e38b8495\n"),
        (
            b'down_revision: str | None = "d45011587673"\n',
            b'down_revision: str | None = "e379e38b8495"\n',
        ),
    ),
    "9680c861644a": (
        (b"Revises: a7c3e9f1b2d4\n", b"Revises: bc3124ad3437\n"),
        (
            b'down_revision = "a7c3e9f1b2d4"\n',
            b'down_revision = "bc3124ad3437"\n',
        ),
        (
            (
                b"extension if needed; its role needs permission to do so. Downgrade removes only\n"
            ),
            (
                b"extension if needed; its role needs permission to do so. An existing extension\n"
                b"stays in its schema, which must be on the search_path. A new one goes in public,\n"
                b"or in ibm_extension on IBM Cloud Databases for PostgreSQL, which only allows\n"
                b"extensions in that read-only, provider-managed schema. Downgrade removes only\n"
            ),
        ),
        (
            (
                b"        DO $$ BEGIN\n"
                b"            CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;\n"
            ),
            (
                b"        DO $$ BEGIN\n"
                b"            IF EXISTS (SELECT FROM pg_extension WHERE extname = 'vector') THEN\n"
                b"                NULL;\n"
                b"            ELSIF to_regnamespace('ibm_extension') IS NOT NULL THEN\n"
                b"                -- IBM's documented form; IBM places it in ibm_extension.\n"
                b"                CREATE EXTENSION IF NOT EXISTS vector;\n"
                b"            ELSE\n"
                b"                CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;\n"
                b"            END IF;\n"
            ),
        ),
        (
            (
                b"                WHERE e.extname = 'vector' AND n.nspname = 'public'\n"
                b"                  AND string_to_array(e.extversion, '.')::int[] >= ARRAY[0,8,0]\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION 'Tracecat requires pgvector >= 0.8.0 in the public schema'\n"
            ),
            (
                b"                WHERE e.extname = 'vector' AND n.nspname = ANY(current_schemas(false))\n"
                b"                  AND string_to_array(e.extversion, '.')::int[] >= ARRAY[0,8,0]\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION 'Tracecat requires pgvector >= 0.8.0 in a schema on the search_path'\n"
            ),
        ),
    ),
    "b4e8f2a6c1d9": (
        (
            b"Revises: 9680c861644a, bc3124ad3437\n",
            b"Revises: 9680c861644a\n",
        ),
        (
            b'down_revision: tuple[str, str] | None = ("9680c861644a", "bc3124ad3437")\n',
            b'down_revision: str | None = "9680c861644a"\n',
        ),
    ),
    "8c0e18190001": (
        (
            b"Revises: 391f391da70b, bc3124ad3437\n",
            b"Revises: 391f391da70b\n",
        ),
        (
            b'down_revision = ("391f391da70b", "bc3124ad3437")\n',
            b'down_revision = "391f391da70b"\n',
        ),
    ),
    # IBM Cloud Databases for PostgreSQL only allows extensions in its
    # read-only ibm_extension schema. These rewrites install pg_trgm there when
    # that schema exists (the 9680c861644a pairs above do the same for
    # pgvector), keep an existing extension in its schema, and look up
    # pg_trgm's schema instead of assuming public.
    "3e54217aadda": (
        (
            (
                b"preinstalled by an administrator. Downgrade retains this shared dependency.\n"
            ),
            (
                b"preinstalled by an administrator. An existing extension stays in its schema. A\n"
                b"new one goes in public, or in ibm_extension on IBM Cloud Databases for\n"
                b"PostgreSQL, which only allows extensions in that read-only, provider-managed\n"
                b"schema. Downgrade retains this shared dependency.\n"
            ),
        ),
        (
            (
                b"            CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;\n"
                b"        EXCEPTION WHEN insufficient_privilege THEN\n"
                b"            RAISE EXCEPTION 'Cannot install pg_trgm with the current role'\n"
                b"                USING ERRCODE = 'insufficient_privilege', DETAIL = SQLERRM,\n"
                b"                      HINT = 'Ask an authorized administrator to preinstall pg_trgm in public, then rerun migrations. On RDS, also check rds.allowed_extensions. The migration does not grant privileges.';\n"
            ),
            (
                b"            IF EXISTS (SELECT FROM pg_catalog.pg_extension WHERE extname = 'pg_trgm') THEN\n"
                b"                NULL;\n"
                b"            ELSIF pg_catalog.to_regnamespace('ibm_extension') IS NOT NULL THEN\n"
                b"                -- IBM's documented form; IBM places it in ibm_extension.\n"
                b"                CREATE EXTENSION IF NOT EXISTS pg_trgm;\n"
                b"            ELSE\n"
                b"                CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;\n"
                b"            END IF;\n"
                b"        EXCEPTION WHEN insufficient_privilege THEN\n"
                b"            RAISE EXCEPTION 'Cannot install pg_trgm with the current role'\n"
                b"                USING ERRCODE = 'insufficient_privilege', DETAIL = SQLERRM,\n"
                b"                      HINT = 'Ask an authorized administrator to preinstall pg_trgm in public (ibm_extension on IBM Cloud), then rerun migrations. On RDS, also check rds.allowed_extensions. The migration does not grant privileges.';\n"
            ),
        ),
        (
            (
                b"            IF extension_schema <> 'public' THEN\n"
                b"                RAISE EXCEPTION 'pg_trgm must be installed in the public schema'\n"
                b"                    USING ERRCODE = 'object_not_in_prerequisite_state',\n"
                b"                          HINT = 'Ask the database administrator to review the existing extension schema. This migration does not relocate, upgrade or drop extensions.';\n"
                b"            END IF;\n"
                b"            IF NOT pg_catalog.has_schema_privilege('public', 'USAGE') THEN\n"
                b"                RAISE EXCEPTION 'The current role needs USAGE on the public schema'\n"
                b"                    USING ERRCODE = 'insufficient_privilege',\n"
                b"                          HINT = 'Ask the database administrator to grant USAGE on schema public to the application and migration roles.';\n"
            ),
            (
                b"            IF NOT pg_catalog.has_schema_privilege(extension_schema, 'USAGE') THEN\n"
                b"                RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',\n"
                b"                    MESSAGE = 'The current role needs USAGE on the ' || extension_schema || ' schema',\n"
                b"                    HINT = 'Ask the database administrator to grant USAGE on schema ' || quote_ident(extension_schema) || ' to the application and migration roles.';\n"
            ),
        ),
        (
            (
                b"                  AND n.nspname = 'public' AND c.opcintype = 'text'::regtype\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION 'pg_trgm must provide public.gin_trgm_ops for text'\n"
                b"                    USING ERRCODE = 'object_not_in_prerequisite_state',\n"
                b"                          HINT = 'Ask the database administrator to repair the extension-owned GIN operator class. A same-named unrelated operator class is not supported.';\n"
                b"            END IF;\n"
                b"\n"
                b"            similarity_oid := pg_catalog.to_regprocedure('public.similarity(text,text)');\n"
                b"            IF similarity_oid IS NULL OR NOT EXISTS (\n"
                b"                SELECT FROM pg_catalog.pg_depend\n"
                b"                WHERE classid = 'pg_proc'::regclass AND objid = similarity_oid\n"
                b"                  AND deptype = 'e' AND refclassid = 'pg_extension'::regclass\n"
                b"                  AND refobjid = extension_oid\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION 'pg_trgm must provide public.similarity(text,text)'\n"
                b"                    USING ERRCODE = 'object_not_in_prerequisite_state',\n"
                b"                          HINT = 'Ask the database administrator to repair the extension-owned similarity function. A same-named unrelated function is not supported.';\n"
                b"            END IF;\n"
                b"            IF NOT pg_catalog.has_function_privilege(similarity_oid, 'EXECUTE') THEN\n"
                b"                RAISE EXCEPTION 'The current role needs EXECUTE on public.similarity(text,text)'\n"
                b"                    USING ERRCODE = 'insufficient_privilege',\n"
                b"                          HINT = 'Ask the database administrator to grant EXECUTE on the pg_trgm functions used by search to the application and migration roles.';\n"
                b"            END IF;\n"
                b"        END\n"
                b"        $$;\n"
                b'    """)\n'
                b"\n"
                b'    op.execute("""\n'
                b"        SELECT public.similarity('synthetic search'::text, 'synthetic search'::text);\n"
            ),
            (
                b"                  AND n.nspname = extension_schema AND c.opcintype = 'text'::regtype\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION USING ERRCODE = 'object_not_in_prerequisite_state',\n"
                b"                    MESSAGE = 'pg_trgm must provide ' || extension_schema || '.gin_trgm_ops for text',\n"
                b"                    HINT = 'Ask the database administrator to repair the extension-owned GIN operator class. A same-named unrelated operator class is not supported.';\n"
                b"            END IF;\n"
                b"\n"
                b"            similarity_oid := pg_catalog.to_regprocedure(\n"
                b"                quote_ident(extension_schema) || '.similarity(text,text)'\n"
                b"            );\n"
                b"            IF similarity_oid IS NULL OR NOT EXISTS (\n"
                b"                SELECT FROM pg_catalog.pg_depend\n"
                b"                WHERE classid = 'pg_proc'::regclass AND objid = similarity_oid\n"
                b"                  AND deptype = 'e' AND refclassid = 'pg_extension'::regclass\n"
                b"                  AND refobjid = extension_oid\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION USING ERRCODE = 'object_not_in_prerequisite_state',\n"
                b"                    MESSAGE = 'pg_trgm must provide ' || extension_schema || '.similarity(text,text)',\n"
                b"                    HINT = 'Ask the database administrator to repair the extension-owned similarity function. A same-named unrelated function is not supported.';\n"
                b"            END IF;\n"
                b"            IF NOT pg_catalog.has_function_privilege(similarity_oid, 'EXECUTE') THEN\n"
                b"                RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',\n"
                b"                    MESSAGE = 'The current role needs EXECUTE on ' || extension_schema || '.similarity(text,text)',\n"
                b"                    HINT = 'Ask the database administrator to grant EXECUTE on the pg_trgm functions used by search to the application and migration roles.';\n"
                b"            END IF;\n"
                b"\n"
                b"            EXECUTE 'SELECT ' || quote_ident(extension_schema) || '.similarity($1, $2)'\n"
                b"                USING 'synthetic search'::text, 'synthetic search'::text;\n"
                b"        END\n"
                b"        $$;\n"
            ),
        ),
    ),
    "e2018a1b2c3d": (
        (
            (
                b"The preceding shared migration enables pg_trgm in public when permitted;\n"
            ),
            (
                b"The preceding shared migration enables pg_trgm in public (ibm_extension on IBM\n"
                b"Cloud) when permitted, and keeps an existing extension in its schema;\n"
            ),
        ),
        (
            (
                b"            f\"lower(CASE WHEN octet_length(coalesce({field}::text, '')) <= {_DOCUMENT_BYTES} THEN coalesce({field}::text, '') ELSE '' END) public.gin_trgm_ops\",\n"
            ),
            (
                b"            f\"lower(CASE WHEN octet_length(coalesce({field}::text, '')) <= {_DOCUMENT_BYTES} THEN coalesce({field}::text, '') ELSE '' END) {{trgm_schema}}.gin_trgm_ops\",\n"
            ),
        ),
        (
            (b"            AND o.opcname = :opclass AND n.nspname = :opclass_schema\n"),
            (
                b"            AND o.opcname = :opclass AND n.nspname = CASE WHEN :is_fts\n"
                b"                THEN 'pg_catalog' ELSE (SELECT en.nspname FROM pg_extension e\n"
                b"                    JOIN pg_namespace en ON en.oid = e.extnamespace\n"
                b"                    WHERE e.extname = 'pg_trgm') END\n"
            ),
        ),
        (
            (
                b'                "opclass": "tsvector_ops" if is_fts else "gin_trgm_ops",\n'
                b'                "opclass_schema": "pg_catalog" if is_fts else "public",\n'
            ),
            (
                b'                "opclass": "tsvector_ops" if is_fts else "gin_trgm_ops",\n'
            ),
        ),
        (
            (
                b"            IF NOT EXISTS (\n"
                b"                SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace\n"
                b"                WHERE e.extname = 'pg_trgm' AND n.nspname = 'public'\n"
                b"            ) THEN\n"
                b"                RAISE EXCEPTION 'Case text search requires pg_trgm in the public schema'\n"
                b"                    USING HINT = 'Run the shared scripts/postgres/pg-trgm.sql check as the migration role and ask an operator to provision pg_trgm in public before retrying.';\n"
                b"            END IF;\n"
                b"        END\n"
                b"        $$\n"
                b'        """\n'
            ),
            (
                b"            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN\n"
                b"                RAISE EXCEPTION 'Case text search requires pg_trgm to be enabled'\n"
                b"                    USING HINT = 'Run the shared scripts/postgres/pg-trgm.sql check as the migration role and ask an operator to provision pg_trgm in public (ibm_extension on IBM Cloud) before retrying.';\n"
                b"            END IF;\n"
                b"        END\n"
                b"        $$\n"
                b'        """\n'
                b"    )\n"
                b"    # pg_trgm is in public, or ibm_extension on IBM Cloud; regnamespace quotes it.\n"
                b"    trgm_schema = op.get_bind().scalar(\n"
                b"        sa.text(\n"
                b"            \"SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'\"\n"
                b"        )\n"
            ),
        ),
        (
            (
                b'                f\'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON public."case" USING gin ({expression})\'\n'
            ),
            (
                b'                f\'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON public."case" USING gin ({expression.format(trgm_schema=trgm_schema)})\'\n'
            ),
        ),
    ),
    # e4fd31674ae9 looks up pg_trgm's schema for show_trgm and gin_trgm_ops so
    # case search word lists also work in IBM Cloud's ibm_extension schema.
    "e4fd31674ae9": (
        (
            (
                b"# class that pg_get_indexdef() reports when public is on the search_path.\n"
            ),
            (
                b"# class that pg_get_indexdef() reports when pg_trgm's schema is on the\n"
                b"# search_path.\n"
            ),
        ),
        (
            (
                b"def _check_words_function() -> bool:\n"
                b"    return _check_function(\n"
                b'        f"public.{_WORDS_FUNCTION}(text)",\n'
                b"        _WORDS_BODY,\n"
            ),
            (
                b"def _trgm_schema() -> str | None:\n"
                b'    """Return pg_trgm\'s schema as SQL: public, or ibm_extension on IBM Cloud."""\n'
                b"    return op.get_bind().scalar(\n"
                b"        sa.text(\n"
                b"            \"SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'\"\n"
                b"        )\n"
                b"    )\n"
                b"\n"
                b"\n"
                b"def _in_trgm_schema(sql: str, trgm_schema: str) -> str:\n"
                b'    """Point this revision\'s pg_trgm calls, written for public, at its schema."""\n'
                b'    return sql.replace("public.show_trgm(", f"{trgm_schema}.show_trgm(")\n'
                b"\n"
                b"\n"
                b"def _check_words_function(trgm_schema: str) -> bool:\n"
                b"    return _check_function(\n"
                b'        f"public.{_WORDS_FUNCTION}(text)",\n'
                b"        _in_trgm_schema(_WORDS_BODY, trgm_schema),\n"
            ),
        ),
        (
            (b"def _index_state(name: str, definition: str) -> _IndexState:\n"),
            (
                b"def _index_state(name: str, definition: str, trgm_schema: str) -> _IndexState:\n"
            ),
        ),
        (
            (
                b"    # The operator class is schema-qualified when public is not on search_path.\n"
                b'    if row[0].replace(" public.gin_trgm_ops)", " gin_trgm_ops)") != definition:\n'
            ),
            (
                b"    # The operator class is schema-qualified when its schema is not on search_path.\n"
                b'    qualified = f" {trgm_schema}.gin_trgm_ops)"\n'
                b'    if row[0].replace(qualified, " gin_trgm_ops)") != definition:\n'
            ),
        ),
        (
            (
                b"    if not op.get_bind().scalar(\n"
                b"        sa.text(\"SELECT to_regprocedure('public.show_trgm(text)') IS NOT NULL\")\n"
                b"    ):\n"
                b'        raise RuntimeError("Case search word lists require pg_trgm in schema public.")\n'
            ),
            (
                b"    trgm_schema = _trgm_schema()\n"
                b"    if trgm_schema is None or not op.get_bind().scalar(\n"
                b'        sa.text("SELECT to_regprocedure(:show_trgm) IS NOT NULL"),\n'
                b'        {"show_trgm": f"{trgm_schema}.show_trgm(text)"},\n'
                b"    ):\n"
                b'        raise RuntimeError("Case search word lists require pg_trgm.")\n'
            ),
        ),
        (
            (
                b"    words_exists = _check_words_function()\n"
                b"    function_exists = _check_trigger_function()\n"
                b"    trigger_exists = _check_trigger()\n"
                b"    states = {\n"
                b"        name: _index_state(name, definition)\n"
            ),
            (
                b"    words_exists = _check_words_function(trgm_schema)\n"
                b"    function_exists = _check_trigger_function()\n"
                b"    trigger_exists = _check_trigger()\n"
                b"    states = {\n"
                b"        name: _index_state(name, definition, trgm_schema)\n"
            ),
        ),
        (
            (
                b'            ).replace(" gin_trgm_ops)", " public.gin_trgm_ops)")\n'
                b"        )\n"
                b"    if not words_exists:\n"
                b"        op.execute(_WORDS_FUNCTION_SQL)\n"
            ),
            (
                b'            ).replace(" gin_trgm_ops)", f" {trgm_schema}.gin_trgm_ops)")\n'
                b"        )\n"
                b"    if not words_exists:\n"
                b"        op.execute(_in_trgm_schema(_WORDS_FUNCTION_SQL, trgm_schema))\n"
            ),
        ),
        (
            (b'            if _index_state(name, definition) != "ready":\n'),
            (
                b'            if _index_state(name, definition, trgm_schema) != "ready":\n'
            ),
        ),
        (
            (
                b"    _check_words_function()\n"
                b"    _check_trigger_function()\n"
                b"    _check_trigger()\n"
                b"    for name, definition in _WORD_INDEXES:\n"
                b"        _index_state(name, definition)\n"
            ),
            (
                b'    trgm_schema = _trgm_schema() or "public"\n'
                b"    _check_words_function(trgm_schema)\n"
                b"    _check_trigger_function()\n"
                b"    _check_trigger()\n"
                b"    for name, definition in _WORD_INDEXES:\n"
                b"        _index_state(name, definition, trgm_schema)\n"
            ),
        ),
    ),
}


def check_migrations(
    scripts: ScriptDirectory,
    *,
    linear_since: str = LINEAR_HISTORY_BASE,
    base: ScriptDirectory | None = None,
    audited_rewrites: Mapping[str, tuple[tuple[bytes, bytes], ...]] = AUDITED_REWRITES,
) -> str:
    """Return the sole head, rejecting invalid graphs and new non-linear history."""
    # Alembic warns rather than fails for some invalid metadata (e.g. duplicate
    # revision IDs). Those must fail CI too.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        heads = scripts.get_heads()
        if len(heads) != 1:
            raise ValueError(
                f"Expected exactly one Alembic head; found {len(heads)}: {heads}. "
                "Rebase unmerged migrations onto the latest head."
            )
        bases = scripts.get_bases()
        if len(bases) != 1:
            raise ValueError(f"Expected exactly one Alembic base; found {bases}.")

        revisions = list(scripts.walk_revisions())
        if base is not None:
            candidate = {revision.revision: revision for revision in revisions}
            for previous in base.walk_revisions():
                current = candidate.get(previous.revision)
                if current is None:
                    raise ValueError(
                        f"Existing revision {previous.revision} was removed or renamed."
                    )
                # Use declared metadata: Alembic's branch_labels attribute also
                # includes labels inherited from other revisions in the graph.
                audited_rewrite = audited_rewrites.get(previous.revision)
                fields = (
                    ("depends_on", "branch_labels")
                    if audited_rewrite is not None
                    else ("down_revision", "depends_on", "branch_labels")
                )
                for field in fields:
                    if getattr(current.module, field, None) != getattr(
                        previous.module, field, None
                    ):
                        raise ValueError(
                            f"Existing revision {previous.revision} changed {field}. "
                            "New migrations must extend the previous head without "
                            "rewriting existing revision metadata."
                        )
                current_bytes = Path(current.path).read_bytes()
                base_bytes = Path(previous.path).read_bytes()
                if audited_rewrite is not None:
                    expected_bytes = base_bytes
                    for old_line, new_line in audited_rewrite:
                        old_count = expected_bytes.count(old_line)
                        if old_count == 1:
                            expected_bytes = expected_bytes.replace(
                                old_line, new_line, 1
                            )
                        elif old_count == 0 and expected_bytes.count(new_line) == 1:
                            continue
                        else:
                            raise ValueError(
                                f"The audited rewrite for {previous.revision} no "
                                "longer matches the base file."
                            )
                    if current_bytes != expected_bytes:
                        raise ValueError(
                            f"Existing revision {previous.revision} changed file "
                            "contents beyond its audited rewrite."
                        )
                elif current_bytes != base_bytes:
                    raise ValueError(
                        f"Existing revision {previous.revision} changed file contents. "
                        "Existing migration files are immutable, including formatting; "
                        "put new operations in a new revision."
                    )
        historical = {
            revision.revision for revision in scripts.walk_revisions(head=linear_since)
        }
        for revision in revisions:
            if revision.revision in historical:
                continue
            if not isinstance(revision.down_revision, str):
                raise ValueError(
                    f"Revision {revision.revision} must have exactly one parent; "
                    f"found {revision.down_revision!r}. New merge revisions are "
                    f"not allowed after {linear_since}."
                )
            if revision.dependencies:
                raise ValueError(
                    f"Revision {revision.revision} uses depends_on; "
                    "new migrations must form a single down_revision chain."
                )
        return heads[0]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        help="Base checkout's Alembic directory; also reject changes to existing revisions.",
    )
    args = parser.parse_args(argv)
    try:
        head = check_migrations(
            ScriptDirectory.from_config(Config("alembic.ini")),
            base=ScriptDirectory(args.base_dir) if args.base_dir is not None else None,
        )
    except (
        ValueError,
        KeyError,
        OSError,
        CommandError,
        RevisionError,
        UserWarning,
    ) as exc:
        print(f"Alembic migration history check failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Alembic migration history OK: one head ({head}), linear after {LINEAR_HISTORY_BASE}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
