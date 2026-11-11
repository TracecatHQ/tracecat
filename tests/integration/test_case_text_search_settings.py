"""Native readiness and transaction-local case search settings."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.test_case_text_search_postgres import (
    WORKSPACE,
    apply_search_migration,
    apply_word_migration,
)
from tests.integration.test_case_text_search_postgres import (
    search_database as _search_database,
)
from tracecat import config
from tracecat.cases.text_search import case_search_execution_context
from tracecat.cases.text_search_indexes import ensure_case_text_indexes
from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.query.lexical_types import LexicalSearchError, LexicalSearchErrorCode

pytestmark = pytest.mark.integration
search_database = _search_database


@pytest.mark.anyio
async def test_separate_runtime_role_needs_explicit_helper_grants(
    search_database: Engine,
) -> None:
    """A hardened migration owner succeeds; the runtime role needs every grant."""
    apply_word_migration(search_database, downgrade=True)
    apply_search_migration(search_database, downgrade=True)
    with search_database.begin() as conn:
        conn.execute(
            sa.text("ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC")
        )
    apply_search_migration(search_database)
    apply_word_migration(search_database)

    role = f"case_text_runtime_{uuid.uuid4().hex}"
    operator = sa.create_engine(search_database.url, poolclass=NullPool)
    runtime = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"),
        poolclass=NullPool,
        connect_args={"server_settings": {"role": role}},
    )
    try:
        with operator.begin() as conn:
            conn.execute(sa.text(f'CREATE ROLE "{role}"'))
            conn.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        async with AsyncSession(runtime) as session:
            with pytest.raises(LexicalSearchError) as error:
                await ensure_case_text_indexes(session)
            assert error.value.code == LexicalSearchErrorCode.UNAVAILABLE

        with operator.begin() as conn:
            conn.execute(
                sa.text(
                    f'GRANT EXECUTE ON FUNCTION public.tracecat_case_safe_vector_v1(text) TO "{role}"'
                )
            )
        async with AsyncSession(runtime) as session:
            with pytest.raises(LexicalSearchError) as error:
                await ensure_case_text_indexes(session)
            assert error.value.code == LexicalSearchErrorCode.UNAVAILABLE

        with operator.begin() as conn:
            conn.execute(
                sa.text(
                    f'GRANT EXECUTE ON FUNCTION public.tracecat_case_text_matches_v1(text,text,boolean) TO "{role}"'
                )
            )
        async with AsyncSession(runtime) as session:
            with pytest.raises(LexicalSearchError) as error:
                await ensure_case_text_indexes(session)
            assert error.value.code == LexicalSearchErrorCode.UNAVAILABLE

        with operator.begin() as conn:
            conn.execute(
                sa.text(f'GRANT SELECT ON public.case_search_word TO "{role}"')
            )
        async with AsyncSession(runtime) as session:
            await ensure_case_text_indexes(session)
            assert await session.scalar(
                sa.text(
                    "SELECT public.tracecat_case_text_matches_v1('synthetic text', 'synthetic', false)"
                )
            )
            assert (
                await session.scalar(
                    sa.text(
                        "SELECT public.tracecat_case_safe_vector_v1('synthetic text')"
                    )
                )
                is not None
            )

        # Search readiness does not cover writes: the word-list trigger runs as
        # the writing role, which needs the helper and vocabulary grants too.
        write = sa.text(
            'INSERT INTO public."case" (id, workspace_id, case_number, summary, description) '
            "VALUES (:id, :workspace, 900, 'synthetic runtime write', '')"
        )
        values = {"id": uuid.uuid4(), "workspace": WORKSPACE}
        # The vocabulary's workspace RLS policy applies to the trigger insert.
        scope = sa.text("SELECT set_config('app.current_workspace_id', :ws, true)")
        with operator.begin() as conn:
            conn.execute(sa.text(f'GRANT INSERT ON public."case" TO "{role}"'))
        with pytest.raises(DBAPIError, match="permission denied"):
            async with runtime.begin() as conn:
                await conn.execute(scope, {"ws": str(WORKSPACE)})
                await conn.execute(write, values)
        with operator.begin() as conn:
            conn.execute(
                sa.text(
                    f'GRANT EXECUTE ON FUNCTION public.tracecat_case_words_v1(text) TO "{role}"'
                )
            )
            conn.execute(
                sa.text(f'GRANT INSERT ON public.case_search_word TO "{role}"')
            )
        async with runtime.begin() as conn:
            await conn.execute(scope, {"ws": str(WORKSPACE)})
            await conn.execute(write, values)
    finally:
        await runtime.dispose()
        with operator.begin() as conn:
            conn.execute(sa.text(f'DROP OWNED BY "{role}"'))
            conn.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        operator.dispose()


@pytest.mark.anyio
async def test_readiness_rejects_a_missing_simple_token_mapping(
    search_database: Engine,
) -> None:
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    operator = sa.create_engine(search_database.url, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            await ensure_case_text_indexes(session)
        # The superuser alters only the uniquely owned disposable database.
        with operator.begin() as conn:
            conn.execute(
                sa.text(
                    "ALTER TEXT SEARCH CONFIGURATION pg_catalog.simple DROP MAPPING FOR asciiword"
                )
            )
        async with AsyncSession(engine) as session:
            with pytest.raises(LexicalSearchError) as error:
                await ensure_case_text_indexes(session)
            assert error.value.code == LexicalSearchErrorCode.CONFIGURATION_CHANGED
            assert await session.scalar(sa.select(sa.literal(1))) == 1
    finally:
        await engine.dispose()
        operator.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("use_trigram_threshold", [False, True])
async def test_live_timeout_and_settings_restore_on_rollback(
    search_database: Engine,
    use_trigram_threshold: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A short budget keeps the forced timeout fast.
    monkeypatch.setattr(config, "TRACECAT__CASE_SEARCH_STATEMENT_TIMEOUT_MS", 1500)
    url = search_database.url.set(drivername="postgresql+asyncpg")
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn, AsyncSession(conn) as session:
            with pytest.raises(TracecatQueryTimeoutError):
                async with (
                    session.begin(),
                    case_search_execution_context(
                        session, use_trigram_threshold=use_trigram_threshold
                    ),
                ):
                    await session.execute(sa.text("SELECT pg_sleep(2)"))
            assert await session.scalar(sa.text("SHOW statement_timeout")) == "0"
            await session.execute(
                sa.text("SELECT public.strict_word_similarity('', '')")
            )
            assert (
                await session.scalar(
                    sa.text("SHOW pg_trgm.strict_word_similarity_threshold")
                )
                == "0.5"
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_nested_fuzzy_timeout_preserves_outer_settings_and_recovers(
    search_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A short budget keeps the forced timeout fast.
    monkeypatch.setattr(config, "TRACECAT__CASE_SEARCH_STATEMENT_TIMEOUT_MS", 1500)
    engine = create_async_engine(
        search_database.url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    try:
        async with AsyncSession(engine) as session:
            async with session.begin():
                async with case_search_execution_context(session):
                    with pytest.raises(TracecatQueryTimeoutError):
                        async with (
                            session.begin_nested(),
                            case_search_execution_context(session),
                        ):
                            await session.execute(sa.text("SELECT pg_sleep(2)"))
                    assert await session.scalar(sa.select(sa.literal(1))) == 1
                    assert (
                        await session.scalar(sa.text("SHOW statement_timeout"))
                        == "1500ms"
                    )
                    assert (
                        await session.scalar(
                            sa.text("SHOW pg_trgm.strict_word_similarity_threshold")
                        )
                        == "0.65"
                    )
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
@pytest.mark.parametrize(
    "function", ["strict_word_similarity", "strict_word_similarity_commutator_op"]
)
async def test_readiness_checks_actual_native_execute_permissions(
    search_database: Engine, function: str
) -> None:
    """Use a limited database owner; the extension's C functions belong to postgres."""
    with search_database.connect() as conn:
        role = conn.scalar(sa.text("SELECT current_user"))
        assert role is not None
    operator = sa.create_engine(search_database.url, poolclass=NullPool)
    try:
        with operator.begin() as conn:
            conn.execute(
                sa.text(
                    f"REVOKE EXECUTE ON FUNCTION public.{function}(text,text) FROM PUBLIC"
                )
            )
        engine = create_async_engine(
            search_database.url.set(drivername="postgresql+asyncpg"),
            poolclass=NullPool,
            connect_args={"server_settings": {"role": role}},
        )
        try:
            async with engine.connect() as conn, AsyncSession(conn) as session:
                with pytest.raises(LexicalSearchError) as error:
                    async with session.begin_nested():
                        await ensure_case_text_indexes(session)
                assert error.value.code == LexicalSearchErrorCode.UNAVAILABLE
                assert await session.scalar(sa.select(sa.literal(1))) == 1
        finally:
            await engine.dispose()
    finally:
        with operator.begin() as conn:
            conn.execute(
                sa.text(
                    f"GRANT EXECUTE ON FUNCTION public.{function}(text,text) TO PUBLIC"
                )
            )
        operator.dispose()
