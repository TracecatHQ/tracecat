"""Store case search vectors in trigger-maintained columns.

Revision ID: 5ca140c06ec2
Revises: b7d41c9e2a6f
Create Date: 2026-10-27 12:00:00.000000

Search previously recomputed ``tracecat_case_safe_vector_v1`` for every
candidate row, several times per field while matching and ranking. This
revision stores each field's safe vector once:

1. Add nullable ``summary_tsv`` and ``description_tsv`` columns (metadata-only).
2. Install a BEFORE INSERT/UPDATE trigger that recomputes a vector only when its
   source text changes, so writes from old and new application versions stay
   current without application changes.
3. Backfill existing rows in committed batches. The backfill writes only the
   vector columns, so the trigger leaves them as written and ``updated_at`` is
   untouched.
4. Build GIN and NULL-fallback indexes concurrently after the backfill.

A NULL vector keeps the meaning it has today: the source is over budget or has
an unsafe token, and search rechecks the complete source text. The expression
indexes from e2018a1b2c3d remain for the previous application version; a later
revision can drop them. Downgrade removes only this revision's objects and
never modifies source text.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5ca140c06ec2"
down_revision: str | None = "b7d41c9e2a6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER = "trg_case_search_vectors"
_TRIGGER_FUNCTION = "tracecat_case_search_vectors_v1"
_BACKFILL_BATCH = 1000
_TRIGGER_BODY = r"""
BEGIN
    IF TG_OP = 'INSERT' OR NEW.summary IS DISTINCT FROM OLD.summary THEN
        NEW.summary_tsv := public.tracecat_case_safe_vector_v1(coalesce(NEW.summary::text, ''));
    END IF;
    IF TG_OP = 'INSERT' OR NEW.description IS DISTINCT FROM OLD.description THEN
        NEW.description_tsv := public.tracecat_case_safe_vector_v1(coalesce(NEW.description::text, ''));
    END IF;
    RETURN NEW;
END
""".strip()
_TRIGGER_FUNCTION_SQL = f"""CREATE FUNCTION public.{_TRIGGER_FUNCTION}()
    RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog AS $case_search$\n{_TRIGGER_BODY}\n$case_search$"""
# No "UPDATE OF summary, description" column list: that would make the trigger
# depend on those columns and block a later ALTER COLUMN ... TYPE. The body's
# IS DISTINCT FROM checks skip recomputation when the text did not change.
_TRIGGER_SQL = (
    f"CREATE TRIGGER {_TRIGGER} BEFORE INSERT OR UPDATE "
    f'ON public."case" FOR EACH ROW EXECUTE FUNCTION public.{_TRIGGER_FUNCTION}()'
)
# Each definition is exactly what pg_get_indexdef() reports once it is built.
_VECTOR_INDEXES = (
    (
        "ix_case_summary_tsv",
        'CREATE INDEX ix_case_summary_tsv ON public."case" USING gin (summary_tsv)',
    ),
    (
        "ix_case_description_tsv",
        'CREATE INDEX ix_case_description_tsv ON public."case" USING gin (description_tsv)',
    ),
    (
        "ix_case_summary_tsv_fallback",
        'CREATE INDEX ix_case_summary_tsv_fallback ON public."case" USING btree (workspace_id, id) WHERE (summary_tsv IS NULL)',
    ),
    (
        "ix_case_description_tsv_fallback",
        'CREATE INDEX ix_case_description_tsv_fallback ON public."case" USING btree (workspace_id, id) WHERE (description_tsv IS NULL)',
    ),
)


def _column_types() -> dict[str, str]:
    rows = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        WHERE a.attrelid = 'public.case'::regclass AND NOT a.attisdropped
            AND a.attname IN ('summary_tsv', 'description_tsv')
    """)
        )
        .tuples()
        .all()
    )
    return dict(rows)


def _trigger_function_exists() -> bool:
    """Reuse this revision's function on retry; never replace another one."""
    source = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT p.prosrc FROM pg_proc p WHERE p.oid = to_regprocedure(:signature)"
            ),
            {"signature": f"public.{_TRIGGER_FUNCTION}()"},
        )
        .scalar_one_or_none()
    )
    if source is None:
        return False
    if source.strip() != _TRIGGER_BODY:
        raise RuntimeError(
            f"public.{_TRIGGER_FUNCTION}() has an incompatible existing definition; "
            "preserve it and ask an operator to resolve the collision."
        )
    return True


