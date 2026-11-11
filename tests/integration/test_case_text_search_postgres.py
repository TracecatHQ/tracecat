"""Live PostgreSQL search/migration checks using a disposable synthetic database.

The minimal source table intentionally has no new application columns or write
hooks: every write in these tests represents a previous-version writer. The
fixture never migrates the shared application database.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.elements import ColumnElement

from tests.database import TEST_DB_CONFIG
from tracecat.cases.enums import CaseStatus
from tracecat.cases.text_search import (
    MAX_EXCERPT_LENGTH,
    CaseTextQuery,
    build_case_text_expressions,
    get_case_search_excerpts,
    parse_case_text_query,
)
from tracecat.cases.text_search_indexes import ensure_case_text_indexes
from tracecat.db.models import Case
from tracecat.query.lexical_types import (
    LexicalSearchError,
    LexicalSearchErrorCode,
    SearchMode,
)

pytestmark = pytest.mark.integration
WORKSPACE = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE = uuid.UUID("00000000-0000-4000-8000-000000000002")
INDEX_NAMES = {
    "ix_case_summary_fts",
    "ix_case_description_fts",
    "ix_case_summary_trgm",
    "ix_case_description_trgm",
    "ix_case_summary_text_fallback",
    "ix_case_description_text_fallback",
}


class SearchMigration(Protocol):
    _FUNCTION_SQL_COMMANDS: tuple[str, ...]
    _INDEXES: tuple[tuple[str, str], ...]
    _FALLBACK_INDEXES: tuple[tuple[str, str], ...]
    _MATCH_BODY: str
    _VECTOR_BODY: str

    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


class VectorMigration(Protocol):
    _BACKFILL_BATCH: int
    _TRIGGER_FUNCTION_SQL: str
    _TRIGGER_SQL: str
    _VECTOR_INDEXES: tuple[tuple[str, str], ...]

    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


class WordMigration(Protocol):
    _BACKFILL_BATCH: int
    _WORDS_FUNCTION_SQL: str
    _TRIGGER_FUNCTION_SQL: str
    _TRIGGER_SQL: str
    _TABLE_INDEXES: tuple[tuple[str, str], ...]
    _WORD_INDEXES: tuple[tuple[str, str], ...]

    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


def _load_revision(filename: str, name: str) -> object:
    path = Path(__file__).resolve().parents[2] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_search_migration() -> SearchMigration:
    """Load the unmerged revision for disposable migration/transport fixtures."""
    return cast(
        SearchMigration,
        _load_revision(
            "e2018a1b2c3d_add_case_text_search_indexes.py",
            "case_text_search_migration",
        ),
    )


def load_vector_migration() -> VectorMigration:
    """Load the stored-vector revision that follows the search revision."""
    return cast(
        VectorMigration,
        _load_revision(
            "5ca140c06ec2_store_case_search_vectors.py", "case_search_vector_migration"
        ),
    )


def load_word_migration() -> WordMigration:
    """Load the stored word-list revision that follows the vector revision."""
    return cast(
        WordMigration,
        _load_revision(
            "e4fd31674ae9_store_case_search_words.py", "case_search_word_migration"
        ),
    )


def apply_search_migration(
    engine: Engine, *, downgrade: bool = False, search_path: str | None = None
) -> None:
    """Apply only this additive revision to the isolated minimal source table."""
    migration = load_search_migration()
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        with context.begin_transaction(), Operations.context(context):
            if search_path is not None:
                conn.execute(
                    sa.text("SELECT set_config('search_path', :search_path, false)"),
                    {"search_path": search_path},
                )
            if downgrade:
                migration.downgrade()
            else:
                migration.upgrade()


def apply_vector_migration(
    engine: Engine, *, downgrade: bool = False, backfill_batch: int | None = None
) -> None:
    """Apply only the stored-vector revision; it requires the search revision."""
    migration = load_vector_migration()
    if backfill_batch is not None:
        migration._BACKFILL_BATCH = backfill_batch
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        with context.begin_transaction(), Operations.context(context):
            if downgrade:
                migration.downgrade()
            else:
                migration.upgrade()


def apply_word_migration(
    engine: Engine,
    *,
    downgrade: bool = False,
    backfill_batch: int | None = None,
    search_path: str | None = None,
) -> None:
    """Apply only the stored word-list revision; it requires the vector revision."""
    migration = load_word_migration()
    if backfill_batch is not None:
        migration._BACKFILL_BATCH = backfill_batch
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        with context.begin_transaction(), Operations.context(context):
            if search_path is not None:
                conn.execute(
                    sa.text("SELECT set_config('search_path', :search_path, false)"),
                    {"search_path": search_path},
                )
            if downgrade:
                migration.downgrade()
            else:
                migration.upgrade()


def apply_case_search_migrations(engine: Engine, *, downgrade: bool = False) -> None:
    """Apply every case search revision in dependency order."""
    if downgrade:
        apply_word_migration(engine, downgrade=True)
        apply_vector_migration(engine, downgrade=True)
        apply_search_migration(engine, downgrade=True)
    else:
        apply_search_migration(engine)
        apply_vector_migration(engine)
        apply_word_migration(engine)


@pytest.fixture
def search_database() -> Iterator[Engine]:
    """Own a random database and a non-superuser migration role, then clean up."""
    name = f"test_case_text_{uuid.uuid4().hex}"
    role = f"case_text_role_{uuid.uuid4().hex}"
    admin = sa.create_engine(
        TEST_DB_CONFIG.sys_url_sync, isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    url = TEST_DB_CONFIG.test_url_sync.replace(TEST_DB_CONFIG.test_db_name, name)
    engine = sa.create_engine(
        url, poolclass=NullPool, connect_args={"options": f"-c role={role}"}
    )
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE ROLE "{role}"'))
            conn.execute(sa.text(f'CREATE DATABASE "{name}" OWNER "{role}"'))
        with engine.begin() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
                    )
                )
                is False
            )
            # The word vocabulary references workspaces; cases here do not.
            conn.execute(sa.text("CREATE TABLE workspace (id uuid PRIMARY KEY)"))
            conn.execute(
                sa.text("INSERT INTO workspace (id) VALUES (:first), (:second)"),
                {"first": WORKSPACE, "second": OTHER_WORKSPACE},
            )
            conn.execute(
                sa.text("""CREATE TABLE "case" (
                id uuid PRIMARY KEY, workspace_id uuid NOT NULL, case_number integer NOT NULL,
                summary varchar NOT NULL, description varchar NOT NULL,
                status varchar(32) NOT NULL DEFAULT 'NEW',
                UNIQUE (workspace_id, case_number))""")
            )
            conn.execute(
                sa.text('CREATE INDEX ix_case_workspace_id ON "case" (workspace_id)')
            )
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION pg_trgm WITH SCHEMA public"))
        apply_case_search_migrations(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        admin.dispose()


def seed_case(
    conn: Connection,
    number: int,
    summary: str,
    description: str = "",
    *,
    workspace: uuid.UUID = WORKSPACE,
    status: str = "NEW",
) -> uuid.UUID:
    """Insert synthetic source text through the legacy write surface."""
    case_id = uuid.UUID(int=number + (1 << 120 if workspace == OTHER_WORKSPACE else 0))
    conn.execute(
        sa.text(
            'INSERT INTO "case" (id, workspace_id, case_number, summary, description, status) VALUES (:id, :workspace, :number, :summary, :description, :status)'
        ),
        {
            "id": case_id,
            "workspace": workspace,
            "number": number,
            "summary": summary,
            "description": description,
            "status": status,
        },
    )
    return case_id


def parse_fixture_query(value: str) -> CaseTextQuery:
    """Keep benchmark labels while invoking the explicit shared literal mode."""
    if value.startswith("literal:"):
        return parse_case_text_query(
            value.removeprefix("literal:"), mode=SearchMode.LITERAL
        )
    return parse_case_text_query(value)


def search_statement(
    query: str,
    *,
    workspace: uuid.UUID = WORKSPACE,
    filters: Sequence[ColumnElement[bool]] = (),
    limit: int = 50,
) -> sa.Select[tuple[uuid.UUID, int, bool, bool]]:
    """Compose all filters before LIMIT and preserve a case-ID tie-breaker."""
    expressions = build_case_text_expressions(parse_fixture_query(query), workspace)
    return (
        sa.select(
            Case.id,
            expressions.score.label("score"),
            expressions.summary_match,
            expressions.description_match,
        )
        .where(Case.workspace_id == workspace, expressions.predicate, *filters)
        .order_by(expressions.score.desc(), Case.id.desc())
        .limit(limit)
    )


def search_ids(
    conn: Connection,
    query: str,
    *,
    workspace: uuid.UUID = WORKSPACE,
    filters: Sequence[ColumnElement[bool]] = (),
    limit: int = 50,
) -> list[uuid.UUID]:
    """Run a bounded search with the documented transaction-local settings."""
    conn.execute(sa.text("SET LOCAL statement_timeout = 1500"))
    conn.execute(sa.text("SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.65"))
    return list(
        conn.execute(
            search_statement(query, workspace=workspace, filters=filters, limit=limit)
        ).scalars()
    )


def test_multiword_crosses_sources_but_phrases_do_not(search_database: Engine) -> None:
    with search_database.begin() as conn:
        split = seed_case(conn, 1, "PowerShell", "encoded command")
        phrase = seed_case(conn, 2, "PowerShell encoded", "command")
        assert set(search_ids(conn, "powershell encoded")) == {split, phrase}
        assert search_ids(conn, '"powershell encoded"') == [phrase]
        rows = conn.execute(search_statement("powershell encoded")).tuples().all()
        split_row = next(row for row in rows if row[0] == split)
        assert split_row[2:] == (True, True)
        seed_case(
            conn, 3, "PowerShell", "<p>encoded command</p>", workspace=OTHER_WORKSPACE
        )
        assert len(search_ids(conn, "powershell encoded")) == 2


def test_pure_phrase_and_mixed_phrase_queries_are_generic(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        expected = seed_case(conn, 1, "suspicious beacon", "network connection blocked")
        seed_case(conn, 2, "suspicious", "beacon network connection blocked")
        assert search_ids(conn, '"suspicious beacon"') == [expected]
        assert search_ids(conn, '"suspicious beacon" blocked') == [expected]


def test_short_exact_words_remain_required_in_selective_queries(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        phrase = seed_case(conn, 1, "PowerShell on host", "encoded")
        split = seed_case(conn, 2, "PowerShell", "on host")
        seed_case(conn, 3, "PowerShell", "host missing the preposition")
        assert set(search_ids(conn, "powershell on host")) == {phrase, split}
        assert set(search_ids(conn, "powershel on host")) == {phrase, split}
        assert search_ids(conn, '"powershell on host"') == [phrase]


@pytest.mark.parametrize("length", [32, 96])
def test_bare_hex_hash_matches_description_as_a_literal(
    search_database: Engine, length: int
) -> None:
    value = "ab" * (length // 2)
    with search_database.begin() as conn:
        expected = seed_case(conn, 1, "synthetic file indicator", f"file hash {value}")
        seed_case(conn, 2, "unrelated file indicator", f"file hash {value[:-1]}c")
        assert search_ids(conn, value) == [expected]


def test_exact_text_summary_and_fuzzy_relevance_tiers(search_database: Engine) -> None:
    with search_database.begin() as conn:
        exact = seed_case(conn, 1, "PowerShell")
        summary = seed_case(conn, 2, "PowerShell detected")
        description = seed_case(conn, 3, "process detected", "PowerShell")
        typo = seed_case(conn, 4, "Powershel detected")
        assert search_ids(conn, "powershell") == [exact, summary, description, typo]
        assert exact in search_ids(conn, "powershel")
        assert search_ids(conn, "CASE-0001") == [exact]
        assert search_ids(conn, str(exact)) == [exact]


@pytest.mark.anyio
async def test_native_excerpts_find_late_exact_and_fuzzy_words_without_prefix_misattribution(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        conn.execute(sa.text('ALTER TABLE "case" ALTER COLUMN description TYPE text'))
        source = (
            "powershellquarantinedunrelatedlongword "
            + "padding " * 900
            + "<b>PowerShell</b> encoded command "
            + "tail " * 100
        )
        expected = seed_case(conn, 1, "synthetic process", source)
        seed_case(conn, 2, "synthetic process", source)
        other = seed_case(
            conn, 3, "synthetic process", source, workspace=OTHER_WORKSPACE
        )
        assert search_ids(conn, "powershellx")[:2] == [uuid.UUID(int=2), expected]
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with engine.connect() as conn, AsyncSession(conn) as session:
            for value in ("powershell", "powershellx", '"powershell encoded"'):
                excerpts = await get_case_search_excerpts(
                    session,
                    workspace_id=WORKSPACE,
                    case_ids=[expected, other],
                    query=parse_case_text_query(value),
                )
                assert set(excerpts) == {expected}
                assert len(excerpts[expected]) == 1
                excerpt = excerpts[expected][0]
                assert excerpt.field == "description"
                assert "PowerShell" in excerpt.text
                assert "quarantined" not in excerpt.text
                assert "<b>" not in excerpt.text
                assert "TCSTART_" not in excerpt.text and "TCSTOP_" not in excerpt.text
                assert len(excerpt.text) <= MAX_EXCERPT_LENGTH
                assert await session.scalar(sa.text("SHOW statement_timeout")) == "0"
                assert (
                    await session.scalar(
                        sa.text("SHOW pg_trgm.strict_word_similarity_threshold")
                    )
                    == "0.5"
                )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_late_literal_excerpt_preserves_wildcard_and_indicator_semantics(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        conn.execute(sa.text('ALTER TABLE "case" ALTER COLUMN description TYPE text'))
        expected = seed_case(
            conn,
            1,
            "synthetic indicator",
            "padding " * 900 + "abc%_value 192.0.2.10 " + "ab" * 16,
        )
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with AsyncSession(engine) as session:
            for value in ("literal:abc%_value", "192.0.2.10", "ab" * 16):
                query = parse_fixture_query(value)
                excerpts = await get_case_search_excerpts(
                    session, workspace_id=WORKSPACE, case_ids=[expected], query=query
                )
                assert query.clauses[0].value in excerpts[expected][0].text
                assert len(excerpts[expected][0].text) <= MAX_EXCERPT_LENGTH
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_repeated_native_highlights_leave_no_partial_private_markers(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        case_id = seed_case(conn, 1, "synthetic process", "PowerShell " * 100)
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with AsyncSession(engine) as session:
            excerpts = await get_case_search_excerpts(
                session,
                workspace_id=WORKSPACE,
                case_ids=[case_id],
                query=parse_case_text_query("powershell"),
            )
            excerpt = excerpts[case_id][0]
            assert "PowerShell" in excerpt.text
            assert "TCSTART" not in excerpt.text and "TCSTOP" not in excerpt.text
            assert len(excerpt.text) <= MAX_EXCERPT_LENGTH
    finally:
        await engine.dispose()


def test_literals_escape_wildcards_and_indicators_do_not_fuzz(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        literal = seed_case(
            conn, 1, "indicator abc%_value", "192.0.2.10 0.0.0.0 " + "a3" * 32
        )
        seed_case(conn, 2, "indicator abcXXvalue", "192.0.2.11 " + "a3" * 31 + "a4")
        assert search_ids(conn, "literal:abc%_value") == [literal]
        assert search_ids(conn, "192.0.2.10") == [literal]
        assert search_ids(conn, "0.0.0.0") == [literal]
        assert search_ids(conn, "a3" * 32) == [literal]


def test_legacy_update_and_delete_are_maintained_by_expression_indexes(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        case_id = seed_case(conn, 1, "old malware", "original text")
        assert search_ids(conn, "malware") == [case_id]
        conn.execute(
            sa.text(
                'UPDATE "case" SET summary = :summary, description = :description WHERE id = :id'
            ),
            {"id": case_id, "summary": "PowerShell", "description": "encoded command"},
        )
        assert not search_ids(conn, "malware")
        assert search_ids(conn, "powershell encoded") == [case_id]
        assert search_ids(conn, "powershel") == [case_id]
        conn.execute(sa.text('DELETE FROM "case" WHERE id = :id'), {"id": case_id})
        assert not search_ids(conn, "powershell encoded")


def test_filters_precede_pagination_and_integer_score_seeks_are_stable(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        expected = [
            seed_case(conn, number, "PowerShell", status="CLOSED")
            for number in range(1, 8)
        ]
        for number in range(8, 308):
            seed_case(conn, number, "PowerShell")
        filters = [Case.status == CaseStatus.CLOSED]
        first = (
            conn.execute(search_statement("powershell", filters=filters, limit=3))
            .tuples()
            .all()
        )
        assert [row[0] for row in first] == list(reversed(expected))[:3]
        expressions = build_case_text_expressions(
            parse_case_text_query("powershell"), WORKSPACE
        )
        cursor = first[-1]
        seek = sa.tuple_(expressions.score, Case.id) < sa.tuple_(
            sa.literal(cursor[1]), sa.literal(cursor[0])
        )
        next_ids = search_ids(conn, "powershell", filters=[*filters, seek], limit=3)
        assert next_ids == list(reversed(expected))[3:6]


@pytest.mark.parametrize(
    ("query", "index"),
    [
        ('"powershell"', "ix_case_summary_tsv"),
        ("powershel", "ix_case_summary_words"),
        ("literal:PowerShell", "ix_case_summary_trgm"),
    ],
)
def test_candidate_predicates_can_use_the_declared_indexes(
    search_database: Engine, query: str, index: str
) -> None:
    with search_database.begin() as conn:
        seed_case(conn, 1, "PowerShell encoded")
        conn.execute(sa.text("SET LOCAL enable_seqscan = off"))
        conn.execute(sa.text("SET LOCAL enable_indexscan = off"))
        conn.execute(
            sa.text("SET LOCAL pg_trgm.strict_word_similarity_threshold = 0.65")
        )
        expressions = build_case_text_expressions(parse_fixture_query(query), WORKSPACE)
        compiled = (
            sa.select(Case.id)
            .where(expressions.predicate)
            .compile(
                dialect=search_database.dialect,
                compile_kwargs={"literal_binds": True},
            )
        )
        # Compiled psycopg SQL escapes percent signs for its parameter syntax;
        # TextClause must receive the original SQL and escape them only once.
        sql = str(compiled).replace("%%", "%")
        plan = "\n".join(conn.execute(sa.text(f"EXPLAIN {sql}")).scalars())
        assert index in plan


@pytest.mark.anyio
async def test_migration_is_retryable_and_downgrade_preserves_source_and_extension(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        seed_case(conn, 1, "preserved source")
        indexes = set(
            conn.execute(
                sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'case'")
            ).scalars()
        )
        assert INDEX_NAMES <= indexes
        role = conn.scalar(sa.text("SELECT current_user"))
        assert role is not None
    apply_search_migration(search_database, search_path="pg_catalog")
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"),
        poolclass=NullPool,
        connect_args={
            "server_settings": {
                "role": role,
                "search_path": "pg_catalog",
                "default_transaction_read_only": "on",
            }
        },
    )
    try:
        async with AsyncSession(engine) as session:
            expressions = (
                (
                    await session.execute(
                        sa.text("""SELECT pg_get_expr(i.indexprs, i.indrelid)
                        FROM pg_index i WHERE i.indexrelid IN
                        ('public.ix_case_summary_fts'::regclass, 'public.ix_case_description_fts'::regclass)""")
                    )
                )
                .scalars()
                .all()
            )
            assert len(expressions) == 2
            assert all(
                value.startswith("public.tracecat_case_safe_vector_v1(")
                for value in expressions
            )
            # Search resolves pg_trgm through the search_path, like tables.
            await session.execute(
                sa.text("SELECT set_config('search_path', 'pg_catalog, public', true)")
            )
            await ensure_case_text_indexes(session)
    finally:
        await engine.dispose()
    apply_search_migration(search_database, downgrade=True, search_path="pg_catalog")
    with search_database.begin() as conn:
        assert conn.scalar(sa.text('SELECT summary FROM "case"')) == "preserved source"
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
            )
            == 1
        )
        indexes = set(
            conn.execute(
                sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'case'")
            ).scalars()
        )
        assert not INDEX_NAMES & indexes


def test_migration_rebuilds_an_invalid_concurrent_index(
    search_database: Engine,
) -> None:
    """Cancel a real guarded build, then retry without deleting source data."""
    migration = load_search_migration()
    expression = next(
        expression
        for name, expression in migration._INDEXES
        if name == "ix_case_description_fts"
    )
    with search_database.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO public.\"case\" (id, workspace_id, case_number, summary, description) SELECT gen_random_uuid(), :workspace, n, 'synthetic', repeat('synthetic alpha ', 400) FROM generate_series(1, 500) n"
            ),
            {"workspace": WORKSPACE},
        )
    with search_database.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        conn.execute(sa.text("DROP INDEX public.ix_case_description_fts"))
        conn.execute(sa.text("SET statement_timeout = 25"))
        with pytest.raises(DBAPIError):
            conn.execute(
                sa.text(
                    f'CREATE INDEX CONCURRENTLY ix_case_description_fts ON public."case" USING gin ({expression})'
                )
            )
        conn.execute(sa.text("SET statement_timeout = 0"))
        assert (
            conn.scalar(
                sa.text(
                    "SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid='public.ix_case_description_fts'::regclass"
                )
            )
            is False
        )
    apply_search_migration(search_database, search_path="pg_catalog")
    with search_database.begin() as conn:
        assert conn.scalar(sa.text('SELECT count(*) FROM public."case"')) == 500
        assert (
            conn.scalar(
                sa.text(
                    "SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid='public.ix_case_description_fts'::regclass"
                )
            )
            is True
        )


@pytest.mark.parametrize("collision", ["target", "expression", "opclass", "partial"])
def test_incompatible_index_collisions_fail_without_adopting_or_dropping_them(
    search_database: Engine, collision: str
) -> None:
    apply_case_search_migrations(search_database, downgrade=True)
    with search_database.begin() as conn:
        seed_case(conn, 1, "preserved synthetic source")
        if collision == "target":
            conn.execute(
                sa.text("CREATE TABLE synthetic_source (summary varchar(255))")
            )
            sql = "CREATE INDEX ix_case_summary_fts ON synthetic_source USING gin (to_tsvector('simple'::regconfig, coalesce(summary, '')))"
        elif collision == "expression":
            sql = "CREATE INDEX ix_case_summary_fts ON \"case\" USING gin (to_tsvector('english'::regconfig, coalesce(summary, '')))"
        elif collision == "opclass":
            sql = "CREATE INDEX ix_case_summary_fts ON \"case\" USING gin (lower(coalesce(summary, '')) public.gin_trgm_ops)"
        else:
            sql = "CREATE INDEX ix_case_summary_fts ON \"case\" USING gin (to_tsvector('simple'::regconfig, coalesce(summary, ''))) WHERE summary <> ''"
        conn.execute(sa.text(sql))
        before = conn.execute(
            sa.text(
                "SELECT oid, pg_get_indexdef(oid) FROM pg_class WHERE oid = 'ix_case_summary_fts'::regclass"
            )
        ).one()
    with pytest.raises(RuntimeError, match="incompatible existing definition"):
        apply_search_migration(search_database)
    with search_database.begin() as conn:
        after = conn.execute(
            sa.text(
                "SELECT oid, pg_get_indexdef(oid) FROM pg_class WHERE oid = 'ix_case_summary_fts'::regclass"
            )
        ).one()
        assert after == before
        assert (
            conn.scalar(sa.text('SELECT summary FROM "case"'))
            == "preserved synthetic source"
        )
        assert (
            conn.scalar(
                sa.text("SELECT count(*) FROM pg_class WHERE relname = ANY(:names)"),
                {"names": sorted(INDEX_NAMES)},
            )
            == 1
        )


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["fts", "text_fallback"])
async def test_spoofed_legacy_expression_index_blocks_migration_not_readiness(
    search_database: Engine, kind: str
) -> None:
    name = f"ix_case_summary_{kind}"
    with search_database.begin() as conn:
        role = conn.scalar(sa.text("SELECT current_user"))
        assert role is not None
        seed_case(conn, 1, "preserved synthetic source")
        conn.execute(sa.text("CREATE SCHEMA synthetic_search"))
        conn.execute(
            sa.text("""CREATE FUNCTION synthetic_search.tracecat_case_safe_vector_v1(text)
                RETURNS tsvector LANGUAGE sql IMMUTABLE STRICT AS $$
                SELECT pg_catalog.to_tsvector('pg_catalog.simple'::regconfig, $1) $$""")
        )
        conn.execute(sa.text("SET search_path=synthetic_search,pg_catalog"))
        conn.execute(sa.text(f"DROP INDEX public.{name}"))
        expression = (
            "synthetic_search.tracecat_case_safe_vector_v1(coalesce(summary::text, ''))"
        )
        if kind == "fts":
            sql = f'CREATE INDEX {name} ON public."case" USING gin ({expression})'
        else:
            sql = f'CREATE INDEX {name} ON public."case" (workspace_id,id) WHERE {expression} IS NULL'
        conn.execute(sa.text(sql))
        before = conn.execute(
            sa.text("""SELECT c.oid, pg_get_indexdef(c.oid),
                pg_get_expr(coalesce(i.indexprs,i.indpred),i.indrelid)
                FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
                WHERE c.oid=to_regclass(:name)"""),
            {"name": f"public.{name}"},
        ).one()
        assert "synthetic_search." not in before[2]
        assert "tracecat_case_safe_vector_v1(" in before[2]
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"),
        poolclass=NullPool,
        connect_args={
            "server_settings": {
                "role": role,
                # public keeps pg_trgm resolvable; only the helpers are spoofed.
                "search_path": "synthetic_search,pg_catalog,public",
            }
        },
    )
    try:
        async with AsyncSession(engine) as session:
            # Search reads the stored vector columns, so a spoofed legacy
            # expression index cannot change readiness.
            await ensure_case_text_indexes(session)
    finally:
        await engine.dispose()
    with pytest.raises(RuntimeError, match="incompatible existing definition"):
        apply_search_migration(
            search_database, search_path="synthetic_search,pg_catalog"
        )
    with search_database.begin() as conn:
        conn.execute(sa.text("SET search_path=synthetic_search,pg_catalog"))
        after = conn.execute(
            sa.text("""SELECT c.oid, pg_get_indexdef(c.oid),
                pg_get_expr(coalesce(i.indexprs,i.indpred),i.indrelid)
                FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
                WHERE c.oid=to_regclass(:name)"""),
            {"name": f"public.{name}"},
        ).one()
        assert after == before
        assert (
            conn.scalar(sa.text('SELECT summary FROM public."case"'))
            == "preserved synthetic source"
        )


@pytest.mark.anyio
async def test_extension_outside_public_is_used_in_place(
    search_database: Engine,
) -> None:
    """IBM Cloud keeps extensions in its read-only ibm_extension schema."""
    # The vocabulary's trigram index and word-list helper also use pg_trgm.
    apply_word_migration(search_database, downgrade=True)
    apply_search_migration(search_database, downgrade=True)
    # Relocation touches extension objects owned by the bootstrap superuser,
    # so it is an operator task even when a normal role installed pg_trgm.
    # Only setup uses this operator connection; the migration remains limited.
    operator = sa.create_engine(search_database.url, poolclass=NullPool)
    try:
        with operator.begin() as conn:
            conn.execute(sa.text("CREATE SCHEMA ibm_extension"))
            # IBM's schema is read-only: usable by every role, writable by none.
            conn.execute(sa.text("GRANT USAGE ON SCHEMA ibm_extension TO PUBLIC"))
            conn.execute(sa.text("ALTER EXTENSION pg_trgm SET SCHEMA ibm_extension"))
        apply_search_migration(search_database)
        apply_word_migration(search_database)
        with operator.begin() as conn:
            opclass_schemas = set(
                conn.execute(
                    sa.text("""SELECT n.nspname FROM pg_index i
                    JOIN pg_opclass o ON o.oid = i.indclass[0]
                    JOIN pg_namespace n ON n.oid = o.opcnamespace
                    WHERE i.indexrelid IN ('public.ix_case_summary_trgm'::regclass,
                        'public.ix_case_description_trgm'::regclass,
                        'public.ix_case_search_word_word_trgm'::regclass)""")
                ).scalars()
            )
            assert opclass_schemas == {"ibm_extension"}
            assert "ibm_extension.show_trgm(" in conn.scalar(
                sa.text(
                    "SELECT prosrc FROM pg_proc WHERE oid = 'public.tracecat_case_words_v1(text)'::regprocedure"
                )
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'"
                    )
                )
                == "ibm_extension"
            )
    finally:
        operator.dispose()

    with search_database.begin() as conn:
        role = conn.scalar(sa.text("SELECT current_user"))
        typo = seed_case(conn, 1, "Powershel detected")
        conn.execute(sa.text("SET LOCAL search_path = public, ibm_extension"))
        assert search_ids(conn, "powershell") == [typo]

    for search_path, ready in (("public, ibm_extension", True), ("public", False)):
        engine = create_async_engine(
            search_database.url.set(drivername="postgresql+asyncpg"),
            poolclass=NullPool,
            connect_args={
                "server_settings": {"role": role, "search_path": search_path}
            },
        )
        try:
            async with AsyncSession(engine) as session:
                if ready:
                    await ensure_case_text_indexes(session)
                else:
                    with pytest.raises(LexicalSearchError) as error:
                        await ensure_case_text_indexes(session)
                    assert error.value.code == LexicalSearchErrorCode.UNAVAILABLE
        finally:
            await engine.dispose()


def test_migration_checks_extension_without_installation(
    search_database: Engine,
) -> None:
    # The vocabulary's trigram index also depends on pg_trgm.
    apply_word_migration(search_database, downgrade=True)
    apply_search_migration(search_database, downgrade=True)
    operator = sa.create_engine(search_database.url, poolclass=NullPool)
    try:
        with operator.begin() as conn:
            conn.execute(sa.text("DROP EXTENSION pg_trgm"))
        with pytest.raises(DBAPIError, match="requires pg_trgm to be enabled"):
            apply_search_migration(search_database)
        with operator.begin() as conn:
            assert not conn.scalar(
                sa.text(
                    "SELECT EXISTS (SELECT FROM pg_extension WHERE extname='pg_trgm')"
                )
            )
            conn.execute(sa.text("CREATE EXTENSION pg_trgm WITH SCHEMA public"))
        apply_search_migration(search_database)
    finally:
        operator.dispose()


def test_model_maximum_unicode_documents_survive_old_writes_and_migration(
    search_database: Engine,
) -> None:
    """Exercise the model's5000-character bound without any new writer code."""
    apply_case_search_migrations(search_database, downgrade=True)
    suffix = " café encoded-command 完了"
    source = ("a-b 中文 " * 800)[: 5000 - len(suffix)] + suffix
    assert len(source) == 5000
    with search_database.begin() as conn:
        expected = seed_case(conn, 1, "Résumé & synthetic-alert " + "é" * 220, source)
    apply_case_search_migrations(search_database)
    with search_database.begin() as conn:
        assert search_ids(conn, '"café encoded-command"') == [expected]
        assert search_ids(conn, "完了") == [expected]
        updated_suffix = " résumé updated-phrase 完了"
        updated = ("ab-cd 中文 " * 800)[: 5000 - len(updated_suffix)] + updated_suffix
        assert len(updated) == 5000
        conn.execute(
            sa.text('UPDATE "case" SET description = :value WHERE id=:id'),
            {"value": updated, "id": expected},
        )
        assert search_ids(conn, '"résumé updated-phrase"') == [expected]
        assert search_ids(conn, "café") == []
        size, maximum_position = conn.execute(
            sa.text(
                "SELECT pg_column_size(to_tsvector('simple', description)), max(p) FROM public.\"case\", LATERAL unnest(to_tsvector('simple', description)) AS t(lexeme, positions, weights), LATERAL unnest(t.positions) p WHERE id=:id GROUP BY description"
            ),
            {"id": expected},
        ).one()
        assert size < 1_048_575 and maximum_position < 16_383
        conn.execute(sa.text('DELETE FROM "case" WHERE id=:id'), {"id": expected})
        assert search_ids(conn, '"résumé updated-phrase"') == []


