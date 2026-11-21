"""Index case search text over the 8,192-byte budget.

Revision ID: 67d28dfaff15
Revises: 42ffaf47af2e
Create Date: 2026-11-18 12:00:00.000000

A summary or description over 8,192 bytes has a NULL stored vector and a NULL
word list (revisions 5ca140c06ec2 and e4fd31674ae9), so every full-text search
re-parsed each of those rows in SQL: about 1.7 ms per row at 9 KB, for every
keyword, including ones with no matches. Two thousand such rows made every
search take over ten seconds.

This revision indexes those rows without changing what the existing columns
mean, so the previous application version keeps working against it:

1. Add nullable ``summary_lexemes`` and ``description_lexemes`` tsvector
   columns (metadata-only). Each holds the field's distinct words without
   positions, only for a field over the byte budget. The words are exactly the
   ones ``tracecat_case_text_matches_v1`` compares, so an indexed word match
   gives the same answer as the per-row check. Phrases still need positions:
   search uses this column to find candidates, then checks the phrase per row.
2. Store the complete word list in ``summary_words``/``description_words`` for
   a field over the byte budget instead of NULL, and add its words to the
   workspace vocabulary. The previous version already treats a non-NULL list
   as complete, so its typo search stays correct.
3. Install ``trg_case_search_words_oversized``, a BEFORE INSERT/UPDATE trigger
   that maintains both for fields over the budget. PostgreSQL fires BEFORE
   triggers in name order, so it runs after ``trg_case_search_vectors`` and
   ``trg_case_search_words``, which still set NULL for those fields. Their
   functions are unchanged, so the previous version's readiness checks pass.
4. Backfill existing over-budget rows in committed batches, then build GIN
   indexes and partial fallback indexes concurrently.
5. Build partial trigram indexes over the complete lowercase text of
   NULL-vector sources, so literal and legacy substring search stop reading
   every long source in full.

The ``summary_tsv``/``description_tsv`` columns keep their meaning: NULL still
marks a field that literal search must read in full. A field stays on the
per-row fallback only when its lexemes are also NULL (an unsafe token below
the budget, or more distinct words than a tsvector holds).

The trigger inlines its SQL rather than calling helper functions, so case
writes need no new EXECUTE grant on installs that revoke it from PUBLIC.

Downgrade removes this revision's columns, trigger, function and indexes.
Word lists and vocabulary words it added stay: they are complete, so the
previous version reads them correctly. It never modifies source text.
"""

import uuid
from collections.abc import Sequence
from typing import Literal

import sqlalchemy as sa

from alembic import op

revision: str = "67d28dfaff15"
down_revision: str | None = "42ffaf47af2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER = "trg_case_search_words_oversized"
_TRIGGER_FUNCTION = "tracecat_case_search_oversized_v1"
_BACKFILL_BATCH = 500
_DOCUMENT_BYTES = 8192
# The SQL below is inlined into the trigger and the backfill instead of living
# in helper functions. PostgreSQL checks EXECUTE on a trigger function only when
# the trigger is created, so case writes need no new grants: a runtime role on
# an install that revokes EXECUTE from PUBLIC keeps writing cases.
#
# Position-free words of an over-budget source. The token rules are those of
# tracecat_case_text_matches_v1: raw tokens of 2,047 bytes or more are skipped,
# and a token must map to exactly one lexeme under 2,047 bytes, so an indexed
# word match gives the same answer as that per-row check. array_to_tsvector
# does not enforce the tsvector string limit: past 1 MB of lexeme text its
# 20-bit offsets overflow and the vector silently stops matching. A source with
# more distinct word text than that gets NULL and keeps the per-row check.
_LEXEMES_SQL = r"""(SELECT CASE WHEN coalesce(sum(octet_length(d.lexeme)), 0) <= 1048575
        THEN pg_catalog.array_to_tsvector(coalesce(array_agg(d.lexeme), '{{}}')) END
    FROM (
        SELECT DISTINCT l.lexemes[1] AS lexeme
        FROM pg_catalog.ts_parse('pg_catalog.default', {source}) p
        JOIN pg_catalog.pg_ts_config_map m
            ON m.mapcfg = 'pg_catalog.simple'::regconfig AND m.maptokentype = p.tokid
        CROSS JOIN LATERAL (
            SELECT pg_catalog.ts_lexize(m.mapdict::regdictionary, p.token) AS lexemes
        ) l
        WHERE octet_length(p.token) < 2047
            AND cardinality(l.lexemes) = 1 AND octet_length(l.lexemes[1]) < 2047
    ) AS d)"""
