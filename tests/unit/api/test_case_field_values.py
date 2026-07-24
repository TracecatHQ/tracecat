from unittest.mock import AsyncMock

import pytest

from tracecat.auth.types import Role
from tracecat.cases.service import CaseFieldsService
from tracecat.tables.enums import SqlType
from tracecat.tables.exceptions import CaseFieldValidationError


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
        CaseFieldValidationError,
        match="Custom field 'text_field' expects TEXT \\(text\\). Received list.",
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
        CaseFieldValidationError,
        match="Custom field 'select_field' expects SELECT",
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
        CaseFieldValidationError,
        match="Custom field 'multi_select_field' expects MULTI_SELECT",
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
        CaseFieldValidationError,
        match="Custom field 'multi_select_field' expects MULTI_SELECT",
    ):
        await service.normalize_field_values({"multi_select_field": ["blocked"]})


@pytest.mark.anyio
async def test_normalize_field_values_rejects_empty_datetime(
    test_admin_role: Role,
) -> None:
    service = CaseFieldsService(AsyncMock(), test_admin_role)
    service.get_field_schema = AsyncMock(
        return_value={"observed_at": {"type": SqlType.TIMESTAMPTZ.value}}
    )
    service.editor.get_columns = AsyncMock(
        return_value=[{"name": "observed_at", "type": SqlType.TIMESTAMPTZ}]
    )

    with pytest.raises(CaseFieldValidationError) as exc_info:
        await service.normalize_field_values({"observed_at": ""})

    assert exc_info.value.detail == {
        "code": "CASE_FIELD_INVALID_VALUE",
        "message": (
            "Custom field 'observed_at' expects TIMESTAMPTZ (an ISO 8601 datetime "
            "(for example, 2026-01-30T12:00:00Z) or a Unix timestamp). Received "
            "an empty string; use null to leave a nullable field empty."
        ),
        "field": "observed_at",
        "expected_type": "TIMESTAMPTZ",
    }