@pytest.mark.parametrize(
    ("source", "term", "phrase"),
    [
        ("foo-bar encoded", "foo-bar", True),
        ("https://example.com/path/file.html encoded", "example.com", False),
        ("foo <b>bar</b> 中文 café", "foo bar", True),
        ("suspicious café encoded-command 完了", "café encoded-command", True),
        ("alpha noise beta", "alpha beta", True),
        ("alpha noise beta", "alpha beta", False),
        ("foo " + "a" * 2046 + " bar", "foo bar", True),
        ("foo " + "a" * 2047 + " bar", "foo bar", True),
        ("foo " + "a" * 2048 + " bar", "foo bar", True),
    ],
)
def test_native_stream_matches_supported_parser_positions(
    search_database: Engine, source: str, term: str, phrase: bool
) -> None:
    with search_database.begin() as conn:
        query = "phraseto_tsquery" if phrase else "plainto_tsquery"
        native, fallback = conn.execute(
            sa.text(
                f"SELECT to_tsvector('pg_catalog.simple', :source) @@ {query}('pg_catalog.simple', :term), public.tracecat_case_text_matches_v1(:source,:term,:phrase)"
            ),
            {"source": source, "term": term, "phrase": phrase},
        ).one()
        assert fallback is native


def test_guarded_old_writes_migration_and_full_source_recall(
    search_database: Engine,
) -> None:
    apply_case_search_migrations(search_database, downgrade=True)
    large = (
        " ".join(f"synthetic{number:06d}" for number in range(90000)) + " alpha beta"
    )
    normalized_overflow = "foo " + "Ⱥ" * 683 + " bar"
    repeated = "foo " * 300 + "foo bar"
    with search_database.begin() as conn:
        huge_id = seed_case(conn, 1, "synthetic large", large)
        overflow_id = seed_case(conn, 2, "synthetic normalized", normalized_overflow)
        repeated_id = seed_case(conn, 3, "synthetic repeated", repeated)
    apply_case_search_migrations(search_database)
    with search_database.begin() as conn:
        assert (
            conn.scalar(
                sa.text("SELECT public.tracecat_case_safe_vector_v1(:source) IS NULL"),
                {"source": large},
            )
            is True
        )
        assert (
            conn.scalar(
                sa.text("SELECT public.tracecat_case_safe_vector_v1(:source) IS NULL"),
                {"source": normalized_overflow},
            )
            is True
        )
        assert (
            conn.scalar(
                sa.text(
                    "SELECT to_tsvector('simple', :source) @@ phraseto_tsquery('simple','foo bar')"
                ),
                {"source": repeated},
            )
            is False
        )
        assert set(search_ids(conn, '"foo bar"')) == {repeated_id}
        assert search_ids(conn, '"alpha beta"') == [huge_id]
        assert set(search_ids(conn, "synthetic000001 beta")) == {huge_id}
        assert overflow_id in search_ids(conn, "foo")
        conn.execute(
            sa.text('UPDATE "case" SET description=:source WHERE id=:id'),
            {"source": large + " café encoded-command", "id": huge_id},
        )
        assert search_ids(conn, '"café encoded-command"') == [huge_id]
        created = seed_case(conn, 4, "synthetic new large", large + " late-phrase")
        assert search_ids(conn, "late-phrase") == [created]
        conn.execute(sa.text('DELETE FROM "case" WHERE id=:id'), {"id": created})
        assert search_ids(conn, "late-phrase") == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("z " * 17000 + "alpha beta", id="position-overflow"),
        pytest.param("alpha z " * 300 + "alpha beta", id="retained-position-overflow"),
        pytest.param(
            "alpha unrelated beta " + "z " * 500 + "alpha beta", id="scattered-decoy"
        ),
        pytest.param(
            ("alpha " * 20 + "z ") * 10 + "padding " * 80 + "alpha beta",
            id="repeated-decoy",
        ),
    ],
)
async def test_large_and_repeated_phrase_excerpts_use_complete_native_context(
    search_database: Engine,
    source: str,
) -> None:
    with search_database.begin() as conn:
        expected = seed_case(conn, 1, "synthetic source", source)
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with engine.connect() as conn, AsyncSession(conn) as session:
            excerpts = await get_case_search_excerpts(
                session,
                workspace_id=WORKSPACE,
                case_ids=[expected],
                query=parse_case_text_query('"alpha beta"'),
            )
            assert "alpha beta" in excerpts[expected][0].text.lower()
            assert len(excerpts[expected][0].text) <= 240
    finally:
        await engine.dispose()


