"""Add native case text search and trigram expression indexes.

Revision ID: e2018a1b2c3d
Revises: a3f8c2d1e9b4
Create Date: 2026-10-12 12:00:00.000000

Expression indexes cover existing rows and every legacy INSERT/UPDATE/DELETE
without application dual-writes or a stored-column backfill. The explicit
``simple`` configuration preserves security vocabulary and does not depend on
the server's default text-search configuration. Guarded source vectors supply indexed word candidates. The positional native
helper preserves full-source phrase/word matching beyond whole-vector limits.

The preceding shared migration enables pg_trgm in public (ibm_extension on IBM
Cloud) when permitted, and keeps an existing extension in its schema;
restricted migration roles require administrator preinstallation using the
shared scripts/postgres/pg-trgm.sql tooling. This revision only checks readiness; it
never installs, moves, upgrades, or drops the shared extension. Concurrent
builds allow ordinary old/new application writes and are retryable after an
invalid failed build. Downgrade removes only this revision's indexes and helper functions.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2018a1b2c3d"
down_revision: str | None = "a3f8c2d1e9b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A byte bound keeps whole vectors below the size/position limits. Larger
# source text remains stored and is searched by the native positional helper.
_DOCUMENT_BYTES = 8192
_MATCH_FUNCTION = "tracecat_case_text_matches_v1"
_MATCH_BODY = r"""
WITH query_tokens AS MATERIALIZED (
    SELECT p.ordinality,
        CASE WHEN cardinality(l.lexemes) = 1 AND octet_length(l.lexemes[1]) < 2047
            THEN l.lexemes[1] ELSE NULL END AS lexeme
    FROM pg_catalog.ts_parse('pg_catalog.default', query_text)
        WITH ORDINALITY p(tokid, token, ordinality)
    JOIN pg_catalog.pg_ts_config_map m
        ON m.mapcfg = 'pg_catalog.simple'::regconfig AND m.maptokentype = p.tokid
    CROSS JOIN LATERAL (SELECT pg_catalog.ts_lexize(m.mapdict::regdictionary, p.token) AS lexemes) l
    WHERE octet_length(p.token) < 2047
), query_positions AS (
    SELECT row_number() OVER (ORDER BY ordinality) AS position, lexeme
    FROM query_tokens
), query_bounds AS (
    SELECT min(position) AS first, max(position) AS last
    FROM query_positions WHERE lexeme IS NOT NULL
), required AS MATERIALIZED (
    SELECT q.position - b.first + 1 AS position, q.lexeme
    FROM query_positions q CROSS JOIN query_bounds b
    WHERE q.position BETWEEN b.first AND b.last
), source_tokens AS MATERIALIZED (
    SELECT p.ordinality,
        CASE WHEN cardinality(l.lexemes) = 1 AND octet_length(l.lexemes[1]) < 2047
            THEN l.lexemes[1] ELSE NULL END AS lexeme
    FROM pg_catalog.ts_parse('pg_catalog.default', source_text)
        WITH ORDINALITY p(tokid, token, ordinality)
    JOIN pg_catalog.pg_ts_config_map m
        ON m.mapcfg = 'pg_catalog.simple'::regconfig AND m.maptokentype = p.tokid
    CROSS JOIN LATERAL (SELECT pg_catalog.ts_lexize(m.mapdict::regdictionary, p.token) AS lexemes) l
    WHERE octet_length(p.token) < 2047
), source_positions AS MATERIALIZED (
    SELECT row_number() OVER (ORDER BY ordinality) AS position, lexeme
    FROM source_tokens
)
 , source_windows AS (
    SELECT position, lexeme, array_agg(lexeme) OVER (
        ORDER BY position ROWS BETWEEN CURRENT ROW
            AND (SELECT coalesce(max(position),1)-1 FROM required) FOLLOWING
    ) AS frame
    FROM source_positions
)
SELECT EXISTS (SELECT 1 FROM required WHERE lexeme IS NOT NULL)
    AND CASE WHEN phrase THEN EXISTS (
        SELECT 1 FROM source_windows s JOIN required first
            ON first.position = 1 AND s.lexeme = first.lexeme
        WHERE NOT EXISTS (
            SELECT 1 FROM required q
            WHERE q.lexeme IS NOT NULL AND s.frame[q.position::integer] IS DISTINCT FROM q.lexeme
        )
    ) ELSE NOT EXISTS (
        SELECT 1 FROM required q
        WHERE q.lexeme IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM source_positions s WHERE s.lexeme = q.lexeme
        )
    ) END