# The body of tracecat_case_words_v1 without its 8,192-byte cutoff. The same
# rules keep the list within what typo search can match: digit-only words and
# words with more than 50 distinct trigrams are omitted, and a remaining word
# over 1,000 bytes (which could exceed a GIN entry) gives NULL so the field
# keeps its per-row typo check.
_WORDS_SQL = r"""(SELECT CASE WHEN bool_or(octet_length(w.word) > 1000) THEN NULL
        ELSE coalesce(array_agg(w.word ORDER BY w.word), '{{}}') END
    FROM (
        SELECT d.word FROM (
            SELECT DISTINCT t.word
            FROM regexp_split_to_table(lower({source}), '[^[:alnum:]]+') AS t(word)
            WHERE t.word ~ '[^0-9]'
        ) AS d
        WHERE cardinality(public.show_trgm(d.word)) <= 50
    ) AS w)"""


def _lexemes(source: str) -> str:
    return _LEXEMES_SQL.format(source=source)


def _words(source: str) -> str:
    return _WORDS_SQL.format(source=source)


def _field_trigger_sql(field: str) -> str:
    source = f"coalesce(NEW.{field}::text, '')"
    return f"""    IF TG_OP = 'INSERT' OR NEW.{field} IS DISTINCT FROM OLD.{field} THEN
        IF octet_length({source}) > {_DOCUMENT_BYTES} THEN
            NEW.{field}_lexemes := {_lexemes(source)};
            NEW.{field}_words := {_words(source)};
            added := added || coalesce(NEW.{field}_words, '{{}}');
        ELSE
            NEW.{field}_lexemes := NULL;
        END IF;
    END IF;"""


# Within the budget the earlier triggers already stored a positional vector and
# word list, so this one only clears stale lexemes. The vocabulary insert is
# the one trg_case_search_words uses.
_TRIGGER_BODY = f"""
DECLARE
    added text[] := '{{}}';
BEGIN
{_field_trigger_sql("summary")}
{_field_trigger_sql("description")}
    IF cardinality(added) > 0 THEN
        INSERT INTO public.case_search_word (workspace_id, word)
        SELECT DISTINCT NEW.workspace_id, a.word FROM unnest(added) AS a(word)
        WHERE NOT EXISTS (
            SELECT 1 FROM public.case_search_word v
            WHERE v.workspace_id = NEW.workspace_id AND v.word = a.word
        );
    END IF;
    RETURN NEW;
END
""".strip()
_TRIGGER_FUNCTION_SQL = f"""CREATE FUNCTION public.{_TRIGGER_FUNCTION}()
    RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog AS $case_search$\n{_TRIGGER_BODY}\n$case_search$"""
