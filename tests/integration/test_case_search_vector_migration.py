"""Stored case search vectors: migration backfill, trigger upkeep, and checks."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_case_text_search_postgres import (
    apply_vector_migration,
    seed_case,
)
from tests.integration.test_case_text_search_postgres import (
    search_database as _search_database,
)
from tracecat.cases.text_search_indexes import ensure_case_text_indexes
from tracecat.query.lexical_types import LexicalSearchError, LexicalSearchErrorCode

pytestmark = pytest.mark.integration
search_database = _search_database


def test_vector_migration_backfills_maintains_and_downgrades(
    search_database: Engine,
) -> None:
    apply_vector_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        existing = seed_case(conn, 1, "PowerShell encoded", "synthetic old write")
        oversized = seed_case(conn, 2, "synthetic large", "word " * 2000)
        for number in range(4, 9):
            seed_case(conn, number, f"synthetic batch {number}", "Credential access")
    # One-row batches exercise the keyset paging across every row.
    apply_vector_migration(search_database, backfill_batch=1)
    with search_database.begin() as conn:
        # Every stored vector equals the helper the expression indexes use.
        assert (
            conn.scalar(
                sa.text("""SELECT count(*) FROM "case" WHERE
                summary_tsv IS DISTINCT FROM public.tracecat_case_safe_vector_v1(coalesce(summary::text, ''))
                OR description_tsv IS DISTINCT FROM public.tracecat_case_safe_vector_v1(coalesce(description::text, ''))""")
            )
            == 0
        )
    with search_database.begin() as conn:
        conn.execute(
            sa.text("UPDATE \"case\" SET summary_tsv = 'stale' WHERE id=:id"),
            {"id": existing},
        )
    # A retry reuses every object and its backfill repairs stale vectors.
    apply_vector_migration(search_database)

    def vectors(conn: Connection, case_id: uuid.UUID) -> tuple[str | None, ...]:
        return (
            conn.execute(
                sa.text(
                    'SELECT summary_tsv::text, description_tsv::text FROM "case" WHERE id=:id'
                ),
                {"id": case_id},
            )
            .one()
            ._tuple()
        )

    with search_database.begin() as conn:
        assert vectors(conn, existing) == (
            "'encoded':2 'powershell':1",
            "'old':2 'synthetic':1 'write':3",
        )
        # Over the byte budget keeps the NULL fallback marker.
        assert vectors(conn, oversized)[1] is None
        inserted = seed_case(conn, 3, "Credential access")
        assert vectors(conn, inserted)[0] == "'access':2 'credential':1"

        conn.execute(
            sa.text("UPDATE \"case\" SET summary_tsv = 'marker' WHERE id=:id"),
            {"id": existing},
        )
        conn.execute(
            sa.text("UPDATE \"case\" SET status = 'CLOSED' WHERE id=:id"),
            {"id": existing},
        )
        conn.execute(
            sa.text('UPDATE "case" SET summary = summary WHERE id=:id'),
            {"id": existing},
        )
        # Writes that leave the source text unchanged do not recompute it.
        assert vectors(conn, existing)[0] == "'marker'"
        conn.execute(
            sa.text("UPDATE \"case\" SET summary = 'Lateral movement' WHERE id=:id"),
            {"id": existing},
        )
        assert vectors(conn, existing)[0] == "'lateral':1 'movement':2"

    apply_vector_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        assert conn.scalar(
            sa.text('SELECT summary FROM "case" WHERE id=:id'), {"id": existing}
        ) == ("Lateral movement")
        columns = set(
            conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'case'"
                )
            ).scalars()
        )
        assert not {"summary_tsv", "description_tsv"} & columns
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_case_search_vectors'"
                )
            )
            == 0
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "collision", ["disabled", "after", "function", "conditional", "security_definer"]
)
async def test_incompatible_vector_trigger_is_a_configuration_change(
    search_database: Engine, collision: str
) -> None:
    with search_database.begin() as conn:
        if collision == "disabled":
            conn.execute(
                sa.text('ALTER TABLE "case" DISABLE TRIGGER trg_case_search_vectors')
            )
        elif collision == "security_definer":
            conn.execute(
                sa.text(
                    "ALTER FUNCTION public.tracecat_case_search_vectors_v1() SECURITY DEFINER"
                )
            )
        elif collision == "conditional":
            conn.execute(sa.text('DROP TRIGGER trg_case_search_vectors ON "case"'))
            conn.execute(
                sa.text(
                    'CREATE TRIGGER trg_case_search_vectors BEFORE INSERT OR UPDATE ON "case" FOR EACH ROW WHEN (false) EXECUTE FUNCTION public.tracecat_case_search_vectors_v1()'
                )
            )
        else:
            conn.execute(sa.text('DROP TRIGGER trg_case_search_vectors ON "case"'))
            function = "public.tracecat_case_search_vectors_v1()"
            timing = "AFTER" if collision == "after" else "BEFORE"
            if collision == "function":
                conn.execute(
                    sa.text("""CREATE FUNCTION public.synthetic_trigger()
                        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$""")
                )
                function = "public.synthetic_trigger()"
            conn.execute(
                sa.text(
                    f'CREATE TRIGGER trg_case_search_vectors {timing} INSERT OR UPDATE ON "case" FOR EACH ROW EXECUTE FUNCTION {function}'
                )
            )
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with AsyncSession(engine) as session:
            with pytest.raises(LexicalSearchError) as error:
                await ensure_case_text_indexes(session)
            assert error.value.code == LexicalSearchErrorCode.CONFIGURATION_CHANGED
    finally:
        await engine.dispose()
    with pytest.raises(RuntimeError, match="resolve the collision"):
        apply_vector_migration(search_database)