""".strip()

_VECTOR_BODY = r"""
DECLARE
    result tsvector;
BEGIN
    IF octet_length(source_text) > 8192 THEN
        RETURN NULL;
    END IF;
    -- Pin safe token bytes across PostgreSQL minor versions. Native lowercase
    -- can expand bytes, so a document-size bound alone is insufficient.
    IF octet_length(source_text) >= 2047 OR source_text ~ '[^\x01-\x7f]' THEN
        IF EXISTS (
            SELECT 1 FROM pg_catalog.ts_parse('pg_catalog.default', source_text) p
            JOIN pg_catalog.pg_ts_config_map m
                ON m.mapcfg = 'pg_catalog.simple'::regconfig AND m.maptokentype = p.tokid
            CROSS JOIN LATERAL (SELECT pg_catalog.ts_lexize(m.mapdict::regdictionary, p.token) AS lexemes) l
            WHERE octet_length(p.token) >= 2047
                OR EXISTS (SELECT 1 FROM unnest(l.lexemes) lexeme WHERE octet_length(lexeme) >= 2047)
        ) THEN
            RETURN NULL;
        END IF;
    END IF;
    result := pg_catalog.to_tsvector('pg_catalog.simple'::regconfig, source_text);
    PERFORM result::text;
    RETURN result;
EXCEPTION WHEN program_limit_exceeded OR character_not_in_repertoire THEN
    RETURN NULL;
END
""".strip()
_FUNCTION_SQL_COMMANDS = (
    f"""CREATE OR REPLACE FUNCTION public.tracecat_case_text_matches_v1(source_text text, query_text text, phrase boolean)
        RETURNS boolean LANGUAGE sql STABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog AS $case_search$\n{_MATCH_BODY}\n$case_search$""",
    f"""CREATE OR REPLACE FUNCTION public.tracecat_case_safe_vector_v1(source_text text)
        RETURNS tsvector LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL UNSAFE
        SET search_path = pg_catalog AS $case_search$\n{_VECTOR_BODY}\n$case_search$""",
)

_INDEXES = tuple(
    (f"ix_case_{field}_{kind}", expression)
    for field in ("summary", "description")
    for kind, expression in (
        (
            "fts",
            f"public.tracecat_case_safe_vector_v1(coalesce({field}::text, ''))",
        ),
        (
            "trgm",
            f"lower(CASE WHEN octet_length(coalesce({field}::text, '')) <= {_DOCUMENT_BYTES} THEN coalesce({field}::text, '') ELSE '' END) {{trgm_schema}}.gin_trgm_ops",
        ),
    )
)


_FALLBACK_INDEXES = tuple(
    (
        f"ix_case_{field}_text_fallback",
        f"public.tracecat_case_safe_vector_v1(coalesce({field}::text, '')) IS NULL",
    )
    for field in ("summary", "description")
)


def _fallback_index_state(name: str, predicate: str) -> bool | None:
    """Validate each small overflow-workspace index before concurrent repair."""
    row = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT i.indisvalid,
            c.relkind='i' AND i.indrelid='public.case'::regclass
            AND a.amname='btree' AND NOT i.indisunique
            AND i.indnkeyatts=2 AND i.indnatts=2 AND i.indexprs IS NULL
            AND i.indkey[0]=(SELECT attnum FROM pg_attribute WHERE attrelid='public.case'::regclass AND attname='workspace_id')
            AND i.indkey[1]=(SELECT attnum FROM pg_attribute WHERE attrelid='public.case'::regclass AND attname='id')
            AND i.indoption[0]=0 AND i.indoption[1]=0
            AND i.indclass[0]=(SELECT o.oid FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace JOIN pg_am am ON am.oid=o.opcmethod WHERE o.opcname='uuid_ops' AND n.nspname='pg_catalog' AND am.amname='btree')
            AND i.indclass[1]=(SELECT o.oid FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace JOIN pg_am am ON am.oid=o.opcmethod WHERE o.opcname='uuid_ops' AND n.nspname='pg_catalog' AND am.amname='btree')
            AND EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
                    AND d.refclassid='pg_proc'::regclass
                    AND d.refobjid=to_regprocedure('public.tracecat_case_safe_vector_v1(text)')
            )
            AND (NOT i.indisvalid OR i.indisready) AS compatible,
            pg_get_expr(i.indpred,i.indrelid) AS predicate
        FROM pg_class c LEFT JOIN pg_index i ON i.indexrelid=c.oid
        LEFT JOIN pg_am a ON a.oid=c.relam
        WHERE c.oid=to_regclass(:name)
    """),
            {"name": f"public.{name}"},
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        return None
    field = "summary" if "summary" in name else "description"
    expected = {
        f"({helper}({source}) IS NULL)"
        for source in (
            f"COALESCE(({field})::text, ''::text)",
            f"COALESCE({field}, ''::text)",
        )
        for helper in (
            "tracecat_case_safe_vector_v1",
            "public.tracecat_case_safe_vector_v1",
        )
    }
    if row[1] is not True or row[2] not in expected:
        raise RuntimeError(
            f"Case text search overflow index public.{name} has an incompatible existing definition ({predicate}); preserve it and resolve the collision before retrying."
        )
    return bool(row[0])


