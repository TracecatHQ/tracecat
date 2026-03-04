import uuid

import pytest
from fastapi import HTTPException

from tracecat.cases.filters import parse_assignee_filter


def test_parse_assignee_filter_accepts_legacy_unassigned() -> None:
    result = parse_assignee_filter(["unassigned"])

    assert result["assignee_ids"] is None
    assert result["include_unassigned"] is True


def test_parse_assignee_filter_accepts_frontend_unassigned_sentinel() -> None:
    result = parse_assignee_filter(["__UNASSIGNED__"])

    assert result["assignee_ids"] is None
    assert result["include_unassigned"] is True


def test_parse_assignee_filter_accepts_uuid_and_unassigned() -> None:
    assignee_id = uuid.uuid4()

    result = parse_assignee_filter([str(assignee_id), "__UNASSIGNED__"])

    assert result["assignee_ids"] == [assignee_id]
    assert result["include_unassigned"] is True


def test_parse_assignee_filter_rejects_invalid_identifier() -> None:
    with pytest.raises(HTTPException, match="Invalid assignee_id"):
        parse_assignee_filter(["not-a-uuid"])
