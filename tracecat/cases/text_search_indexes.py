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
# Stored vectors from revision 5ca140c06ec2. Index definitions are exactly what
# pg_get_indexdef() reports, so any drift is a configuration change.
CASE_SEARCH_TRIGGER = "trg_case_search_vectors"
CASE_SEARCH_TRIGGER_FUNCTION = "tracecat_case_search_vectors_v1"
CASE_SEARCH_TRIGGER_BODY_HASH = "3bc6d04ff3e3411985bdce0aac8f48b6"
CASE_SEARCH_VECTOR_INDEXES = {
    "ix_case_summary_tsv": 'CREATE INDEX ix_case_summary_tsv ON public."case" USING gin (summary_tsv)',
    "ix_case_description_tsv": 'CREATE INDEX ix_case_description_tsv ON public."case" USING gin (description_tsv)',
    "ix_case_summary_tsv_fallback": 'CREATE INDEX ix_case_summary_tsv_fallback ON public."case" USING btree (workspace_id, id) WHERE (summary_tsv IS NULL)',
    "ix_case_description_tsv_fallback": 'CREATE INDEX ix_case_description_tsv_fallback ON public."case" USING btree (workspace_id, id) WHERE (description_tsv IS NULL)',
}
# Stored word lists and vocabulary from revision e4fd31674ae9.
CASE_SEARCH_WORDS_TRIGGER = "trg_case_search_words"
CASE_SEARCH_WORDS_TRIGGER_FUNCTION = "tracecat_case_search_words_v1"
CASE_SEARCH_WORDS_TRIGGER_BODY_HASH = "d48a65db907b7efea28d3277250298d8"
CASE_WORDS_FUNCTION = "tracecat_case_words_v1"
CASE_WORDS_BODY_HASH = "c1884ffb8b8da892b01688030163fe80"
CASE_SEARCH_WORD_INDEXES = {
    "ix_case_summary_words": 'CREATE INDEX ix_case_summary_words ON public."case" USING gin (summary_words)',
    "ix_case_description_words": 'CREATE INDEX ix_case_description_words ON public."case" USING gin (description_words)',
    "ix_case_summary_words_fallback": 'CREATE INDEX ix_case_summary_words_fallback ON public."case" USING btree (workspace_id, id) WHERE (summary_words IS NULL)',
    "ix_case_description_words_fallback": 'CREATE INDEX ix_case_description_words_fallback ON public."case" USING btree (workspace_id, id) WHERE (description_words IS NULL)',
    "ix_case_search_word_workspace_id_word": "CREATE INDEX ix_case_search_word_workspace_id_word ON public.case_search_word USING btree (workspace_id, word)",
    "ix_case_search_word_word_trgm": "CREATE INDEX ix_case_search_word_word_trgm ON public.case_search_word USING gin (word gin_trgm_ops)",
}
# pg_trigger.tgtype bits: ROW (1) | BEFORE (2) | INSERT (4) | UPDATE (16).
_BEFORE_ROW_INSERT_UPDATE = 23


async def ensure_case_text_indexes(session: AsyncSession) -> None:
    """Verify extension/index readiness without installing or scanning sources."""
    extension = await session.scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'pg_trgm' AND n.nspname = 'public')
    """)
    )
    if not extension:
        raise LexicalSearchError(LexicalSearchErrorCode.UNAVAILABLE)
    permitted = await session.scalar(
        sa.text("""
        WITH extension AS (SELECT oid FROM pg_extension WHERE extname = 'pg_trgm'),
        required AS (
            SELECT to_regprocedure('public.strict_word_similarity(text,text)')::oid AS oid
            UNION ALL
            SELECT o.oprcode FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace
                JOIN pg_depend d ON d.classid = 'pg_operator'::regclass AND d.objid = o.oid
                    AND d.deptype = 'e' AND d.refclassid = 'pg_extension'::regclass
                JOIN extension e ON e.oid = d.refobjid
            WHERE n.nspname = 'public' AND o.oprname = '%>>'
                AND o.oprleft = 'text'::regtype AND o.oprright = 'text'::regtype
        )
        SELECT has_schema_privilege('public', 'USAGE') AND count(*) = 2
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
    trigram_rows = (
        (
            await session.execute(
                sa.text("""
        SELECT c.relname, i.indisvalid AND i.indisready AS ready,
            i.indrelid = 'public.case'::regclass AND a.amname = 'gin'
            AND NOT i.indisunique AND i.indpred IS NULL
            AND i.indnkeyatts = 1 AND i.indnatts = 1 AND i.indkey[0] = 0
            AND o.opcname = 'gin_trgm_ops' AND n.nspname = 'public' AS compatible,
            pg_get_expr(i.indexprs, i.indrelid) AS expression
        FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = c.oid
        LEFT JOIN pg_am a ON a.oid = c.relam
        LEFT JOIN pg_opclass o ON o.oid = i.indclass[0]
        LEFT JOIN pg_namespace n ON n.oid = o.opcnamespace
        WHERE s.nspname = 'public' AND c.relname = ANY(:names)
    """),
                {"names": ["ix_case_summary_trgm", "ix_case_description_trgm"]},
            )
        )
        .tuples()
        .all()
    )
    for name, ready, compatible, expression in trigram_rows:
        field = "summary" if "summary" in name else "description"
        expected = set()
        for source in (
            f"COALESCE(({field})::text, ''::text)",
            f"COALESCE({field}, ''::text)",
        ):
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
    if len(trigram_rows) != 2:
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    await _ensure_trigger(
        session,
        CASE_SEARCH_TRIGGER,
        CASE_SEARCH_TRIGGER_FUNCTION,
        CASE_SEARCH_TRIGGER_BODY_HASH,
    )
    await _ensure_indexes(session, CASE_SEARCH_VECTOR_INDEXES)
    await _ensure_stored_words(session)