def _function_exists(name: str, body: str, *, vector: bool = False) -> bool:
    """Never replace an unrelated function occupying this revision's name."""
    signature = (
        f"public.{name}(text)" if vector else f"public.{name}(text,text,boolean)"
    )
    row = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT p.prosrc, p.prorettype = CAST(:return_type AS regtype)
            AND l.lanname = :language AND p.provolatile = :volatility
            AND p.proparallel = :parallel AND p.proisstrict AND NOT p.prosecdef AND p.prokind = 'f'
            AND p.proargnames = CAST(:arguments AS text[])
            AND p.proconfig = ARRAY['search_path=pg_catalog']::text[] AS compatible
        FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.oid = to_regprocedure(:signature)
    """),
            {
                "signature": signature,
                "return_type": "tsvector" if vector else "bool",
                "language": "plpgsql" if vector else "sql",
                "volatility": "i" if vector else "s",
                "parallel": "u" if vector else "s",
                "arguments": ["source_text"]
                if vector
                else ["source_text", "query_text", "phrase"],
            },
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        return False
    if row[0].strip() != body or row[1] is not True:
        raise RuntimeError(
            "Case text search native helper has an incompatible existing definition; preserve it and ask an operator to resolve the collision."
        )
    return True


def _index_state(name: str, expression: str) -> bool | None:
    """Validate ownership/definition before reusing or rebuilding a name."""
    field = "summary" if "summary" in name else "description"
    is_fts = name.endswith("_fts")
    expected_expressions = set()
    for source in (
        f"COALESCE(({field})::text, ''::text)",
        f"COALESCE({field}, ''::text)",
    ):
        if is_fts:
            for helper in (
                "tracecat_case_safe_vector_v1",
                "public.tracecat_case_safe_vector_v1",
            ):
                expected_expressions.add(f"{helper}({source})")
        else:
            guard = f"CASE WHEN (octet_length({source}) <= {_DOCUMENT_BYTES}) THEN {source} ELSE ''::text END"
            expected_expressions.add(f"lower({guard})")
    row = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT i.indisvalid,
            c.relkind = 'i' AND i.indrelid = 'public.case'::regclass
            AND a.amname = 'gin' AND NOT i.indisunique
            AND i.indnkeyatts = 1 AND i.indnatts = 1 AND i.indkey[0] = 0
            AND i.indpred IS NULL AND (NOT i.indisvalid OR i.indisready)
            AND o.opcname = :opclass AND n.nspname = CASE WHEN :is_fts
                THEN 'pg_catalog' ELSE (SELECT en.nspname FROM pg_extension e
                    JOIN pg_namespace en ON en.oid = e.extnamespace
                    WHERE e.extname = 'pg_trgm') END
            AND (NOT :is_fts OR EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
                    AND d.refclassid='pg_proc'::regclass
                    AND d.refobjid=to_regprocedure('public.tracecat_case_safe_vector_v1(text)')
            )) AS compatible,
            pg_get_expr(i.indexprs, i.indrelid) AS expression
        FROM pg_class c
        LEFT JOIN pg_index i ON i.indexrelid = c.oid
        LEFT JOIN pg_am a ON a.oid = c.relam
        LEFT JOIN pg_opclass o ON o.oid = i.indclass[0]
        LEFT JOIN pg_namespace n ON n.oid = o.opcnamespace
        WHERE c.oid = to_regclass(:name)
        """),
            {
                "name": f"public.{name}",
                "opclass": "tsvector_ops" if is_fts else "gin_trgm_ops",
                "is_fts": is_fts,
            },
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        return None
    valid, compatible, actual_expression = row
    if (
        compatible is not True
        or re.sub(
            r"\(\s+",
            "(",
            re.sub(r"\s+\)", ")", re.sub(r"\s+", " ", actual_expression or "").strip()),
        )
        not in expected_expressions
    ):
        raise RuntimeError(
            f"Case text search index public.{name} has an incompatible existing definition. "
            f"Expected a non-partial GIN index on public.case ({expression}); "
            "preserve the existing object and have an operator resolve the name collision before retrying."
        )
    return bool(valid)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
                RAISE EXCEPTION 'Case text search requires the pg_trgm extension'
                    USING HINT = 'Install the PostgreSQL pg_trgm package and allow pg_trgm in the RDS extension policy, then retry.';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
                RAISE EXCEPTION 'Case text search requires pg_trgm to be enabled'
                    USING HINT = 'Run the shared scripts/postgres/pg-trgm.sql check as the migration role and ask an operator to provision pg_trgm in public (ibm_extension on IBM Cloud) before retrying.';
            END IF;
        END
        $$
        """
    )
    # pg_trgm is in public, or ibm_extension on IBM Cloud; regnamespace quotes it.
    trgm_schema = op.get_bind().scalar(
        sa.text(
            "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'"
        )
    )
    # Inspect every name before any concurrent build/drop. Never adopt or
    # destroy an unrelated index merely because its name matches this revision.
    states = {name: _index_state(name, expression) for name, expression in _INDEXES}
    fallback_states = {
        name: _fallback_index_state(name, predicate)
        for name, predicate in _FALLBACK_INDEXES
    }
    functions = (
        ("tracecat_case_text_matches_v1", _MATCH_BODY, False),
        ("tracecat_case_safe_vector_v1", _VECTOR_BODY, True),
    )
    present = [
        _function_exists(name, body, vector=vector) for name, body, vector in functions
    ]
    for exists, command in zip(present, _FUNCTION_SQL_COMMANDS, strict=True):
        if not exists:
            # Preflight established absence; do not replace a same-name object.
            op.execute(
                command.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
            )
    with op.get_context().autocommit_block():
        for name, expression in _INDEXES:
            if states[name] is False:
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON public."case" USING gin ({expression.format(trgm_schema=trgm_schema)})'
            )
            # Verify again to catch a competing same-name object created after
            # preflight. Name existence alone is never readiness evidence.
            if _index_state(name, expression) is not True:
                raise RuntimeError(
                    f"Case text search index public.{name} is not ready; retry the migration."
                )

        for name, predicate in _FALLBACK_INDEXES:
            if fallback_states[name] is False:
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON public."case" (workspace_id,id) WHERE {predicate}'
            )
            if _fallback_index_state(name, predicate) is not True:
                raise RuntimeError(
                    f"Case text search overflow index public.{name} is not ready; retry."
                )


def downgrade() -> None:
    _function_exists("tracecat_case_text_matches_v1", _MATCH_BODY)
    _function_exists("tracecat_case_safe_vector_v1", _VECTOR_BODY, vector=True)
    for name, expression in _INDEXES:
        _index_state(name, expression)
    for name, predicate in _FALLBACK_INDEXES:
        _fallback_index_state(name, predicate)
    with op.get_context().autocommit_block():
        for name, _ in reversed(_FALLBACK_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
        for name, _ in reversed(_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
    op.execute(
        "DROP FUNCTION IF EXISTS public.tracecat_case_text_matches_v1(text,text,boolean)"
    )
    op.execute("DROP FUNCTION IF EXISTS public.tracecat_case_safe_vector_v1(text)")
