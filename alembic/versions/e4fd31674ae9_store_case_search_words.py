"""Store case word lists and a workspace vocabulary for typo search.

Revision ID: e4fd31674ae9
Revises: 5ca140c06ec2
Create Date: 2026-10-28 12:00:00.000000

A typo-tolerant clause matches a case when one whole word of its text has a
pg_trgm strict word similarity of at least 0.65 with the search term. Search
previously tested that on every case in scope, about 130 microseconds per row.
This revision stores the inputs to answer it from indexes instead:

1. Add nullable ``summary_words`` and ``description_words`` text[] columns
   (metadata-only). Each holds the distinct lowercase words of its source.
2. Create ``case_search_word``: every word seen in each workspace, with a
   trigram index and the standard workspace row-level security policy.
   Search finds the few vocabulary words within typo distance of the term,
   then keeps cases whose word list overlaps them.
3. Install a BEFORE INSERT/UPDATE trigger that recomputes a word list only when
   its source text changes and adds new words to the vocabulary.
4. Backfill existing rows in committed batches, then build GIN and
   NULL-fallback indexes concurrently.

A NULL word list means the source exceeds the search byte budget; search
evaluates those rows with the previous per-row check. The vocabulary is
insert-only and has no unique constraint, so concurrent case writes never wait
on each other's new words. A race can store a duplicate word, which search
tolerates. Words from deleted cases remain until their workspace is deleted.
Downgrade removes only this revision's objects and never modifies source text.
"""

import uuid
from collections.abc import Sequence
from typing import Literal

import sqlalchemy as sa

from alembic import op
from tracecat.db.tenant_rls import enable_workspace_table_rls, policy_name

revision: str = "e4fd31674ae9"
down_revision: str | None = "5ca140c06ec2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "case_search_word"
_WORDS_FUNCTION = "tracecat_case_words_v1"
_TRIGGER = "trg_case_search_words"
_TRIGGER_FUNCTION = "tracecat_case_search_words_v1"
_BACKFILL_BATCH = 1000
# Words are runs of letters and digits, the same split search used per row.
# Digit-only words share no trigram with a letters-only typo term, and a word
# with more than 50 distinct trigrams cannot reach 0.65 similarity with a term
# of at most 32 letters (33 trigrams / 0.65 < 51), so neither is stored.
# A longer word with few distinct trigrams (a repeated short pattern) could
# still match, but may exceed the B-tree and GIN entry limit (about 2.7 kB).
# Dropping it would lose matches, so its whole source gets the NULL fallback.
_WORDS_BODY = r"""
SELECT CASE WHEN octet_length(source_text) <= 8192 THEN (
    SELECT CASE WHEN bool_or(octet_length(w.word) > 1000) THEN NULL
        ELSE coalesce(array_agg(w.word ORDER BY w.word), '{}') END
    FROM (
        SELECT DISTINCT t.word
        FROM regexp_split_to_table(lower(source_text), '[^[:alnum:]]+') AS t(word)
        WHERE t.word ~ '[^0-9]' AND cardinality(public.show_trgm(t.word)) <= 50
    ) AS w
) END
""".strip()
_WORDS_FUNCTION_SQL = f"""CREATE FUNCTION public.{_WORDS_FUNCTION}(source_text text)
    RETURNS text[] LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    SET search_path = pg_catalog AS $case_words$\n{_WORDS_BODY}\n$case_words$"""
