from __future__ import annotations

import pytest
from sqlalchemy import select

from tracecat.api_keys.service import _apply_created_cursor
from tracecat.db.models import OrganizationApiKey
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginationParams


def test_apply_created_cursor_rejects_invalid_cursor() -> None:
    with pytest.raises(TracecatValidationError, match="Invalid cursor for API keys"):
        _apply_created_cursor(
            select(OrganizationApiKey),
            model=OrganizationApiKey,
            params=CursorPaginationParams(limit=20, cursor="bad-cursor"),
        )