# No "UPDATE OF" column list, for the same reason as trg_case_search_vectors.
# The name must sort after trg_case_search_words: see the module docstring.
_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER} BEFORE INSERT OR UPDATE "
    f'ON public."case" FOR EACH ROW EXECUTE FUNCTION public.{_TRIGGER_FUNCTION}()'
)
# Each definition is exactly what pg_get_indexdef() reports once it is built.
_INDEXES = (
    (
        "ix_case_summary_lexemes",
        'CREATE INDEX ix_case_summary_lexemes ON public."case" USING gin (summary_lexemes)',
    ),
    (
        "ix_case_description_lexemes",
        'CREATE INDEX ix_case_description_lexemes ON public."case" USING gin (description_lexemes)',
    ),
    (
        "ix_case_summary_lexemes_fallback",
        'CREATE INDEX ix_case_summary_lexemes_fallback ON public."case" USING btree (workspace_id, id) WHERE ((summary_tsv IS NULL) AND (summary_lexemes IS NULL))',
    ),
    (
        "ix_case_description_lexemes_fallback",
        'CREATE INDEX ix_case_description_lexemes_fallback ON public."case" USING btree (workspace_id, id) WHERE ((description_tsv IS NULL) AND (description_lexemes IS NULL))',
    ),
    # Literal and legacy substring search read a NULL-vector source in full;
    # these index its complete lowercase text, as ix_case_*_trgm does below the
    # budget. The operator class is unqualified, as pg_get_indexdef() reports
    # it when pg_trgm's schema is on the search_path.
    (
        "ix_case_summary_trgm_fallback",
        "CREATE INDEX ix_case_summary_trgm_fallback ON public.\"case\" USING gin (lower(COALESCE((summary)::text, ''::text)) gin_trgm_ops) WHERE (summary_tsv IS NULL)",
    ),
    (
        "ix_case_description_trgm_fallback",
        "CREATE INDEX ix_case_description_trgm_fallback ON public.\"case\" USING gin (lower(COALESCE((description)::text, ''::text)) gin_trgm_ops) WHERE (description_tsv IS NULL)",
    ),
)


def _column_types() -> dict[str, str]:
    rows = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        WHERE a.attrelid = 'public.case'::regclass AND a.attnum > 0
            AND NOT a.attisdropped
            AND a.attname IN ('summary_lexemes', 'description_lexemes')
    """)
        )
        .tuples()
        .all()
    )
    return dict(rows)


def _check_function(signature: str, body: str, attributes: str) -> bool:
    """Return whether this revision's function exists; raise on a collision.

    A retry reuses the function; any other same-named function is preserved.
    ``attributes`` is a SQL condition over ``p`` (pg_proc) and ``l`` (pg_language).
    """
    row = (
        op.get_bind()
        .execute(
            sa.text(f"""
        SELECT p.prosrc, {attributes}
            AND NOT p.prosecdef AND p.prokind = 'f'
            AND p.proconfig = ARRAY['search_path=pg_catalog']::text[]
        FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.oid = to_regprocedure(:signature)
    """),
            {"signature": signature},
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        return False
    source, attributes_match = row
    if source.strip() != body or attributes_match is not True:
        raise RuntimeError(
            f"{signature} has an incompatible existing definition; "
            "preserve it and ask an operator to resolve the collision."
        )
    return True


def _trgm_schema() -> str | None:
    """Return pg_trgm's schema as SQL: public, or ibm_extension on IBM Cloud."""
    return op.get_bind().scalar(
        sa.text(
            "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'"
        )
    )


def _in_trgm_schema(sql: str, trgm_schema: str) -> str:
    """Point this revision's pg_trgm calls, written for public, at its schema."""
    return sql.replace("public.show_trgm(", f"{trgm_schema}.show_trgm(")


def _check_trigger_function(trgm_schema: str) -> bool:
    return _check_function(
        f"public.{_TRIGGER_FUNCTION}()",
        _in_trgm_schema(_TRIGGER_BODY, trgm_schema),
        "p.prorettype = 'trigger'::regtype AND l.lanname = 'plpgsql'",
    )


