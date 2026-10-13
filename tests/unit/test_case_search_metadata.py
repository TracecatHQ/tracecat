from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.search_metadata import validate_case_filter_metadata
from tracecat.cases.search_schemas import CaseQuery
from tracecat.db.models import (
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseTag,
    Organization,
    Workspace,
)
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.query.filters import Condition, FilterOp

pytestmark = pytest.mark.anyio
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000001")


@pytest.fixture
async def metadata_workspaces(
    session: AsyncSession, session_test_organization: Organization
) -> tuple[Workspace, Workspace]:
    local = Workspace(
        name="metadata-local", organization_id=session_test_organization.id
    )
    other = Workspace(
        name="metadata-other", organization_id=session_test_organization.id
    )
    session.add_all([local, other])
    await session.flush()
    return local, other


async def test_no_metadata_means_no_metadata_queries() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = await validate_case_filter_metadata(
        session=session,
        workspace_id=WORKSPACE_ID,
        filters=[Condition(field="priority", op=FilterOp.EQ, value="critical"), None],
        case_addons_enabled=False,
    )
    assert result == ()
    session.execute.assert_not_awaited()
    session.scalars.assert_not_awaited()


@pytest.mark.parametrize(
    "condition",
    [
        Condition(field="tags", op=FilterOp.IN, value="synthetic-tag"),
        Condition(field="tags", op=FilterOp.GT, value="synthetic-tag"),
        Condition(field="tags", op=FilterOp.CONTAINS, value=["synthetic-tag"]),
        Condition(
            field="dropdowns.verdict", op=FilterOp.IS_NULL, value="synthetic-option"
        ),
        Condition(field="dropdowns.verdict", op=FilterOp.IN, value=[True]),
        Condition(field="tags", op=FilterOp.CONTAINS, value="synthetic\x00tag"),
        Condition(
            field="dropdowns.verdict", op=FilterOp.EQ, value="synthetic\x00option"
        ),
    ],
)
async def test_shared_compiler_rejects_invalid_metadata_condition_before_querying(
    condition: Condition,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(TracecatValidationError):
        await validate_case_filter_metadata(
            session=session,
            workspace_id=WORKSPACE_ID,
            filters=[condition],
            case_addons_enabled=True,
        )
    session.execute.assert_not_awaited()
    session.scalars.assert_not_awaited()


async def test_negated_dropdown_requires_entitlement_before_querying() -> None:
    query = CaseQuery.model_validate(
        {
            "filters": {
                "not": {"field": "dropdowns.verdict", "op": "eq", "value": "selected"}
            }
        }
    )
    session = AsyncMock(spec=AsyncSession)
    with pytest.raises(EntitlementRequired):
        await validate_case_filter_metadata(
            session=session,
            workspace_id=WORKSPACE_ID,
            filters=[query.filters],
            case_addons_enabled=False,
        )
    session.execute.assert_not_awaited()
    session.scalars.assert_not_awaited()


async def test_aggregation_dropdown_refs_require_entitlement() -> None:
    with pytest.raises(EntitlementRequired):
        await validate_case_filter_metadata(
            session=AsyncMock(spec=AsyncSession),
            workspace_id=WORKSPACE_ID,
            filters=[],
            case_addons_enabled=False,
            extra_dropdown_refs=["verdict"],
        )


@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_null_dropdown_refs_never_query_and_preserve_entitlement_first(
    extra: bool, enabled: bool
) -> None:
    session = AsyncMock(spec=AsyncSession)
    expected_error = TracecatValidationError if enabled else EntitlementRequired
    with pytest.raises(expected_error):
        await validate_case_filter_metadata(
            session=session,
            workspace_id=WORKSPACE_ID,
            filters=[]
            if extra
            else [Condition(field="dropdowns.synthetic\x00ref", op=FilterOp.IS_NULL)],
            case_addons_enabled=enabled,
            extra_dropdown_refs=["synthetic\x00ref"] if extra else [],
        )
    session.execute.assert_not_awaited()
    session.scalars.assert_not_awaited()


async def test_batch_metadata_validation_preserves_separate_trees(
    session: AsyncSession, metadata_workspaces: tuple[Workspace, Workspace]
) -> None:
    local, _ = metadata_workspaces
    first = CaseDropdownDefinition(workspace_id=local.id, name="Verdict", ref="verdict")
    second = CaseDropdownDefinition(workspace_id=local.id, name="Region", ref="region")
    session.add_all(
        [
            CaseTag(workspace_id=local.id, name="First tag", ref="first-tag"),
            CaseTag(workspace_id=local.id, name="Second tag", ref="second-tag"),
            first,
            second,
        ]
    )
    await session.flush()
    session.add_all(
        [
            CaseDropdownOption(
                definition_id=first.id, label="Selected", ref="selected"
            ),
            CaseDropdownOption(definition_id=second.id, label="West", ref="west"),
        ]
    )
    await session.flush()
    query = CaseQuery.model_validate(
        {
            "filters": {
                "and": [
                    {"field": "tags", "op": "in", "value": ["first-tag", "second-tag"]},
                    {
                        "not": {
                            "field": "dropdowns.verdict",
                            "op": "eq",
                            "value": "selected",
                        }
                    },
                ]
            }
        }
    )
    legacy = Condition(field="dropdowns.region", op=FilterOp.IN, value=["west"])
    original = query.model_dump()
    with patch.object(session, "execute", wraps=session.execute) as execute:
        result = await validate_case_filter_metadata(
            session=session,
            workspace_id=local.id,
            filters=[query.filters, legacy],
            case_addons_enabled=True,
        )
    assert result == ("region", "verdict")
    assert execute.await_count == 2
    assert query.model_dump() == original


@pytest.mark.parametrize("include_known", [False, True])
async def test_missing_tag_under_negation_is_rejected(
    session: AsyncSession,
    metadata_workspaces: tuple[Workspace, Workspace],
    include_known: bool,
) -> None:
    local, _ = metadata_workspaces
    session.add(CaseTag(workspace_id=local.id, name="Known tag", ref="known-tag"))
    await session.flush()
    tags = ["known-tag", "missing-tag"] if include_known else ["missing-tag"]
    query = CaseQuery.model_validate(
        {"filters": {"not": {"field": "tags", "op": "in", "value": tags}}}
    )
    with pytest.raises(TracecatValidationError, match="missing-tag"):
        await validate_case_filter_metadata(
            session=session,
            workspace_id=local.id,
            filters=[query.filters],
            case_addons_enabled=True,
        )


async def test_other_workspace_metadata_cannot_satisfy_validation(
    session: AsyncSession, metadata_workspaces: tuple[Workspace, Workspace]
) -> None:
    local, other = metadata_workspaces
    foreign_dropdown = CaseDropdownDefinition(
        workspace_id=other.id, name="Foreign", ref="foreign"
    )
    session.add_all(
        [
            CaseTag(workspace_id=other.id, name="Foreign tag", ref="foreign-tag"),
            foreign_dropdown,
        ]
    )
    await session.flush()
    for condition in (
        Condition(field="tags", op=FilterOp.CONTAINS, value="foreign-tag"),
        Condition(field="dropdowns.foreign", op=FilterOp.IS_NULL),
    ):
        with pytest.raises(TracecatValidationError, match="Unknown"):
            await validate_case_filter_metadata(
                session=session,
                workspace_id=local.id,
                filters=[condition],
                case_addons_enabled=True,
            )


async def test_dropdown_option_from_another_definition_is_rejected(
    session: AsyncSession, metadata_workspaces: tuple[Workspace, Workspace]
) -> None:
    local, _ = metadata_workspaces
    first = CaseDropdownDefinition(workspace_id=local.id, name="Verdict", ref="verdict")
    second = CaseDropdownDefinition(workspace_id=local.id, name="Other", ref="other")
    session.add_all([first, second])
    await session.flush()
    session.add(
        CaseDropdownOption(definition_id=second.id, label="Selected", ref="selected")
    )
    await session.flush()
    with pytest.raises(TracecatValidationError, match="Unknown options"):
        await validate_case_filter_metadata(
            session=session,
            workspace_id=local.id,
            filters=[
                Condition(
                    field="dropdowns.verdict", op=FilterOp.NOT_IN, value=["selected"]
                )
            ],
            case_addons_enabled=True,
        )


async def test_unset_dropdown_and_empty_membership_keep_definition_validation(
    session: AsyncSession, metadata_workspaces: tuple[Workspace, Workspace]
) -> None:
    local, _ = metadata_workspaces
    session.add(
        CaseDropdownDefinition(workspace_id=local.id, name="Empty", ref="empty")
    )
    await session.flush()
    result = await validate_case_filter_metadata(
        session=session,
        workspace_id=local.id,
        case_addons_enabled=True,
        filters=[Condition(field="dropdowns.empty", op=FilterOp.IS_NULL)],
        extra_dropdown_refs=["empty"],
    )
    assert result == ("empty",)
    with pytest.raises(TracecatValidationError, match="Unknown case dropdown refs"):
        await validate_case_filter_metadata(
            session=session,
            workspace_id=local.id,
            case_addons_enabled=True,
            filters=[Condition(field="dropdowns.missing", op=FilterOp.IN, value=[])],
        )
