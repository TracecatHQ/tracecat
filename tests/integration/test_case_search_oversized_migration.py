"""Over-budget case text: lexeme backfill, trigger upkeep, matching, downgrade."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from tests.integration.test_case_text_search_postgres import (
    OTHER_WORKSPACE,
    WORKSPACE,
    apply_oversized_migration,
    parse_fixture_query,
    search_ids,
    seed_case,
)
from tests.integration.test_case_text_search_postgres import (
    search_database as _search_database,
)
from tracecat.cases.text_search import build_case_text_expressions
from tracecat.db.models import Case

pytestmark = pytest.mark.integration
search_database = _search_database

# Over the 8,192-byte budget, so the positional vector and v1 word list are NULL.
FILLER = "routine telemetry line " * 400


def _stored(conn: Connection, case_id: uuid.UUID) -> sa.Row[tuple[object, ...]]:
    return conn.execute(
        sa.text(
            'SELECT description_tsv IS NULL AS no_vector, description_lexemes, description_words FROM "case" WHERE id=:id'
        ),
        {"id": case_id},
    ).one()


def _vocabulary(conn: Connection, workspace: uuid.UUID) -> set[str]:
    return set(
        conn.execute(
            sa.text("SELECT word FROM case_search_word WHERE workspace_id=:ws"),
            {"ws": workspace},
        ).scalars()
    )


def test_oversized_migration_backfills_maintains_and_downgrades(
    search_database: Engine,
) -> None:
    apply_oversized_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        large = seed_case(
            conn,
            1,
            "synthetic large",
            FILLER + "alpha beta gamma powershel attachment invoice-17.pdf",
        )
        small = seed_case(conn, 2, "synthetic small", "alpha only")
    # One-row batches exercise the loop; a retry reuses every object.
    apply_oversized_migration(search_database, backfill_batch=1)
    apply_oversized_migration(search_database)

    with search_database.begin() as conn:
        stored = _stored(conn, large)
        assert stored.no_vector is True
        assert stored.description_lexemes is not None
        assert {"gamma", "invoice-17.pdf"} <= set(
            conn.execute(
                sa.text(
                    'SELECT unnest(tsvector_to_array(description_lexemes)) FROM "case" WHERE id=:id'
                ),
                {"id": large},
            ).scalars()
        )
        assert stored.description_words is not None
        assert {"gamma", "invoice", "powershel"} <= set(stored.description_words)
        assert {"gamma", "powershel"} <= _vocabulary(conn, WORKSPACE)
        assert _stored(conn, small).description_lexemes is None

        # Words, phrases, typos and literals all find the over-budget case.
        assert search_ids(conn, "gamma") == [large]
        assert search_ids(conn, "alpha gamma") == [large]
        assert search_ids(conn, '"beta gamma"') == [large]
        assert search_ids(conn, '"gamma beta"') == []
        assert search_ids(conn, "invoice-17.pdf") == [large]
        assert search_ids(conn, "powershell") == [large]
        assert search_ids(conn, "literal:ce-17.p") == [large]
        assert set(search_ids(conn, "alpha")) == {large, small}

        # Shrinking below the budget clears the lexemes; the positional vector
        # and bounded word list take over.
        conn.execute(
            sa.text("UPDATE \"case\" SET description = 'gamma short' WHERE id=:id"),
            {"id": large},
        )
        shrunk = _stored(conn, large)
        assert (shrunk.no_vector, shrunk.description_lexemes) == (False, None)
        assert shrunk.description_words == ["gamma", "short"]
        assert search_ids(conn, "gamma") == [large]
        # Growing again restores them, and new words reach the vocabulary.
        conn.execute(
            sa.text('UPDATE "case" SET description = :text WHERE id=:id'),
            {"text": FILLER + "zeta", "id": large},
        )
        grown = _stored(conn, large)
        assert grown.no_vector is True and grown.description_lexemes is not None
        assert "zeta" in _vocabulary(conn, WORKSPACE)
        assert search_ids(conn, "zeta") == [large]
        # A write that leaves the text unchanged does not recompute it.
        conn.execute(
            sa.text("UPDATE \"case\" SET status = 'CLOSED' WHERE id=:id"),
            {"id": large},
        )
        assert _stored(conn, large).description_lexemes == grown.description_lexemes
        # New over-budget cases are indexed by the trigger, per workspace.
        other = seed_case(
            conn, 3, "synthetic other", FILLER + "omega", workspace=OTHER_WORKSPACE
        )
        assert _stored(conn, other).description_lexemes is not None
        assert "omega" in _vocabulary(conn, OTHER_WORKSPACE)
        assert "omega" not in _vocabulary(conn, WORKSPACE)
        assert search_ids(conn, "omega", workspace=OTHER_WORKSPACE) == [other]

    apply_oversized_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        columns = set(
            conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'case'"
                )
            ).scalars()
        )
        assert not {"summary_lexemes", "description_lexemes"} & columns
        assert (
            conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_case_search_words_oversized'"
                )
            )
            == 0
        )
        # The complete word list stays; the previous version reads it as is.
        assert "zeta" in conn.scalar(
            sa.text('SELECT description_words FROM "case" WHERE id=:id'), {"id": large}
        )
    apply_oversized_migration(search_database)


@pytest.mark.parametrize(
    "tail",
    [
        "Email analyst@example.com about https://intel.example.com/path?q=1",
        "Hosts host-12.corp.example.com and 10.1.2.3, file C:\\Temp\\run.ps1",
        "Unicode Résumé ÉLAN straße İstanbul 日本語 テキスト",
        "Compound co-operate re-entry v2.1.3 -42 +7 3.14 1e10",
        "Long " + "x" * 2100 + " token then tail",
        "Lowercase growth " + "İ" * 1000 + " after",
    ],
)
def test_lexemes_answer_words_like_the_per_row_check(
    search_database: Engine, tail: str
) -> None:
    """Indexed word matching agrees with tracecat_case_text_matches_v1."""
    with search_database.begin() as conn:
        source = FILLER + tail
        case_id = seed_case(conn, 1, "synthetic", source)
        assert _stored(conn, case_id).description_lexemes is not None
        tokens = set(source.split()) | {
            "analyst",
            "example",
            "intel.example.com",
            "host-12",
            "10.1.2.3",
            "résumé",
            "élan",
            "straße",
            "co-operate",
            "operate",
            "v2.1.3",
            "missingword",
            "tail",
            "after",
        }
        mismatches = [
            term
            for term, indexed, native in conn.execute(
                sa.text("""
                SELECT q.term,
                    c.description_lexemes @@ plainto_tsquery('pg_catalog.simple', q.term),
                    public.tracecat_case_text_matches_v1(c.description, q.term, false)
                FROM "case" c CROSS JOIN unnest(CAST(:terms AS text[])) AS q(term)
                WHERE c.id = :id AND numnode(plainto_tsquery('pg_catalog.simple', q.term)) > 0
                    AND octet_length(q.term) < 2047
            """),
                {"id": case_id, "terms": sorted(tokens)},
            ).tuples()
            if indexed is not native
        ]
        assert mismatches == []


def test_too_many_distinct_words_keep_the_per_row_check(
    search_database: Engine,
) -> None:
    """Past 1 MB of distinct word text no lexemes are stored, never a corrupt vector."""
    words = " ".join(f"w{index:017d}" for index in range(60_000))
    with search_database.begin() as conn:
        case_id = seed_case(conn, 1, "synthetic huge", words)
        assert _stored(conn, case_id).description_lexemes is None
        assert search_ids(conn, f"w{59_999:017d}") == [case_id]
        assert search_ids(conn, f"w{0:017d}") == [case_id]
        assert search_ids(conn, "absentword") == []


def test_cleared_lexemes_keep_the_per_row_check(search_database: Engine) -> None:
    """A source with neither a vector nor lexemes is still matched per row."""
    with search_database.begin() as conn:
        case_id = seed_case(conn, 1, "synthetic", FILLER + '"quoted words" here')
        # Clearing a stored column without changing the text keeps it cleared.
        conn.execute(
            sa.text('UPDATE "case" SET description_lexemes = NULL WHERE id=:id'),
            {"id": case_id},
        )
        assert _stored(conn, case_id).description_lexemes is None
        assert search_ids(conn, "quoted") == [case_id]
        assert search_ids(conn, '"quoted words"') == [case_id]
        assert search_ids(conn, '"words quoted"') == []


def test_literal_search_can_read_long_sources_through_trigrams(
    search_database: Engine,
) -> None:
    """The complete text of a NULL-vector source has its own trigram index."""
    with search_database.connect() as conn, conn.begin() as transaction:
        large = seed_case(conn, 1, "synthetic", FILLER + "needle-42.pdf")
        # On a tiny table the planner prefers these small B-tree indexes.
        # Dropping them inside this rolled-back transaction leaves only the
        # trigram index to answer the long-source branch.
        conn.execute(
            sa.text(
                "DROP INDEX ix_case_summary_tsv_fallback, ix_case_description_tsv_fallback"
            )
        )
        conn.execute(sa.text("SET LOCAL enable_seqscan = off"))
        conn.execute(sa.text("SET LOCAL enable_indexscan = off"))
        expressions = build_case_text_expressions(
            parse_fixture_query("literal:needle-42"), WORKSPACE
        )
        compiled = (
            sa.select(Case.id)
            .where(expressions.predicate)
            .compile(
                dialect=search_database.dialect, compile_kwargs={"literal_binds": True}
            )
        )
        sql = str(compiled).replace("%%", "%")
        plan = "\n".join(conn.execute(sa.text(f"EXPLAIN {sql}")).scalars())
        assert "ix_case_description_trgm_fallback" in plan
        assert search_ids(conn, "literal:needle-42") == [large]
        transaction.rollback()
