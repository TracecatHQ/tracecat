from unittest.mock import AsyncMock

import pytest

from tracecat.auth.types import Role
from tracecat.cases.service import CaseFieldsService
from tracecat.tables.enums import SqlType


@pytest.mark.anyio
async def test_normalize_field_values_rejects_structured_value_for_text_field(
    test_admin_role: Role,
) -> None:
    service = CaseFieldsService(AsyncMock(), test_admin_role)
    service.get_field_schema = AsyncMock(
        return_value={"text_field": {"type": SqlType.TEXT.value}}
    )
    service.editor.get_columns = AsyncMock(
        return_value=[{"name": "text_field", "type": SqlType.TEXT}]
    )

    with pytest.raises(
        ValueError,
        match="Custom field 'text_field' expects TEXT but received list.",
    ):
        await service.normalize_field_values({"text_field": ["alpha", "beta"]})


@pytest.mark.anyio
async def test_normalize_field_values_allows_scalar_for_text_field(
    test_admin_role: Role,
) -> None:
    service = CaseFieldsService(AsyncMock(), test_admin_role)
    service.get_field_schema = AsyncMock(
        return_value={"text_field": {"type": SqlType.TEXT.value}}
    )
    service.editor.get_columns = AsyncMock(
        return_value=[{"name": "text_field", "type": SqlType.TEXT}]
    )

    assert await service.normalize_field_values({"text_field": 42}) == {
        "text_field": "42"
    }


@pytest.mark.anyio
async def test_normalize_field_values_rejects_unknown_select_option(
    test_admin_role: Role,
) -> None:
    service = CaseFieldsService(AsyncMock(), test_admin_role)
    service.get_field_schema = AsyncMock(
        return_value={
            "select_field": {
                "type": SqlType.SELECT.value,
                "options": ["allowed", "also_allowed"],
            }
        }
    )
    service.editor.get_columns = AsyncMock(
        return_value=[{"name": "select_field", "type": SqlType.TEXT}]
    )

    with pytest.raises(
        ValueError,
        match="Value 'blocked' is not in the available options",
    ):
        await service.normalize_field_values({"select_field": "blocked"})


@pytest.mark.anyio
async def test_normalize_field_values_rejects_non_list_multi_select(
    test_admin_role: Role,
) -> None:
    service = CaseFieldsService(AsyncMock(), test_admin_role)
    service.get_field_schema = AsyncMock(
        return_value={
            "multi_select_field": {
                "type": SqlType.MULTI_SELECT.value,
                "options": ["allowed", "also_allowed"],
            }
        }
    )
    service.editor.get_columns = AsyncMock(
        return_value=[{"name": "multi_select_field", "type": SqlType.JSONB}]
    )

    with pytest.raises(
        ValueError,
        match="MULTI_SELECT values must be provided as a list of strings",
    ):
        await service.normalize_field_values({"multi_select_field": "allowed"})


@pytest.mark.anyio
async def test_normalize_field_values_rejects_unknown_multi_select_option(
    test_admin_role: Role,
) -> None:
    service = CaseFieldsService(AsyncMock(), test_admin_role)
    service.get_field_schema = AsyncMock(
        return_value={
            "multi_select_field": {
                "type": SqlType.MULTI_SELECT.value,
                "options": ["allowed", "also_allowed"],
            }
        }
    )
    service.editor.get_columns = AsyncMock(
        return_value=[{"name": "multi_select_field", "type": SqlType.JSONB}]
    )

    with pytest.raises(
        ValueError,
        match="Value\\(s\\) blocked are not in the available options",
    ):
        await service.normalize_field_values({"multi_select_field": ["blocked"]})
