"""Stored case word lists: migration backfill, trigger upkeep, and downgrade."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.integration.test_case_text_search_postgres import (
    OTHER_WORKSPACE,
    WORKSPACE,
    apply_word_migration,
    search_ids,
    seed_case,
)
from tests.integration.test_case_text_search_postgres import (
    search_database as _search_database,
)

pytestmark = pytest.mark.integration
search_database = _search_database


def test_word_migration_backfills_maintains_and_downgrades(
    search_database: Engine,
) -> None:
    apply_word_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        existing = seed_case(conn, 1, "PowerShell encoded", "Ran 42 times; x86 host")
        oversized = seed_case(conn, 2, "synthetic large", "word " * 2000)
    # One-row batches exercise the loop and vocabulary dedup across commits.
    apply_word_migration(search_database, backfill_batch=1)
    # A retry reuses every object and backfills again without failing.
    apply_word_migration(search_database)

    def words(conn: Connection, case_id: uuid.UUID) -> tuple[list[str] | None, ...]:
        return (
            conn.execute(
                sa.text(
                    'SELECT summary_words, description_words FROM "case" WHERE id=:id'
                ),
                {"id": case_id},
            )
            .one()
            ._tuple()
        )

    def vocabulary(conn: Connection, workspace: uuid.UUID) -> list[str]:
        return list(
            conn.execute(
                sa.text(
                    "SELECT DISTINCT word FROM case_search_word WHERE workspace_id=:ws ORDER BY word"
                ),
                {"ws": workspace},
            ).scalars()
        )

    with search_database.begin() as conn:
        # Digit-only words cannot match a typo term, so they are not stored.
        assert words(conn, existing) == (
            ["encoded", "powershell"],
            ["host", "ran", "times", "x86"],
        )
        # Over the byte budget keeps the NULL fallback marker.
        assert words(conn, oversized) == (["large", "synthetic"], None)
        assert vocabulary(conn, WORKSPACE) == [
            "encoded",
            "host",
            "large",
            "powershell",
            "ran",
            "synthetic",
            "times",
            "x86",
        ]
        # A retried backfill does not duplicate stored words.
        assert conn.scalar(sa.text("SELECT count(*) FROM case_search_word")) == 8

        inserted = seed_case(conn, 3, "Credential access", workspace=OTHER_WORKSPACE)
        assert words(conn, inserted) == (["access", "credential"], [])
        assert vocabulary(conn, OTHER_WORKSPACE) == ["access", "credential"]

        conn.execute(
            sa.text("UPDATE \"case\" SET summary_words = '{marker}' WHERE id=:id"),
            {"id": existing},
        )
        conn.execute(
            sa.text("UPDATE \"case\" SET status = 'CLOSED' WHERE id=:id"),
            {"id": existing},
        )
        # Writes that leave the source text unchanged do not recompute it.
        assert words(conn, existing)[0] == ["marker"]
        conn.execute(
            sa.text("UPDATE \"case\" SET summary = 'Lateral movement' WHERE id=:id"),
            {"id": existing},
        )
        assert words(conn, existing)[0] == ["lateral", "movement"]
        assert {"lateral", "movement"} <= set(vocabulary(conn, WORKSPACE))
        # Moving a case adds its words to the destination vocabulary.
        conn.execute(
            sa.text('UPDATE "case" SET workspace_id = :ws WHERE id=:id'),
            {"ws": OTHER_WORKSPACE, "id": existing},
        )
        assert {"lateral", "movement", "host"} <= set(vocabulary(conn, OTHER_WORKSPACE))

    apply_word_migration(search_database, downgrade=True)
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
        assert not {"summary_words", "description_words"} & columns
        assert conn.scalar(sa.text("SELECT to_regclass('case_search_word')")) is None
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_case_search_words'"
                )
            )
            == 0
        )


def test_long_low_variety_word_falls_back_to_a_null_list(
    search_database: Engine,
) -> None:
    """A long word with few trigrams is never stored where it could overflow."""
    with search_database.begin() as conn:
        # 3,000 bytes but only five distinct trigrams, so it passes the
        # similarity filter and could otherwise reach an index entry.
        long_word = seed_case(conn, 1, "synthetic", "ab" * 1500 + " powershel")
        bounded = seed_case(conn, 2, "synthetic", "ab" * 500 + " powershel")
        lists = dict(
            conn.execute(sa.text('SELECT id, description_words FROM "case"'))
            .tuples()
            .all()
        )
        assert lists[long_word] is None
        assert lists[bounded] == ["ab" * 500, "powershel"]
        assert set(search_ids(conn, "powershell")) == {long_word, bounded}


def test_vocabulary_index_collision_fails_without_dropping_it(
    search_database: Engine,
) -> None:
    apply_word_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE case_search_word (id bigint GENERATED ALWAYS AS IDENTITY, workspace_id uuid NOT NULL, word text NOT NULL)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE INDEX ix_case_search_word_word_trgm ON case_search_word (word)"
            )
        )
    with pytest.raises(RuntimeError, match="incompatible existing definition"):
        apply_word_migration(search_database)
    with search_database.begin() as conn:
        assert conn.scalar(
            sa.text("SELECT pg_get_indexdef('ix_case_search_word_word_trgm'::regclass)")
        ) == (
            "CREATE INDEX ix_case_search_word_word_trgm ON public.case_search_word USING btree (word)"
        )


def test_vocabulary_is_workspace_isolated_for_a_non_owner_role(
    search_database: Engine,
) -> None:
    """The vocabulary copies case text, so it carries the workspace RLS policy."""
    with search_database.begin() as conn:
        seed_case(conn, 1, "alpha synthetic")
        seed_case(conn, 2, "bravo synthetic", workspace=OTHER_WORKSPACE)
    role = f"case_search_word_runtime_{uuid.uuid4().hex}"
    operator = sa.create_engine(search_database.url, poolclass=NullPool)
    runtime = sa.create_engine(
        search_database.url,
        poolclass=NullPool,
        connect_args={"options": f"-c role={role}"},
    )
    try:
        with operator.begin() as conn:
            conn.execute(sa.text(f'CREATE ROLE "{role}"'))
            conn.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            conn.execute(sa.text(f'GRANT SELECT, INSERT ON "case" TO "{role}"'))
            conn.execute(
                sa.text(f'GRANT SELECT, INSERT ON case_search_word TO "{role}"')
            )
        with runtime.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(WORKSPACE)},
            )
            # The trigger's vocabulary insert passes the policy's check.
            seed_case(conn, 3, "charlie synthetic")
            visible = set(
                conn.execute(sa.text("SELECT word FROM case_search_word")).scalars()
            )
            assert visible == {"alpha", "charlie", "synthetic"}
        with pytest.raises(DBAPIError, match="row-level security"):
            with runtime.begin() as conn:
                conn.execute(
                    sa.text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                    {"ws": str(WORKSPACE)},
                )
                seed_case(conn, 4, "delta synthetic", workspace=OTHER_WORKSPACE)
    finally:
        runtime.dispose()
        with operator.begin() as conn:
            conn.execute(sa.text(f'DROP OWNED BY "{role}"'))
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        operator.dispose()


def test_vocabulary_policy_is_reset_and_survives_a_restricted_search_path(
    search_database: Engine,
) -> None:
    """A same-named, altered policy is replaced; RLS is re-enabled on retry."""
    with search_database.begin() as conn:
        conn.execute(
            sa.text(
                "ALTER POLICY rls_policy_case_search_word ON case_search_word USING (true) WITH CHECK (true)"
            )
        )
        conn.execute(sa.text("ALTER TABLE case_search_word DISABLE ROW LEVEL SECURITY"))
    apply_word_migration(search_database, search_path="pg_catalog")
    with search_database.begin() as conn:
        enabled, using = conn.execute(
            sa.text("""
            SELECT c.relrowsecurity, p.qual FROM pg_class c
            JOIN pg_policies p ON p.tablename = c.relname
            WHERE c.oid = 'public.case_search_word'::regclass
                AND p.policyname = 'rls_policy_case_search_word'
        """)
        ).one()
        assert enabled is True
        assert "app.current_workspace_id" in using
