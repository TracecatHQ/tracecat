"""Case lexical index/function readiness without DDL or automatic repair."""

import re

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.query.lexical_types import LexicalSearchError, LexicalSearchErrorCode

CASE_SEARCH_GENERATION = "case-text-v2"
CASE_TEXT_DOCUMENT_BYTES = 8192
CASE_TEXT_MATCH_FUNCTION = "tracecat_case_text_matches_v1"
# Matches the immutable body carried by the additive Alembic revision. A
# changed same-name helper must never silently change an issued continuation.
CASE_TEXT_MATCH_BODY_HASH = "c7f46017f2472f11cd81a697c54328f2"
CASE_TEXT_VECTOR_BODY_HASH = "08c09d8befdb26cbb7f76a0d66e075de"


async def ensure_case_text_indexes(session: AsyncSession) -> None:
    """Verify extension/index readiness without installing or scanning sources."""
    extension = await session.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')")
    )
    if not extension:
        raise LexicalSearchError(LexicalSearchErrorCode.UNAVAILABLE)
    # Search calls pg_trgm unqualified because its schema varies (public, or
    # ibm_extension on IBM Cloud). Verify that search_path resolves each name
    # to the extension's own objects, not a same-named lookalike.
    permitted = await session.scalar(
        sa.text("""
        WITH extension AS (
            SELECT oid, extnamespace FROM pg_extension WHERE extname = 'pg_trgm'
        ),
        required AS (
            SELECT to_regprocedure('strict_word_similarity(text,text)')::oid AS oid
            UNION ALL
            SELECT o.oprcode FROM pg_operator o
                JOIN pg_depend d ON d.classid = 'pg_operator'::regclass AND d.objid = o.oid
                    AND d.deptype = 'e' AND d.refclassid = 'pg_extension'::regclass
                JOIN extension e ON e.oid = d.refobjid
            WHERE o.oid = to_regoperator('%>>(text,text)')
        )
        SELECT has_schema_privilege((SELECT extnamespace FROM extension), 'USAGE')
            AND count(*) = 2
            AND coalesce(bool_and(has_function_privilege(r.oid, 'EXECUTE')), false)
        FROM required r JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = r.oid
            AND d.deptype = 'e' AND d.refclassid = 'pg_extension'::regclass
        JOIN extension e ON e.oid = d.refobjid
    """)
    )
    if not permitted:
        raise LexicalSearchError(LexicalSearchErrorCode.UNAVAILABLE)
    native_helper = await session.scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
            WHERE p.oid = to_regprocedure('public.tracecat_case_text_matches_v1(text,text,boolean)')
              AND p.prorettype = 'bool'::regtype AND l.lanname = 'sql'
              AND p.provolatile = 's' AND p.proparallel = 's' AND p.proisstrict
              AND NOT p.prosecdef AND p.prokind = 'f'
              AND p.proargnames = ARRAY['source_text','query_text','phrase']::text[]
              AND p.proconfig = ARRAY['search_path=pg_catalog']::text[]
              AND md5(regexp_replace(p.prosrc, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = :body_hash
              AND has_function_privilege(p.oid, 'EXECUTE')
        )
    """),
        {"body_hash": CASE_TEXT_MATCH_BODY_HASH},
    )
    if not native_helper:
        raise LexicalSearchError(LexicalSearchErrorCode.UNAVAILABLE)
    safe_vector = await session.scalar(
        sa.text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
            WHERE p.oid = to_regprocedure('public.tracecat_case_safe_vector_v1(text)')
              AND p.prorettype = 'tsvector'::regtype AND l.lanname = 'plpgsql'
              AND p.provolatile = 'i' AND p.proparallel = 'u' AND p.proisstrict
              AND NOT p.prosecdef AND p.prokind = 'f'
              AND p.proargnames = ARRAY['source_text']::text[]
              AND p.proconfig = ARRAY['search_path=pg_catalog']::text[]
              AND md5(regexp_replace(p.prosrc, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = :body_hash
              AND has_function_privilege(p.oid, 'EXECUTE')
        )
    """),
        {"body_hash": CASE_TEXT_VECTOR_BODY_HASH},
    )
    if not safe_vector:
        raise LexicalSearchError(LexicalSearchErrorCode.UNAVAILABLE)
    # PostgreSQL 16's simple configuration maps these default-parser token types.
    # Reject omissions as well as dictionary/sequence changes to that baseline.
    configuration = await session.scalar(
        sa.text("""
        SELECT c.cfgparser = (SELECT p.oid FROM pg_ts_parser p JOIN pg_namespace n ON n.oid=p.prsnamespace WHERE n.nspname='pg_catalog' AND p.prsname='default')
            AND d.dictinitoption IS NULL
            AND (SELECT array_agg(m.maptokentype ORDER BY m.maptokentype)
                FROM pg_ts_config_map m WHERE m.mapcfg = c.oid)
                = ARRAY[1,2,3,4,5,6,7,8,9,10,11,15,16,17,18,19,20,21,22]::integer[]
            AND NOT EXISTS (SELECT 1 FROM pg_ts_config_map m
                WHERE m.mapcfg = c.oid AND (m.mapdict <> d.oid OR m.mapseqno <> 1))
        FROM pg_ts_config c CROSS JOIN pg_ts_dict d
        WHERE c.oid = 'pg_catalog.simple'::regconfig AND d.oid = 'pg_catalog.simple'::regdictionary
    """)
    )
    if not configuration:
        raise LexicalSearchError(LexicalSearchErrorCode.CONFIGURATION_CHANGED)
    rows = (
        (
            await session.execute(
                sa.text("""
        SELECT c.relname, i.indisvalid AND i.indisready AS ready,
            i.indrelid = 'public.case'::regclass AND a.amname = 'gin'
            AND NOT i.indisunique AND i.indpred IS NULL
            AND i.indnkeyatts = 1 AND i.indnatts = 1 AND i.indkey[0] = 0
            AND o.opcname = CASE WHEN c.relname LIKE '%_fts' THEN 'tsvector_ops' ELSE 'gin_trgm_ops' END
            AND n.nspname = CASE WHEN c.relname LIKE '%_fts' THEN 'pg_catalog' ELSE (
                SELECT en.nspname FROM pg_extension e
                JOIN pg_namespace en ON en.oid = e.extnamespace WHERE e.extname = 'pg_trgm'
            ) END
            AND (c.relname NOT LIKE '%_fts' OR EXISTS (
                SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid
                    AND d.refclassid='pg_proc'::regclass
                    AND d.refobjid=to_regprocedure('public.tracecat_case_safe_vector_v1(text)')
            )) AS compatible,
            pg_get_expr(i.indexprs, i.indrelid) AS expression
        FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = c.oid
        LEFT JOIN pg_am a ON a.oid = c.relam
        LEFT JOIN pg_opclass o ON o.oid = i.indclass[0]
        LEFT JOIN pg_namespace n ON n.oid = o.opcnamespace
        WHERE s.nspname = 'public' AND c.relname = ANY(:names)
    """),
                {
                    "names": [
                        "ix_case_summary_fts",
                        "ix_case_description_fts",
                        "ix_case_summary_trgm",
                        "ix_case_description_trgm",
                    ]
                },
            )
        )
        .tuples()
        .all()
    )
    for name, ready, compatible, expression in rows:
        field = "summary" if "summary" in name else "description"
        expected = set()
        for source in (
            f"COALESCE(({field})::text, ''::text)",
            f"COALESCE({field}, ''::text)",
        ):
            if name.endswith("_fts"):
                for helper in (
                    "tracecat_case_safe_vector_v1",
                    "public.tracecat_case_safe_vector_v1",
                ):
                    expected.add(f"{helper}({source})")
            else:
                guard = f"CASE WHEN (octet_length({source}) <= {CASE_TEXT_DOCUMENT_BYTES}) THEN {source} ELSE ''::text END"
                expected.add(f"lower({guard})")
        if (
            compatible is not True
            or re.sub(
                r"\(\s+",
                "(",
                re.sub(r"\s+\)", ")", re.sub(r"\s+", " ", expression or "").strip()),
            )
            not in expected
        ):
            raise LexicalSearchError(LexicalSearchErrorCode.CONFIGURATION_CHANGED)
        if ready is not True:
            raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    if len(rows) != 4:
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    overflow = (
        (
            await session.execute(
                sa.text("""
        SELECT c.relname, i.indisvalid AND i.indisready,
            i.indrelid='public.case'::regclass AND a.amname='btree' AND NOT i.indisunique
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
            ) AS compatible,
            pg_get_expr(i.indpred,i.indrelid)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid=c.oid LEFT JOIN pg_am a ON a.oid=c.relam
        WHERE n.nspname='public' AND c.relname=ANY(:names)
    """),
                {
                    "names": [
                        "ix_case_summary_text_fallback",
                        "ix_case_description_text_fallback",
                    ]
                },
            )
        )
        .tuples()
        .all()
    )
    for name, ready, compatible, predicate in overflow:
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
        if compatible is not True or predicate not in expected:
            raise LexicalSearchError(LexicalSearchErrorCode.CONFIGURATION_CHANGED)
        if ready is not True:
            raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    if len(overflow) != 2:
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
