"""Native case lexical search through real public/internal transports."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, get_args
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tests.integration.test_case_search_reads import (
    capture_search_queries,
    population_counts,
)
from tests.integration.test_case_text_search_postgres import (
    load_search_migration,
    load_vector_migration,
    load_word_migration,
)
from tests.unit.test_case_aggregation import add_case
from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole, WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseStatus
from tracecat.cases.router import cases_router
from tracecat.cases.search_schemas import CaseQuery, CaseSearchRequest
from tracecat.cases.service import CasesService
from tracecat.db.engine import get_async_session
from tracecat.db.models import Case, Organization, Workspace
from tracecat.executor.action_gateway.app import create_app
from tracecat.pagination import PaginationError, PaginationErrorCode
from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.query.lexical_types import SearchMode
from tracecat.tiers import defaults as tier_defaults

pytest_plugins = ["tests.unit.test_case_aggregation"]
pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def provision_case_search(session: AsyncSession) -> None:
    """Install the case search revisions' objects in the disposable test database.

    The model already declares the vector and word columns and the vocabulary
    table, so only functions, triggers, and indexes are created here.
    """
    await session.execute(
        sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
    )
    migration = load_search_migration()
    for command in migration._FUNCTION_SQL_COMMANDS:
        await session.execute(sa.text(command))
    for name, expression in migration._INDEXES:
        await session.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS "{name}" ON public."case" USING gin ({expression.format(trgm_schema="public")})'
            )
        )
    for name, predicate in migration._FALLBACK_INDEXES:
        await session.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS "{name}" ON public."case" (workspace_id,id) WHERE {predicate}'
            )
        )
    vectors = load_vector_migration()
    await session.execute(
        sa.text(
            vectors._TRIGGER_FUNCTION_SQL.replace("CREATE ", "CREATE OR REPLACE ", 1)
        )
    )
    await session.execute(
        sa.text(vectors._TRIGGER_SQL.replace("CREATE ", "CREATE OR REPLACE ", 1))
    )
    for _, definition in vectors._VECTOR_INDEXES:
        await session.execute(
            sa.text(
                definition.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
            )
        )
    words = load_word_migration()
    for command in (words._WORDS_FUNCTION_SQL, words._TRIGGER_FUNCTION_SQL):
        await session.execute(
            sa.text(command.replace("CREATE ", "CREATE OR REPLACE ", 1))
        )
    await session.execute(
        sa.text(words._TRIGGER_SQL.replace("CREATE ", "CREATE OR REPLACE ", 1))
    )
    for _, definition in (*words._TABLE_INDEXES, *words._WORD_INDEXES):
        await session.execute(
            sa.text(
                definition.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
            )
        )


@pytest.fixture
async def lexical_service(aggregate_service: CasesService) -> CasesService:
    """Provision prerequisites only in the disposable test database."""
    await provision_case_search(aggregate_service.session)
    return aggregate_service


@pytest.fixture
async def lexical_client(
    aggregate_app, lexical_service: CasesService
) -> AsyncIterator[httpx.AsyncClient]:
    aggregate_app.include_router(cases_router)

    async def role():
        return lexical_service.role

    aggregate_app.dependency_overrides[
        get_args(WorkspaceActorRouteRole)[1].dependency
    ] = role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=aggregate_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def text_case(
    service: CasesService,
    summary: str,
    description: str = "",
    *,
    priority: CasePriority = CasePriority.HIGH,
) -> Case:
    case = await add_case(service, priority=priority)
    case.summary, case.description = summary, description
    await service.session.flush()
    return case


@pytest.mark.parametrize(
    ("term", "mode", "expected"),
    [
        ("powershell encoded", "full_text", ["phrase", "split", "operators"]),
        ('"powershell encoded"', "full_text", ["phrase"]),
        ("powershel", "full_text", ["split", "phrase", "operators"]),
        ("powershell encoded", "literal", ["phrase"]),
        ("abc%_value", "literal", ["literal"]),
        ("powershell OR encoded", "full_text", ["operators"]),
        ("-encoded", "full_text", ["operators", "phrase", "split"]),
    ],
)
async def test_get_post_public_internal_and_count_parity(
    lexical_service: CasesService,
    lexical_client: httpx.AsyncClient,
    term: str,
    mode: str,
    expected: list[str],
) -> None:
    cases = {
        "split": await text_case(lexical_service, "PowerShell", "encoded command"),
        "phrase": await text_case(lexical_service, "PowerShell encoded", "command"),
        "literal": await text_case(lexical_service, "synthetic", "abc%_value"),
        "operators": await text_case(
            lexical_service, "PowerShell OR encoded", "synthetic"
        ),
    }
    other = Workspace(
        name="synthetic other", organization_id=lexical_service.role.organization_id
    )
    lexical_service.session.add(other)
    await lexical_service.session.flush()
    foreign = await text_case(lexical_service, "PowerShell encoded", "abc%_value")
    foreign.workspace_id = other.id
    await lexical_service.session.flush()
    body: dict[str, Any] = {"search_term": term, "search_mode": mode}
    # Open dictionaries exercise actual HTTP parsing rather than an assumed AST.
    for prefix in ("", "/internal"):
        for method in ("get", "post"):
            if method == "get":
                response = await lexical_client.get(
                    f"{prefix}/cases/search", params=body
                )
            else:
                response = await lexical_client.post(
                    f"{prefix}/cases/search", json=body
                )
            assert response.status_code == 200, response.text
            result = response.json()
            assert {item["id"] for item in result["items"]} == {
                str(cases[name].id) for name in expected
            }
            assert result["total_estimate"] == len(expected)
            for item in result["items"]:
                assert item["search_match"]["matched_columns"]
                assert all(
                    len(excerpt) <= 240 for excerpt in item["search_match"]["excerpts"]
                )
                assert (item["search_match"]["score"] is None) == (mode == "literal")
        counts = await lexical_client.post(
            f"{prefix}/cases/search/aggregate", json=body
        )
        assert counts.status_code == 200, counts.text
        assert counts.json()["total"] == len(expected)
        assert sum(counts.json()["status_groups"].values()) == len(expected)
    counts = await lexical_client.get("/cases/search/aggregate", params=body)
    assert counts.status_code == 200, counts.text
    assert counts.json()["total"] == len(expected)


@pytest.mark.parametrize(
    ("term", "mode"),
    [
        ("powershell encoded", SearchMode.LITERAL),
        ('"powershell encoded"', SearchMode.FULL_TEXT),
        ("-encoded", SearchMode.FULL_TEXT),
        ("powershel", SearchMode.FULL_TEXT),
    ],
)
async def test_rows_counts_and_excerpts_only_configure_fuzzy_threshold_when_needed(
    lexical_service: CasesService, term: str, mode: SearchMode
) -> None:
    expected = await text_case(lexical_service, "PowerShell encoded")
    with capture_search_queries(lexical_service.session) as queries:
        page = await lexical_service.query_cases(
            CaseSearchRequest(search_term=term, search_mode=mode)
        )
        counts = await lexical_service.query_case_aggregates(
            CaseQuery(search_term=term, search_mode=mode)
        )
    assert [item.id for item in page.items] == [expected.id]
    assert page.total_estimate == counts.total == 1
    match = page.items[0].search_match
    assert match is not None
    assert match.excerpts and "PowerShell encoded" in match.excerpts[0]
    loads = [
        query
        for query in queries
        if query.statement == "SELECT strict_word_similarity('', '')"
    ]
    settings = [
        query
        for query in queries
        if "set_config('pg_trgm.strict_word_similarity_threshold'" in query.statement
    ]
    if term == "powershel":
        # Rows, standalone-protected nested excerpts, and counts load the GUC;
        # only the outer rows and count contexts change/restore the threshold.
        assert len(loads) == 3
        assert [query.parameters for query in settings] == [
            ("0.65",),
            ("0.5",),
        ] * 2
    else:
        assert loads == settings == []


async def test_filters_precede_relevance_pages_and_explicit_sorts(
    lexical_service: CasesService, lexical_client: httpx.AsyncClient
) -> None:
    for _ in range(110):
        await text_case(
            lexical_service, "powershell encoded", priority=CasePriority.LOW
        )
    split = await text_case(lexical_service, "PowerShell", "encoded")
    phrase = await text_case(lexical_service, "PowerShell encoded")
    request: dict[str, Any] = {
        "search_term": "powershell encoded",
        "search_mode": "full_text",
        "limit": 1,
        "filters": {"field": "priority", "op": "eq", "value": "high"},
        "include_count": False,
    }
    first = await lexical_client.post("/cases/search", json=request)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["items"][0]["id"] == str(phrase.id)
    assert first_body["total_estimate"] is None
    assert first_body["has_more"]
    second = await lexical_client.post(
        "/cases/search", json={**request, "cursor": first_body["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["items"][0]["id"] == str(split.id)
    backward = await lexical_client.post(
        "/cases/search", json={**request, "cursor": second.json()["prev_cursor"]}
    )
    assert backward.status_code == 200, backward.text
    assert backward.json()["items"][0]["id"] == str(phrase.id)
    explicit = await lexical_client.post(
        "/cases/search", json={**request, "order_by": "short_id", "sort": "asc"}
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["items"][0]["id"] == str(split.id)
    for changed in (
        {"search_columns": ["description"]},
        {"search_term": "powershell"},
        {"search_mode": "literal"},
    ):
        with pytest.raises(PaginationError):
            await lexical_service.query_cases(
                CaseSearchRequest.model_validate(
                    {**request, **changed, "cursor": first_body["next_cursor"]}
                )
            )

    with patch(
        "tracecat.cases.text_search_indexes.CASE_SEARCH_GENERATION",
        "synthetic-next-generation",
    ):
        with pytest.raises(PaginationError) as stale:
            await lexical_service.query_cases(
                CaseSearchRequest.model_validate(
                    {**request, "cursor": first_body["next_cursor"]}
                )
            )
        assert stale.value.code == PaginationErrorCode.CURSOR_MISMATCH
        fresh = await lexical_service.query_cases(
            CaseSearchRequest.model_validate(request)
        )
        assert fresh.items[0].id == phrase.id


async def test_selected_columns_and_literal_identifiers_are_not_fuzzy(
    lexical_service: CasesService, lexical_client: httpx.AsyncClient
) -> None:
    expected = await text_case(
        lexical_service, "PowerShell", "encoded command 192.0.2.10 " + "ab" * 16
    )
    text_id = await text_case(lexical_service, f"CASE-{expected.case_number:04d}")
    for body, expected_ids in (
        (
            {
                "search_term": "powershell",
                "search_mode": "full_text",
                "search_columns": ["description"],
            },
            [],
        ),
        (
            {
                "search_term": "encoded",
                "search_mode": "full_text",
                "search_columns": ["description"],
            },
            [expected.id],
        ),
        ({"search_term": "192.0.2.10", "search_mode": "literal"}, [expected.id]),
        ({"search_term": "ab" * 16, "search_mode": "full_text"}, [expected.id]),
        (
            {
                "search_term": f"CASE-{expected.case_number:04d}",
                "search_mode": "full_text",
            },
            [expected.id, text_id.id],
        ),
        (
            {
                "search_term": f"CASE-{expected.case_number:04d}",
                "search_mode": "literal",
            },
            [text_id.id],
        ),
    ):
        response = await lexical_client.post("/cases/search", json=body)
        assert response.status_code == 200, response.text
        assert {item["id"] for item in response.json()["items"]} == {
            str(case_id) for case_id in expected_ids
        }


async def test_legacy_matching_and_detail_count_projection_are_preserved(
    lexical_service: CasesService, lexical_client: httpx.AsyncClient
) -> None:
    expected = await text_case(lexical_service, "synthetic", "abc%_value")
    await text_case(lexical_service, "abcXXvalue")
    response = await lexical_client.get(
        "/cases/search", params={"search_term": "abc%_value"}
    )
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [str(expected.id)]
    assert response.json()["items"][0]["search_match"] is None
    excerpt_parameters: list[tuple[object, ...]] = []

    def record_excerpt(connection, cursor, statement, parameters, context, executemany):
        if "WITH page_sources" in statement:
            excerpt_parameters.append(parameters)

    engine = lexical_service.session.get_bind().engine
    event.listen(engine, "before_cursor_execute", record_excerpt)
    with (
        patch.object(
            tier_defaults,
            "DEFAULT_ENTITLEMENTS",
            tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
                update={"case_addons": False}
            ),
        ),
        capture_search_queries(lexical_service.session) as queries,
    ):
        page = await lexical_service.query_cases(
            CaseSearchRequest(
                search_term="abc%_value",
                search_mode=SearchMode.LITERAL,
                include_count=False,
                limit=1,
            )
        )
    assert page.total_estimate is None
    assert len(page.items) == 1
    assert page.items[0].payload is None
    assert not population_counts(queries)
    page_query = next(
        query.statement
        for query in queries
        if "_tracecat_page_key_0" in query.statement
    )
    projection = page_query.split("FROM", 1)[0]
    assert '"case".description' not in projection
    assert '"case".payload' not in projection
    event.remove(engine, "before_cursor_execute", record_excerpt)
    assert len(excerpt_parameters) == 1
    assert [expected.id] in excerpt_parameters[0]


@pytest.mark.parametrize(
    "options",
    [
        {"search_term": "literal", "search_columns": []},
        {"search_term": "literal", "search_columns": ["payload"]},
        {"search_term": "literal", "search_columns": ["summary", "summary"]},
        {"search_term": "on", "search_mode": "literal"},
        {"search_term": "powershell", "order_by": "relevance", "sort": "asc"},
    ],
)
async def test_invalid_lexical_contract_does_not_widen_scope(
    lexical_client: httpx.AsyncClient, options: dict[str, Any]
) -> None:
    response = await lexical_client.post(
        "/cases/search", json={"search_mode": "full_text", **options}
    )
    assert response.status_code == 422, response.text


async def test_missing_index_returns_typed_availability_and_legacy_keeps_working(
    lexical_service: CasesService, lexical_client: httpx.AsyncClient
) -> None:
    await lexical_service.session.execute(
        sa.text("DROP INDEX public.ix_case_summary_tsv")
    )
    response = await lexical_client.post(
        "/cases/search", json={"search_term": "powershell", "search_mode": "full_text"}
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "search_indexing"
    for path in (
        "/cases/search",
        "/internal/cases/search",
        "/cases/search/aggregate",
    ):
        response = await lexical_client.get(
            path, params={"search_term": "powershell", "search_mode": "full_text"}
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "search_indexing"
        assert await lexical_service.session.scalar(sa.select(sa.literal(1))) == 1
    legacy = await lexical_client.get(
        "/cases/search", params={"search_term": "powershell"}
    )
    assert legacy.status_code == 200, legacy.text
    assert await lexical_service.session.scalar(sa.select(sa.literal(1))) == 1


@pytest.fixture
async def timeout_service(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[CasesService]:
    """Own committed synthetic setup so a second connection can lock the table."""

    # A short budget makes the blocked statements time out inside the tests'
    # own asyncio deadline.
    monkeypatch.setattr(config, "TRACECAT__CASE_SEARCH_STATEMENT_TIMEOUT_MS", 1500)
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    org_id, workspace_id = uuid4(), uuid4()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(
                Organization(
                    id=org_id,
                    name="Synthetic timeout",
                    slug=f"synthetic-{org_id.hex}",
                    is_active=True,
                )
            )
            await session.flush()
            session.add(
                Workspace(
                    id=workspace_id, name="Synthetic timeout", organization_id=org_id
                )
            )
            await session.flush()
            service = CasesService(
                session,
                Role(
                    type="service",
                    service_id="tracecat-executor",
                    workspace_id=workspace_id,
                    organization_id=org_id,
                    scopes=frozenset({"case:read"}),
                ),
            )
            await provision_case_search(session)
            await text_case(service, "PowerShell encoded")
            await session.commit()
            yield service
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.delete(Case).where(Case.workspace_id == workspace_id))
            await conn.execute(sa.delete(Workspace).where(Workspace.id == workspace_id))
            await conn.execute(sa.delete(Organization).where(Organization.id == org_id))
        await engine.dispose()


@pytest.mark.parametrize("phase", ["count", "page", "status"])
@pytest.mark.parametrize("term", ["powershell", '"powershell encoded"'])
async def test_canonical_timeout_rolls_back_its_savepoint(
    timeout_service: CasesService, phase: str, term: str
) -> None:
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    # Load this connection's extension GUC before the blocked readiness query.
    await timeout_service.session.execute(
        sa.text("SELECT public.strict_word_similarity('', '')")
    )
    try:
        async with engine.connect() as blocker, blocker.begin():
            await blocker.execute(
                sa.text('LOCK TABLE public."case" IN ACCESS EXCLUSIVE MODE')
            )
            with pytest.raises(TracecatQueryTimeoutError) as error:
                async with asyncio.timeout(8):
                    if phase == "status":
                        await timeout_service.query_case_aggregates(
                            CaseQuery(
                                search_term=term,
                                search_mode=SearchMode.FULL_TEXT,
                            )
                        )
                    else:
                        await timeout_service.query_cases(
                            CaseSearchRequest(
                                search_term=term,
                                search_mode=SearchMode.FULL_TEXT,
                                include_count=phase == "count",
                            )
                        )
            assert error.value.code.value == "query_timeout"
            assert await timeout_service.session.scalar(sa.select(sa.literal(1))) == 1
            assert (
                await timeout_service.session.scalar(sa.text("SHOW statement_timeout"))
                == "0"
            )
            assert (
                await timeout_service.session.scalar(
                    sa.text("SHOW pg_trgm.strict_word_similarity_threshold")
                )
                == "0.5"
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "path",
    ["/cases/search", "/internal/cases/search", "/cases/search/aggregate"],
)
async def test_get_timeout_returns_typed_error_and_recovers_same_session(
    timeout_service: CasesService, path: str
) -> None:
    app = create_app()
    app.include_router(cases_router)

    async def role() -> Role:
        return timeout_service.role

    async def session() -> AsyncSession:
        return timeout_service.session

    for dependency in (ExecutorWorkspaceRole, WorkspaceActorRouteRole):
        app.dependency_overrides[get_args(dependency)[1].dependency] = role
    app.dependency_overrides[get_async_session] = session

    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    await timeout_service.session.execute(
        sa.text("SELECT public.strict_word_similarity('', '')")
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            async with engine.connect() as blocker, blocker.begin():
                await blocker.execute(
                    sa.text('LOCK TABLE public."case" IN ACCESS EXCLUSIVE MODE')
                )
                async with asyncio.timeout(8):
                    response = await client.get(
                        path,
                        params={
                            "search_term": "powershell",
                            "search_mode": "full_text",
                        },
                    )
                assert response.status_code == 422, response.text
                assert response.json()["detail"]["code"] == "query_timeout"
                assert (
                    await timeout_service.session.scalar(sa.select(sa.literal(1))) == 1
                )
                assert (
                    await timeout_service.session.scalar(
                        sa.text("SHOW statement_timeout")
                    )
                    == "0"
                )
                assert (
                    await timeout_service.session.scalar(
                        sa.text("SHOW pg_trgm.strict_word_similarity_threshold")
                    )
                    == "0.5"
                )
            response = await client.get(
                path,
                params={"search_term": "powershell", "search_mode": "full_text"},
            )
            assert response.status_code == 200, response.text
            if path.endswith("/aggregate"):
                assert response.json()["total"] == 1
            else:
                assert len(response.json()["items"]) == 1
    finally:
        await engine.dispose()


async def test_normalized_byte_overflow_has_typed_query_rejection_and_literal_recall(
    lexical_service: CasesService, lexical_client: httpx.AsyncClient
) -> None:
    term = "Ⱥ" * 683
    expected = await text_case(
        lexical_service, "synthetic normalized", "foo " + term + " bar"
    )
    for prefix in ("", "/internal"):
        invalid = await lexical_client.post(
            f"{prefix}/cases/search",
            json={"search_term": term, "search_mode": "full_text"},
        )
        assert invalid.status_code == 400, invalid.text
        assert "literal search" in invalid.json()["detail"]
        literal = await lexical_client.post(
            f"{prefix}/cases/search",
            json={"search_term": term, "search_mode": "literal"},
        )
        assert literal.status_code == 200, literal.text
        assert [item["id"] for item in literal.json()["items"]] == [str(expected.id)]
        words = await lexical_client.post(
            f"{prefix}/cases/search",
            json={"search_term": "foo", "search_mode": "full_text"},
        )
        assert words.status_code == 200, words.text
        assert [item["id"] for item in words.json()["items"]] == [str(expected.id)]
        phrase = await lexical_client.post(
            f"{prefix}/cases/search",
            json={"search_term": '"foo bar"', "search_mode": "full_text"},
        )
        assert phrase.status_code == 200, phrase.text
        assert phrase.json()["items"] == []


@pytest.mark.parametrize("reference_type", ["short_id", "uuid"])
@pytest.mark.parametrize("delete_exact", [False, True])
async def test_promoted_identity_and_native_mentions_share_one_stable_cursor(
    lexical_service: CasesService,
    reference_type: Literal["short_id", "uuid"],
    delete_exact: bool,
) -> None:
    """Real indexed search retains mentions, filters, counts and deleted anchors."""
    now = datetime.now(UTC)
    exact = await text_case(lexical_service, "Synthetic older exact case")
    exact.created_at = now - timedelta(days=60)
    exact.status = CaseStatus.CLOSED
    reference = exact.short_id if reference_type == "short_id" else str(exact.id)
    first = await text_case(
        lexical_service, "Synthetic description mention", f"Follow up on {reference}"
    )
    second = await text_case(lexical_service, f"Another synthetic mention {reference}")
    first.created_at = now - timedelta(days=1)
    second.created_at = now - timedelta(days=2)
    await lexical_service.session.flush()
    request = CaseSearchRequest(
        search_term=reference,
        search_mode=SearchMode.FULL_TEXT,
        promote_exact_match=True,
        start_time=now - timedelta(days=30),
        status=[CaseStatus.NEW],
        order_by="created_at",
        sort="desc",
        limit=1,
    )
    ids = []
    for index in range(3):
        page = await lexical_service.query_cases(request)
        assert len(page.items) == 1
        assert page.total_estimate == 2
        row = page.items[0]
        ids.append(row.id)
        assert row.is_exact_match is (index == 0)
        assert row.is_outside_filters is (index == 0)
        if index == 1:
            assert row.search_match is not None
            assert "description" in row.search_match.matched_columns
            assert any(
                reference.lower() in excerpt.lower()
                for excerpt in row.search_match.excerpts
            )
        if index == 0 and delete_exact:
            await lexical_service.session.delete(exact)
            await lexical_service.session.flush()
        request = request.model_copy(update={"cursor": page.next_cursor})
    assert ids == [exact.id, first.id, second.id]
    assert request.cursor is None
    counts = await lexical_service.query_case_aggregates(
        CaseQuery(
            search_term=reference,
            search_mode=SearchMode.FULL_TEXT,
            start_time=now - timedelta(days=30),
            status=[CaseStatus.NEW],
        )
    )
    assert counts.total == 2
    assert counts.status_groups.new == 2


async def test_reference_within_view_matches_identity_and_native_mentions_once(
    lexical_service: CasesService,
) -> None:
    """Identity need not occur in source text to remain inside the view filters."""
    exact = await text_case(lexical_service, "Synthetic case without its reference")
    mention = await text_case(lexical_service, f"Synthetic mention {exact.short_id}")
    page = await lexical_service.query_cases(
        CaseSearchRequest(
            search_term=exact.short_id,
            search_mode=SearchMode.FULL_TEXT,
            promote_exact_match=True,
            limit=10,
        )
    )
    assert [item.id for item in page.items] == [exact.id, mention.id]
    assert page.items[0].is_exact_match
    assert page.items[0].is_outside_filters is False
    assert page.items[0].search_match is not None
    assert "short_id" in page.items[0].search_match.matched_columns
    assert page.total_estimate == 2


async def test_literal_promotion_reports_ordinary_membership_for_group_counts(
    lexical_service: CasesService,
) -> None:
    """The exact row must not be subtracted from counts that never included it."""
    exact = await text_case(lexical_service, "Synthetic case without reference text")
    mention = await text_case(lexical_service, f"Synthetic mention {exact.short_id}")
    request = CaseSearchRequest(
        search_term=exact.short_id,
        search_mode=SearchMode.LITERAL,
        promote_exact_match=True,
        limit=10,
    )
    page = await lexical_service.query_cases(request)
    assert [item.id for item in page.items] == [exact.id, mention.id]
    assert page.items[0].is_exact_match
    assert page.items[0].is_outside_filters
    assert page.items[0].search_match is None
    assert page.items[1].search_match is not None
    assert page.items[1].search_match.matched_columns == ["summary"]
    assert page.items[1].search_match.excerpts
    assert page.total_estimate == 1
    counts = await lexical_service.query_case_aggregates(
        CaseQuery(
            search_term=exact.short_id,
            search_mode=SearchMode.LITERAL,
        )
    )
    assert counts.total == 1
    assert counts.status_groups.new == 1


@pytest.mark.parametrize("mode", [SearchMode.FULL_TEXT, SearchMode.LITERAL])
@pytest.mark.parametrize("case_addons_enabled", [False, True])
async def test_promoted_reference_preserves_explicit_hierarchy(
    lexical_service: CasesService,
    lexical_client: httpx.AsyncClient,
    mode: SearchMode,
    case_addons_enabled: bool,
) -> None:
    """Explicit top-level scope stays strict; disabled addons retain flat lists."""
    parent = await text_case(lexical_service, "Synthetic parent")
    exact = await text_case(lexical_service, "Synthetic sub-case")
    exact.parent_id = parent.id
    mention = await text_case(
        lexical_service, "Synthetic follow-up", f"Follow up on {exact.id}"
    )
    await lexical_service.session.flush()
    with patch.object(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
            update={"case_addons": case_addons_enabled}
        ),
    ):
        for hierarchy in ("all", "top_level"):
            response = await lexical_client.post(
                "/cases/search",
                json={
                    "search_term": str(exact.id),
                    "search_mode": mode.value,
                    "promote_exact_match": True,
                    "hierarchy": hierarchy,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            strict = case_addons_enabled and hierarchy == "top_level"
            expected = [mention.id] if strict else [exact.id, mention.id]
            assert [row["id"] for row in body["items"]] == [
                str(case_id) for case_id in expected
            ]
            assert body["items"][0]["is_exact_match"] is (not strict)
            assert body["total_estimate"] == (
                1 if strict or mode == SearchMode.LITERAL else 2
            )