def _trigger_exists() -> bool:
    function_oid = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT t.tgfoid = to_regprocedure(:signature)
        FROM pg_trigger t
        WHERE t.tgrelid = 'public.case'::regclass AND t.tgname = :name
    """),
            {"signature": f"public.{_TRIGGER_FUNCTION}()", "name": _TRIGGER},
        )
        .scalar_one_or_none()
    )
    if function_oid is None:
        return False
    if function_oid is not True:
        raise RuntimeError(
            f"Trigger {_TRIGGER} on public.case calls an unexpected function; "
            "preserve it and ask an operator to resolve the collision."
        )
    return True


def _index_state(name: str, definition: str) -> bool | None:
    """Return whether this revision's index is ready, or None when it is absent."""
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
        return None
    if row[0] != definition:
        raise RuntimeError(
            f"Index public.{name} has an incompatible existing definition ({row[0]}); "
            "preserve it and resolve the name collision before retrying."
        )
    return bool(row[1])


def _backfill() -> None:
    """Fill vectors in committed id-ordered batches; retries redo every row.

    The trigger fires on these updates but keeps the assigned vectors because
    the source text is unchanged.
    """
    bind = op.get_bind()
    after = uuid.UUID(int=0)
    while True:
        ids = (
            bind.execute(
                sa.text("""
            WITH batch AS (
                SELECT id FROM public."case" WHERE id > :after ORDER BY id LIMIT :limit
            )
            UPDATE public."case" c SET
                summary_tsv = public.tracecat_case_safe_vector_v1(coalesce(c.summary::text, '')),
                description_tsv = public.tracecat_case_safe_vector_v1(coalesce(c.description::text, ''))
            FROM batch WHERE c.id = batch.id
            RETURNING c.id
        """),
                {"after": after, "limit": _BACKFILL_BATCH},
            )
            .scalars()
            .all()
        )
        if not ids:
            return
        # Python UUID ordering matches PostgreSQL's byte-wise uuid ordering.
        after = max(ids)


def upgrade() -> None:
    if not op.get_bind().scalar(
        sa.text(
            "SELECT to_regprocedure('public.tracecat_case_safe_vector_v1(text)') IS NOT NULL"
        )
    ):
        raise RuntimeError(
            "Stored case search vectors require public.tracecat_case_safe_vector_v1 "
            "from revision e2018a1b2c3d."
        )
    # Inspect every object before changing anything.
    columns = _column_types()
    if any(value != "tsvector" for value in columns.values()):
        raise RuntimeError(
            f"public.case has incompatible search vector columns ({columns}); "
            "preserve them and ask an operator to resolve the collision."
        )
    function_exists = _trigger_function_exists()
    trigger_exists = _trigger_exists()
    states = {
        name: _index_state(name, definition) for name, definition in _VECTOR_INDEXES
    }

    op.execute(
        'ALTER TABLE public."case" '
        "ADD COLUMN IF NOT EXISTS summary_tsv tsvector, "
        "ADD COLUMN IF NOT EXISTS description_tsv tsvector"
    )
    if not function_exists:
        op.execute(_TRIGGER_FUNCTION_SQL)
    if not trigger_exists:
        op.execute(_TRIGGER_SQL)

    # The trigger commits before the backfill starts, so rows written during the
    # backfill are maintained by the trigger and rows written before it are
    # covered by a later batch.
    with op.get_context().autocommit_block():
        _backfill()
        for name, definition in _VECTOR_INDEXES:
            if states[name] is False:
                op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
            op.execute(
                definition.replace(
                    "CREATE INDEX ", "CREATE INDEX CONCURRENTLY IF NOT EXISTS ", 1
                )
            )
            if _index_state(name, definition) is not True:
                raise RuntimeError(
                    f"Case search index public.{name} is not ready; retry the migration."
                )
    # Give the planner statistics for the new columns before the app uses them.
    op.execute('ANALYZE public."case"')


def downgrade() -> None:
    _trigger_function_exists()
    _trigger_exists()
    for name, definition in _VECTOR_INDEXES:
        _index_state(name, definition)
    with op.get_context().autocommit_block():
        for name, _ in reversed(_VECTOR_INDEXES):
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"')
    op.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER} ON public."case"')
    op.execute(f"DROP FUNCTION IF EXISTS public.{_TRIGGER_FUNCTION}()")
    op.execute(
        'ALTER TABLE public."case" '
        "DROP COLUMN IF EXISTS summary_tsv, DROP COLUMN IF EXISTS description_tsv"
    )
