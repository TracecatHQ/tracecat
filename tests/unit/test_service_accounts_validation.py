from __future__ import annotations

import pytest
from sqlalchemy import select

from tracecat.db.models import ServiceAccount, ServiceAccountApiKey
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginationParams
from tracecat.service_accounts.service import (
    _apply_api_key_created_cursor,
    _apply_created_cursor,
)


def test_apply_created_cursor_rejects_invalid_cursor() -> None:
    with pytest.raises(
        TracecatValidationError, match="Invalid cursor for service accounts"
    ):
        _apply_created_cursor(
            select(ServiceAccount),
            params=CursorPaginationParams(limit=20, cursor="bad-cursor"),
        )


def test_apply_api_key_created_cursor_rejects_invalid_cursor() -> None:
    with pytest.raises(
        TracecatValidationError, match="Invalid cursor for service account API keys"
    ):
        _apply_api_key_created_cursor(
            select(ServiceAccountApiKey),
            params=CursorPaginationParams(limit=20, cursor="bad-cursor"),
        )