_TRIGGER_BODY = rf"""
DECLARE
    added text[] := '{{}}';
BEGIN
    IF TG_OP = 'INSERT' OR NEW.summary IS DISTINCT FROM OLD.summary THEN
        NEW.summary_words := public.{_WORDS_FUNCTION}(coalesce(NEW.summary::text, ''));
        added := added || coalesce(NEW.summary_words, '{{}}');
    END IF;
    IF TG_OP = 'INSERT' OR NEW.description IS DISTINCT FROM OLD.description THEN
        NEW.description_words := public.{_WORDS_FUNCTION}(coalesce(NEW.description::text, ''));
        added := added || coalesce(NEW.description_words, '{{}}');
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
        added := coalesce(NEW.summary_words, '{{}}') || coalesce(NEW.description_words, '{{}}');
    END IF;
    IF cardinality(added) > 0 THEN
        INSERT INTO public.{_TABLE} (workspace_id, word)
        SELECT DISTINCT NEW.workspace_id, a.word FROM unnest(added) AS a(word)
        WHERE NOT EXISTS (
            SELECT 1 FROM public.{_TABLE} v
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
_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER} BEFORE INSERT OR UPDATE "
    f'ON public."case" FOR EACH ROW EXECUTE FUNCTION public.{_TRIGGER_FUNCTION}()'
)
_TABLE_SQL = f"""CREATE TABLE public.{_TABLE} (
    id bigint GENERATED ALWAYS AS IDENTITY,
    workspace_id uuid NOT NULL,
    word text NOT NULL,
    CONSTRAINT pk_{_TABLE} PRIMARY KEY (id),
    CONSTRAINT fk_{_TABLE}_workspace_id_workspace FOREIGN KEY (workspace_id)
        REFERENCES public.workspace (id) ON DELETE CASCADE
)"""
# The table is new and empty, so its indexes are built in the transaction
# before the backfill needs them. Definitions use the unqualified operator
# class that pg_get_indexdef() reports when pg_trgm's schema is on the
# search_path.
_TABLE_INDEXES = (
    (
        f"ix_{_TABLE}_workspace_id_word",
        f"CREATE INDEX ix_{_TABLE}_workspace_id_word ON public.{_TABLE} USING btree (workspace_id, word)",
    ),
    (
        f"ix_{_TABLE}_word_trgm",
        f"CREATE INDEX ix_{_TABLE}_word_trgm ON public.{_TABLE} USING gin (word gin_trgm_ops)",
    ),
)
# Each definition is exactly what pg_get_indexdef() reports once it is built.
_WORD_INDEXES = (
    (
        "ix_case_summary_words",
        'CREATE INDEX ix_case_summary_words ON public."case" USING gin (summary_words)',
    ),
    (
        "ix_case_description_words",
        'CREATE INDEX ix_case_description_words ON public."case" USING gin (description_words)',
    ),
    (
        "ix_case_summary_words_fallback",
        'CREATE INDEX ix_case_summary_words_fallback ON public."case" USING btree (workspace_id, id) WHERE (summary_words IS NULL)',
    ),
    (
        "ix_case_description_words_fallback",
        'CREATE INDEX ix_case_description_words_fallback ON public."case" USING btree (workspace_id, id) WHERE (description_words IS NULL)',
    ),
)
_EXPECTED_TABLE_COLUMNS = {"id": "bigint", "workspace_id": "uuid", "word": "text"}


def _column_types(table: str, names: Sequence[str]) -> dict[str, str]:
    rows = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        WHERE a.attrelid = to_regclass(:table) AND a.attnum > 0
            AND NOT a.attisdropped AND a.attname = ANY(:names)
    """),
            {"table": table, "names": list(names)},
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


def _check_words_function(trgm_schema: str) -> bool:
    return _check_function(
        f"public.{_WORDS_FUNCTION}(text)",
        _in_trgm_schema(_WORDS_BODY, trgm_schema),
        "p.prorettype = 'text[]'::regtype AND l.lanname = 'sql' "
        "AND p.provolatile = 'i' AND p.proisstrict",
    )