async def _ensure_stored_words(session: AsyncSession) -> None:
    """Verify the word-list helper, its trigger, vocabulary, and indexes.

    Search reads the vocabulary but never calls the helper; only case writes do,
    through the trigger, so the helper's execute privilege is not checked here.
    """
    words_function = await session.scalar(
        sa.text("""
        SELECT md5(regexp_replace(p.prosrc, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = :body_hash
            AND p.prorettype = 'text[]'::regtype AND l.lanname = 'sql'
            AND p.provolatile = 'i' AND p.proisstrict
            AND NOT p.prosecdef AND p.prokind = 'f'
            AND p.proconfig IS NOT DISTINCT FROM ARRAY['search_path=pg_catalog']::text[]
        FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.oid = to_regprocedure(:function)
    """),
        {
            "function": f"public.{CASE_WORDS_FUNCTION}(text)",
            "body_hash": CASE_WORDS_BODY_HASH,
        },
    )
    if words_function is None:
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    if words_function is not True:
        raise LexicalSearchError(LexicalSearchErrorCode.CONFIGURATION_CHANGED)
    vocabulary = (
        await session.execute(
            sa.text("""
        SELECT to_regclass('public.case_search_word') IS NOT NULL,
            coalesce(has_table_privilege(to_regclass('public.case_search_word'), 'SELECT'), false)
    """)
        )
    ).one()
    if not vocabulary[0]:
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    if not vocabulary[1]:
        raise LexicalSearchError(LexicalSearchErrorCode.UNAVAILABLE)
    await _ensure_trigger(
        session,
        CASE_SEARCH_WORDS_TRIGGER,
        CASE_SEARCH_WORDS_TRIGGER_FUNCTION,
        CASE_SEARCH_WORDS_TRIGGER_BODY_HASH,
    )
    await _ensure_indexes(session, CASE_SEARCH_WORD_INDEXES)


async def _ensure_trigger(
    session: AsyncSession, name: str, function: str, body_hash: str
) -> None:
    """Verify a BEFORE INSERT/UPDATE row trigger that maintains search columns."""
    compatible = (
        await session.execute(
            sa.text("""
        SELECT t.tgfoid = to_regprocedure(:function)
            AND t.tgenabled IN ('O', 'A')
            AND t.tgtype = :trigger_type
            AND cardinality(t.tgattr::smallint[]) = 0
            AND t.tgqual IS NULL
            AND p.prorettype = 'trigger'::regtype AND l.lanname = 'plpgsql'
            AND NOT p.prosecdef AND p.prokind = 'f'
            -- proconfig is NULL without a SET clause; keep the result non-NULL
            -- so that is a configuration change, not missing indexing.
            AND p.proconfig IS NOT DISTINCT FROM ARRAY['search_path=pg_catalog']::text[]
            AND md5(regexp_replace(p.prosrc, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = :body_hash
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        JOIN pg_language l ON l.oid = p.prolang
        WHERE t.tgrelid = 'public.case'::regclass AND t.tgname = :name
    """),
            {
                "name": name,
                "function": f"public.{function}()",
                "trigger_type": _BEFORE_ROW_INSERT_UPDATE,
                "body_hash": body_hash,
            },
        )
    ).scalar_one_or_none()
    if compatible is None:
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    if compatible is not True:
        raise LexicalSearchError(LexicalSearchErrorCode.CONFIGURATION_CHANGED)


async def _ensure_indexes(session: AsyncSession, indexes: dict[str, str]) -> None:
    """Verify each named index exists, is ready, and has its exact definition."""
    rows = (
        (
            await session.execute(
                sa.text("""
        SELECT c.relname, pg_get_indexdef(c.oid), i.indisvalid AND i.indisready
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_index i ON i.indexrelid = c.oid
        WHERE n.nspname = 'public' AND c.relname = ANY(:names)
    """),
                {"names": list(indexes)},
            )
        )
        .tuples()
        .all()
    )
    for name, definition, ready in rows:
        # pg_get_indexdef() schema-qualifies the operator class when public is
        # not on the connection's search_path.
        if (
            definition.replace(" public.gin_trgm_ops)", " gin_trgm_ops)")
            != (indexes[name])
        ):
            raise LexicalSearchError(LexicalSearchErrorCode.CONFIGURATION_CHANGED)
        if ready is not True:
            raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
    if len(rows) != len(indexes):
        raise LexicalSearchError(LexicalSearchErrorCode.INDEXING)
