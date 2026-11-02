"""Live SQL budgets and compatibility semantics for batched search tags."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.filters import resolve_tag_ids
from tracecat.cases.search_schemas import CaseQuery
from tracecat.cases.service import CasesService
from tracecat.cases.tags.search import resolve_case_tag_filters
from tracecat.db.models import CaseTag, Organization, Workspace
from tracecat.exceptions import TracecatValidationError
from tracecat.query.filters import Condition

pytestmark = pytest.mark.anyio


@contextmanager
def capture_tag_queries(
    session: AsyncSession,
) -> Iterator[list[tuple[str, tuple[object, ...]]]]:
    """Record real SELECTs on the isolated engine, including bound candidates."""
    queries: list[tuple[str, tuple[object, ...]]] = []

    def record(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: tuple[object, ...],
        context: object,
        executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("SELECT"):
            queries.append((statement, parameters))

    engine = session.get_bind().engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield queries
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
async def tag_search_service(
    session: AsyncSession, session_test_organization: Organization
) -> CasesService:
    workspace = Workspace(
        name="synthetic-batch-tags", organization_id=session_test_organization.id
    )
    session.add(workspace)
    await session.flush()
    return CasesService(
        session,
        Role(
            type="service",
            service_id="tracecat-executor",
            workspace_id=workspace.id,
            organization_id=workspace.organization_id,
            scopes=frozenset({"case:read"}),
        ),
    )


async def test_many_duplicate_ref_name_and_uuid_inputs_use_one_query(
    tag_search_service: CasesService,
) -> None:
    service = tag_search_service
    tags = [
        CaseTag(
            workspace_id=service.workspace_id,
            name=f"Synthetic Tag {i}",
            ref=f"synthetic-tag-{i}",
        )
        for i in range(100)
    ]
    service.session.add_all(tags)
    await service.session.flush()
    aliases = [
        identifier for tag in tags for identifier in (tag.ref, tag.name, str(tag.id))
    ]
    inputs = (aliases * 4)[:1000]
    expected_ids = [tags[(i % len(aliases)) // 3].id for i in range(1000)]
    with capture_tag_queries(service.session) as queries:
        resolved = await resolve_case_tag_filters(
            session=service.session, workspace_id=service.workspace_id, tags=inputs
        )
    assert [tag.id for tag in resolved] == expected_ids
    assert len(queries) == 1
    assert "case_tag.workspace_id =" in queries[0][0]
    # 100 UUIDs + 200 distinct exact/slug refs + the mandatory workspace UUID.
    assert len(queries[0][1]) == 301
    assert service.workspace_id in queries[0][1]
    with capture_tag_queries(service.session) as canonical_queries:
        filters = await service._search_service().compatibility_filters(
            CaseQuery(tags=inputs)
        )
    assert len(canonical_queries) == 1
    assert isinstance(filters, Condition) and filters.field == "tags"
    assert filters.value == [tag.ref for tag in tags]
    with capture_tag_queries(service.session) as legacy_queries:
        ids = await resolve_tag_ids(
            tags=inputs, session=service.session, role=service.role
        )
    assert len(legacy_queries) == 1 and ids == expected_ids


async def test_exact_ref_precedes_slug_without_trimming_nonblank_input(
    tag_search_service: CasesService,
) -> None:
    service = tag_search_service
    exact = CaseTag(
        workspace_id=service.workspace_id, name="Synthetic exact", ref="Mixed Label"
    )
    fallback = CaseTag(
        workspace_id=service.workspace_id, name="Synthetic fallback", ref="mixed-label"
    )
    padded = CaseTag(
        workspace_id=service.workspace_id, name="Synthetic padded", ref=" Mixed Label "
    )
    service.session.add_all([exact, fallback, padded])
    await service.session.flush()
    with capture_tag_queries(service.session) as queries:
        resolved = await resolve_case_tag_filters(
            session=service.session,
            workspace_id=service.workspace_id,
            tags=["Mixed Label", "MIXED LABEL", " Mixed Label ", "", "  "],
        )
    assert [tag.id for tag in resolved] == [exact.id, fallback.id, padded.id]
    assert len(queries) == 1


@pytest.mark.parametrize("uuid_format", ["canonical", "uppercase", "hex"])
async def test_uuid_identifiers_never_fall_back_to_refs(
    tag_search_service: CasesService, uuid_format: str
) -> None:
    service = tag_search_service
    missing_id = uuid4()
    matching_ref = CaseTag(
        workspace_id=service.workspace_id,
        name="Synthetic UUID ref",
        ref=str(missing_id),
    )
    service.session.add(matching_ref)
    await service.session.flush()
    identifier = (
        str(missing_id)
        if uuid_format == "canonical"
        else (str(missing_id).upper() if uuid_format == "uppercase" else missing_id.hex)
    )
    with (
        capture_tag_queries(service.session) as queries,
        pytest.raises(TracecatValidationError) as error,
    ):
        await resolve_case_tag_filters(
            session=service.session,
            workspace_id=service.workspace_id,
            tags=[identifier],
        )
    assert str(error.value) == f"Unknown case tag filter: {identifier!r}"
    assert len(queries) == 1
    assert missing_id in queries[0][1]
    assert str(missing_id) not in queries[0][1]


@pytest.mark.parametrize("missing_kind", ["foreign_uuid", "foreign_ref", "missing"])
@pytest.mark.parametrize("include_known", [False, True])
async def test_missing_and_foreign_tags_reject_the_whole_filter_in_one_query(
    tag_search_service: CasesService, missing_kind: str, include_known: bool
) -> None:
    service = tag_search_service
    other = Workspace(
        name="synthetic-batch-foreign", organization_id=service.organization_id
    )
    service.session.add(other)
    await service.session.flush()
    known = CaseTag(
        workspace_id=service.workspace_id, name="Synthetic local", ref="local"
    )
    foreign = CaseTag(workspace_id=other.id, name="Synthetic foreign", ref="foreign")
    service.session.add_all([known, foreign])
    await service.session.flush()
    identifier = (
        str(foreign.id)
        if missing_kind == "foreign_uuid"
        else (foreign.ref if missing_kind == "foreign_ref" else "missing-tag")
    )
    inputs = [known.ref, identifier] if include_known else [identifier]
    for entry in ("batch", "canonical", "legacy"):
        expected_error = HTTPException if entry == "legacy" else TracecatValidationError
        with (
            capture_tag_queries(service.session) as queries,
            pytest.raises(expected_error) as error,
        ):
            if entry == "batch":
                await resolve_case_tag_filters(
                    session=service.session,
                    workspace_id=service.workspace_id,
                    tags=inputs,
                )
            elif entry == "canonical":
                await service._search_service().compatibility_filters(
                    CaseQuery(tags=inputs)
                )
            else:
                await resolve_tag_ids(
                    tags=inputs, session=service.session, role=service.role
                )
        assert len(queries) == 1
        message = f"Unknown case tag filter: {identifier!r}"
        if isinstance(error.value, HTTPException):
            assert error.value.status_code == 400 and error.value.detail == message
        else:
            assert str(error.value) == message


@pytest.mark.parametrize("tags", [None, [], ["", "  ", "\t"]])
async def test_blank_tag_inputs_need_no_query(
    tag_search_service: CasesService, tags: list[str] | None
) -> None:
    service = tag_search_service
    with capture_tag_queries(service.session) as queries:
        assert (
            await resolve_case_tag_filters(
                session=service.session, workspace_id=service.workspace_id, tags=tags
            )
            == []
        )
        assert (
            await service._search_service().compatibility_filters(CaseQuery(tags=tags))
            is None
        )
        assert (
            await resolve_tag_ids(tags=tags, session=service.session, role=service.role)
            is None
        )
    assert queries == []


async def test_unknown_identifiers_report_first_input_after_known_aliases(
    tag_search_service: CasesService,
) -> None:
    service = tag_search_service
    with pytest.raises(TracecatValidationError) as error:
        await resolve_case_tag_filters(
            session=service.session,
            workspace_id=service.workspace_id,
            tags=["first-missing", "second-missing", "first-missing"],
        )
    assert str(error.value) == "Unknown case tag filter: 'first-missing'"


@pytest.mark.parametrize("tags", [["\x00"], ["synthetic\x00tag"], ["known", "bad\x00"]])
async def test_null_tag_identifiers_fail_before_any_lookup(
    tag_search_service: CasesService, tags: list[str]
) -> None:
    service = tag_search_service
    for entry in ("batch", "canonical", "legacy"):
        expected_error = HTTPException if entry == "legacy" else TracecatValidationError
        with (
            capture_tag_queries(service.session) as queries,
            pytest.raises(expected_error) as error,
        ):
            if entry == "batch":
                await resolve_case_tag_filters(
                    session=service.session,
                    workspace_id=service.workspace_id,
                    tags=tags,
                )
            elif entry == "canonical":
                await service._search_service().compatibility_filters(
                    CaseQuery(tags=tags)
                )
            else:
                await resolve_tag_ids(
                    tags=tags, session=service.session, role=service.role
                )
        assert queries == []
        if isinstance(error.value, HTTPException):
            assert error.value.status_code == 400
        assert await service.session.scalar(select(1)) == 1