def _check_trigger() -> bool:
    """Return whether this revision's trigger exists; raise on a collision.

    Only an enabled, unconditional BEFORE INSERT OR UPDATE row trigger on our
    function is reused.
    """
    compatible = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT t.tgfoid = to_regprocedure(:signature)
            AND t.tgenabled IN ('O', 'A')
            -- pg_trigger.tgtype bits: ROW (1) | BEFORE (2) | INSERT (4) | UPDATE (16).
            AND t.tgtype = 23
            AND cardinality(t.tgattr::smallint[]) = 0
            AND t.tgqual IS NULL
        FROM pg_trigger t
        WHERE t.tgrelid = 'public.case'::regclass AND t.tgname = :name
    """),
            {"signature": f"public.{_TRIGGER_FUNCTION}()", "name": _TRIGGER},
        )
        .scalar_one_or_none()
    )
    if compatible is None:
        return False
    if compatible is not True:
        raise RuntimeError(
            f"Trigger {_TRIGGER} on public.case is disabled or has an unexpected "
            "function, timing, event, column list, or WHEN condition; preserve it "
            "and ask an operator to resolve the collision."
        )
    return True


type _IndexState = Literal["absent", "invalid", "ready"]


def _index_state(name: str, definition: str, trgm_schema: str) -> _IndexState:
    """Return this revision's index state; raise on a same-named collision."""
    row = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT pg_get_indexdef(c.oid), i.indisvalid AND i.indisready,
            -- The definition omits a visible opclass's schema, so a same-named
            -- lookalike earlier on search_path would render identically.
            NOT EXISTS (
                SELECT 1 FROM unnest(i.indclass::oid[]) AS k(opclass)
                JOIN pg_opclass o ON o.oid = k.opclass
                WHERE o.opcname = 'gin_trgm_ops' AND o.opcnamespace IS DISTINCT FROM (
                    SELECT extnamespace FROM pg_extension WHERE extname = 'pg_trgm'
                )
            )
        FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid
        WHERE c.oid = to_regclass(:name)
    """),
            {"name": f"public.{name}"},
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        return "absent"
    # The operator class is schema-qualified when its schema is not on search_path.
    definition_matches = (
        row[0].replace(f" {trgm_schema}.gin_trgm_ops)", " gin_trgm_ops)") == definition
    )
    if not definition_matches or not row[2]:
        raise RuntimeError(
            f"Index public.{name} has an incompatible existing definition ({row[0]}); "
            "preserve it and resolve the name collision before retrying."
        )
    return "ready" if row[1] else "invalid"


def _backfill(trgm_schema: str) -> None:
    """Index over-budget fields in committed id-ordered batches.

    Every over-budget field has a NULL stored vector, so each batch reads the
    two small NULL-vector partial indexes instead of the whole table. The
    trigger fires on these updates but keeps the assigned values because the
    source text is unchanged, so the same statement adds their vocabulary. SET
    reads the updated row itself, so a row changed concurrently is indexed
    from its new text. Retries redo every row; NOT EXISTS skips stored words.
    """
    summary = "coalesce(c.summary::text, '')"
    description = "coalesce(c.description::text, '')"
    statement = sa.text(
        _in_trgm_schema(
            f"""
            WITH batch AS (
                SELECT id FROM public."case"
                WHERE (summary_tsv IS NULL OR description_tsv IS NULL) AND id > :after
                    AND (octet_length(coalesce(summary::text, '')) > {_DOCUMENT_BYTES}
                        OR octet_length(coalesce(description::text, '')) > {_DOCUMENT_BYTES})
                ORDER BY id LIMIT :limit
            ), updated AS (
                UPDATE public."case" c SET
                    summary_lexemes = CASE WHEN octet_length({summary}) > {_DOCUMENT_BYTES}
                        THEN {_lexemes(summary)} END,
                    description_lexemes = CASE WHEN octet_length({description}) > {_DOCUMENT_BYTES}
                        THEN {_lexemes(description)} END,
                    summary_words = CASE WHEN octet_length({summary}) > {_DOCUMENT_BYTES}
                        THEN {_words(summary)} ELSE c.summary_words END,
                    description_words = CASE WHEN octet_length({description}) > {_DOCUMENT_BYTES}
                        THEN {_words(description)} ELSE c.description_words END
                FROM batch WHERE c.id = batch.id
                RETURNING c.workspace_id, c.summary_words, c.description_words
            ), words AS (
                SELECT DISTINCT u.workspace_id, w.word FROM updated u
                CROSS JOIN LATERAL unnest(
                    coalesce(u.summary_words, '{{}}') || coalesce(u.description_words, '{{}}')
                ) AS w(word)
            ), inserted AS (
                INSERT INTO public.case_search_word (workspace_id, word)
                SELECT x.workspace_id, x.word FROM words x
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.case_search_word v
                    WHERE v.workspace_id = x.workspace_id AND v.word = x.word
                )
            )
            SELECT id FROM batch ORDER BY id DESC LIMIT 1
        """,
            trgm_schema,
        )
    )
    bind = op.get_bind()
    after = uuid.UUID(int=0)
    while True:
        # Advance by the selected ids, not the updated ones: a batch whose rows
        # were all deleted concurrently must not end the backfill early.
        last = bind.execute(
            statement, {"after": after, "limit": _BACKFILL_BATCH}
        ).scalar_one_or_none()
        if last is None:
            return
        after = last


def upgrade() -> None:
    bind = op.get_bind()
    trgm_schema = _trgm_schema()
    if trgm_schema is None or not bind.scalar(
        sa.text("SELECT to_regprocedure(:show_trgm) IS NOT NULL"),
        {"show_trgm": f"{trgm_schema}.show_trgm(text)"},
    ):
        raise RuntimeError("Indexing over-budget case text requires pg_trgm.")
    if not bind.scalar(
        sa.text("SELECT to_regclass('public.case_search_word') IS NOT NULL")
    ):
        raise RuntimeError(
            "Indexing over-budget case text requires public.case_search_word "
            "from revision e4fd31674ae9."
        )
    # Inspect every object before changing anything.
    columns = _column_types()
    if any(value != "tsvector" for value in columns.values()):
        raise RuntimeError(
            f"public.case has incompatible search lexeme columns ({columns}); "
            "preserve them and ask an operator to resolve the collision."
        )
    function_exists = _check_trigger_function(trgm_schema)
    trigger_exists = _check_trigger()
    states = {
        name: _index_state(name, definition, trgm_schema)
        for name, definition in _INDEXES
    }

    # Adding nullable columns is catalog-only, but ALTER TABLE and CREATE
    # TRIGGER still take brief exclusive locks on a hot table. Fail fast rather
    # than queue every case read and write behind a long-running transaction.
    # The upgrade is retry-safe.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        'ALTER TABLE public."case" '
        "ADD COLUMN IF NOT EXISTS summary_lexemes tsvector, "
        "ADD COLUMN IF NOT EXISTS description_lexemes tsvector"
    )
    if not function_exists:
        op.execute(_in_trgm_schema(_TRIGGER_FUNCTION_SQL, trgm_schema))
    if not trigger_exists:
        op.execute(_TRIGGER_SQL)

    # The trigger commits before the backfill starts, so rows written during the
    # backfill are maintained by the trigger and rows written before it are
    # covered by a later batch.
    with op.get_context().autocommit_block():
        _backfill(trgm_schema)
        for name, definition in _INDEXES:
            if states[name] == "invalid":
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
            op.execute(
                definition.replace(
                    "CREATE INDEX ", "CREATE INDEX CONCURRENTLY IF NOT EXISTS ", 1
                ).replace(" gin_trgm_ops)", f" {trgm_schema}.gin_trgm_ops)")
            )
            if _index_state(name, definition, trgm_schema) != "ready":
                raise RuntimeError(
                    f"Case search index public.{name} is not ready; retry the migration."
                )
    # Give the planner statistics for the new columns before the app uses them.
    op.execute('ANALYZE public."case"')
    op.execute("ANALYZE public.case_search_word")


def downgrade() -> None:
    # Called for their collision checks: never drop objects this revision
    # did not create.
    trgm_schema = _trgm_schema() or "public"
    _check_trigger_function(trgm_schema)
    _check_trigger()
    for name, definition in _INDEXES:
        _index_state(name, definition, trgm_schema)
    with op.get_context().autocommit_block():
        for name, _ in reversed(_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER} ON public."case"')
    op.execute(f"DROP FUNCTION IF EXISTS public.{_TRIGGER_FUNCTION}()")
    op.execute(
        'ALTER TABLE public."case" '
        "DROP COLUMN IF EXISTS summary_lexemes, DROP COLUMN IF EXISTS description_lexemes"
    )