def _check_trigger_function() -> bool:
    return _check_function(
        f"public.{_TRIGGER_FUNCTION}()",
        _TRIGGER_BODY,
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
        SELECT pg_get_indexdef(c.oid), i.indisvalid AND i.indisready
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
    qualified = f" {trgm_schema}.gin_trgm_ops)"
    if row[0].replace(qualified, " gin_trgm_ops)") != definition:
        raise RuntimeError(
            f"Index public.{name} has an incompatible existing definition ({row[0]}); "
            "preserve it and resolve the name collision before retrying."
        )
    return "ready" if row[1] else "invalid"


def _apply_vocabulary_policy() -> None:
    """Install the standard workspace policy, replacing any same-named one.

    The policy name belongs to this table, so a retry or an altered policy is
    reset to the standard definition rather than trusted by name. The shared
    helper names the table without a schema, so it runs on a transaction-local
    search_path that resolves it in public, restored afterwards. pg_catalog
    comes first and pg_temp last, so no writable or temporary object can shadow
    a name in the policy.
    """
    bind = op.get_bind()
    previous = bind.scalar(sa.text("SELECT current_setting('search_path')"))
    op.execute(f"DROP POLICY IF EXISTS {policy_name(_TABLE)} ON public.{_TABLE}")
    bind.execute(
        sa.text("SELECT set_config('search_path', 'pg_catalog, public, pg_temp', true)")
    )
    op.execute(enable_workspace_table_rls(_TABLE))
    bind.execute(
        sa.text("SELECT set_config('search_path', :previous, true)"),
        {"previous": previous},
    )


def _backfill() -> None:
    """Fill word lists and vocabulary in committed id-ordered batches.

    The trigger fires on these updates but keeps the assigned lists because the
    source text is unchanged, so the same statement adds their vocabulary. SET
    reads the updated row itself, so a row changed concurrently gets a list for
    its new text. Retries redo every row; NOT EXISTS skips stored words.
    """
    bind = op.get_bind()
    after = uuid.UUID(int=0)
    while True:
        # Advance by the selected ids, not the updated ones: a batch whose rows
        # were all deleted concurrently must not end the backfill early.
        last = bind.execute(
            sa.text(f"""
            WITH batch AS (
                SELECT id FROM public."case" WHERE id > :after ORDER BY id LIMIT :limit
            ), updated AS (
                UPDATE public."case" c SET
                    summary_words = public.{_WORDS_FUNCTION}(coalesce(c.summary::text, '')),
                    description_words = public.{_WORDS_FUNCTION}(coalesce(c.description::text, ''))
                FROM batch WHERE c.id = batch.id
                RETURNING c.id, c.workspace_id, c.summary_words, c.description_words
            ), words AS (
                SELECT DISTINCT u.workspace_id, w.word FROM updated u
                CROSS JOIN LATERAL unnest(
                    coalesce(u.summary_words, '{{}}') || coalesce(u.description_words, '{{}}')
                ) AS w(word)
            ), inserted AS (
                INSERT INTO public.{_TABLE} (workspace_id, word)
                SELECT x.workspace_id, x.word FROM words x
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.{_TABLE} v
                    WHERE v.workspace_id = x.workspace_id AND v.word = x.word
                )
            )
            SELECT id FROM batch ORDER BY id DESC LIMIT 1
        """),
            {"after": after, "limit": _BACKFILL_BATCH},
        ).scalar_one_or_none()
        if last is None:
            return
        after = last


def upgrade() -> None:
    trgm_schema = _trgm_schema()
    if trgm_schema is None or not op.get_bind().scalar(
        sa.text("SELECT to_regprocedure(:show_trgm) IS NOT NULL"),
        {"show_trgm": f"{trgm_schema}.show_trgm(text)"},
    ):
        raise RuntimeError("Case search word lists require pg_trgm.")
    # Inspect every object before changing anything.
    columns = _column_types("public.case", ("summary_words", "description_words"))
    if any(value != "text[]" for value in columns.values()):
        raise RuntimeError(
            f"public.case has incompatible search word columns ({columns}); "
            "preserve them and ask an operator to resolve the collision."
        )
    table_columns = _column_types(f"public.{_TABLE}", list(_EXPECTED_TABLE_COLUMNS))
    if table_columns and table_columns != _EXPECTED_TABLE_COLUMNS:
        raise RuntimeError(
            f"public.{_TABLE} has an incompatible definition ({table_columns}); "
            "preserve it and ask an operator to resolve the collision."
        )
    words_exists = _check_words_function(trgm_schema)
    function_exists = _check_trigger_function()
    trigger_exists = _check_trigger()
    states = {
        name: _index_state(name, definition, trgm_schema)
        for name, definition in (*_TABLE_INDEXES, *_WORD_INDEXES)
    }

    # Adding nullable columns is catalog-only, but ALTER TABLE and CREATE
    # TRIGGER still take brief exclusive locks on a hot table. Fail fast rather
    # than queue every case read and write behind a long-running transaction.
    # The upgrade is retry-safe.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(
        'ALTER TABLE public."case" '
        "ADD COLUMN IF NOT EXISTS summary_words text[], "
        "ADD COLUMN IF NOT EXISTS description_words text[]"
    )
    if not table_columns:
        op.execute(_TABLE_SQL)
    # The vocabulary copies case text, so it gets the same workspace isolation.
    _apply_vocabulary_policy()
    for name, definition in _TABLE_INDEXES:
        if states[name] == "invalid":
            op.execute(f'DROP INDEX IF EXISTS public."{name}"')
        op.execute(
            definition.replace(
                "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1
            ).replace(" gin_trgm_ops)", f" {trgm_schema}.gin_trgm_ops)")
        )
    if not words_exists:
        op.execute(_in_trgm_schema(_WORDS_FUNCTION_SQL, trgm_schema))
    if not function_exists:
        op.execute(_TRIGGER_FUNCTION_SQL)
    if not trigger_exists:
        op.execute(_TRIGGER_SQL)

    # The trigger commits before the backfill starts, so rows written during the
    # backfill are maintained by the trigger and rows written before it are
    # covered by a later batch.
    with op.get_context().autocommit_block():
        _backfill()
        for name, definition in _WORD_INDEXES:
            if states[name] == "invalid":
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
            op.execute(
                definition.replace(
                    "CREATE INDEX ", "CREATE INDEX CONCURRENTLY IF NOT EXISTS ", 1
                )
            )
            if _index_state(name, definition, trgm_schema) != "ready":
                raise RuntimeError(
                    f"Case search index public.{name} is not ready; retry the migration."
                )
    # Give the planner statistics for the new columns before the app uses them.
    op.execute('ANALYZE public."case"')
    op.execute(f"ANALYZE public.{_TABLE}")


def downgrade() -> None:
    # Called for their collision checks: never drop objects this revision
    # did not create.
    trgm_schema = _trgm_schema() or "public"
    _check_words_function(trgm_schema)
    _check_trigger_function()
    _check_trigger()
    for name, definition in _WORD_INDEXES:
        _index_state(name, definition, trgm_schema)
    with op.get_context().autocommit_block():
        for name, _ in reversed(_WORD_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER} ON public."case"')
    op.execute(f"DROP FUNCTION IF EXISTS public.{_TRIGGER_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_WORDS_FUNCTION}(text)")
    op.execute(f"DROP TABLE IF EXISTS public.{_TABLE}")
    op.execute(
        'ALTER TABLE public."case" '
        "DROP COLUMN IF EXISTS summary_words, DROP COLUMN IF EXISTS description_words"
    )