def test_safe_vector_exception_boundary_disables_parallel_operations(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        for number in range(1, 101):
            seed_case(conn, number, "synthetic parallel", "PowerShell encoded")
        conn.execute(sa.text("SET LOCAL max_parallel_workers_per_gather=2"))
        conn.execute(sa.text("SET LOCAL min_parallel_table_scan_size=0"))
        conn.execute(sa.text("SET LOCAL parallel_setup_cost=0"))
        conn.execute(sa.text("SET LOCAL parallel_tuple_cost=0"))
        statement = 'SELECT sum(octet_length(public.tracecat_case_safe_vector_v1(description)::text)) FROM public."case"'
        plan = "\n".join(conn.execute(sa.text("EXPLAIN " + statement)).scalars())
        assert "Gather" not in plan
        assert conn.scalar(sa.text(statement)) > 0
        assert (
            conn.scalar(
                sa.text(
                    "SELECT proparallel FROM pg_proc WHERE oid='public.tracecat_case_safe_vector_v1(text)'::regprocedure"
                )
            )
            == "u"
        )


def test_typo_matching_uses_complete_words_and_over_budget_fallback(
    search_database: Engine,
) -> None:
    with search_database.begin() as conn:
        listed = seed_case(conn, 1, "synthetic alert", "ran powershel encoded")
        # Over the byte budget, so it has no stored word list.
        large = seed_case(conn, 2, "synthetic large", "word " * 2000 + "powershel")
        # Similar only across a word boundary: no complete word matches.
        seed_case(conn, 3, "synthetic split", "power shell")
        other = seed_case(
            conn, 4, "synthetic other", "powershel", workspace=OTHER_WORKSPACE
        )
        assert (
            conn.scalar(
                sa.text('SELECT description_words IS NULL FROM "case" WHERE id=:id'),
                {"id": large},
            )
            is True
        )
        assert set(search_ids(conn, "powershell")) == {listed, large}
        assert search_ids(conn, "powershell", workspace=OTHER_WORKSPACE) == [other]
        # A duplicate vocabulary word from a concurrent insert is harmless.
        conn.execute(
            sa.text(
                "INSERT INTO case_search_word (workspace_id, word) VALUES (:ws, 'powershel')"
            ),
            {"ws": WORKSPACE},
        )
        assert set(search_ids(conn, "powershell")) == {listed, large}
        # Words of deleted cases stay in the vocabulary without matching.
        conn.execute(sa.text('DELETE FROM "case" WHERE id=:id'), {"id": listed})
        assert search_ids(conn, "powershell") == [large]
