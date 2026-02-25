"""Tests for core.cases UDFs in the registry.

These tests verify the UDF layer behavior by mocking the SDK client context.
For end-to-end integration tests, see test_cases_characterization.py.
"""

import base64
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tracecat_registry.core.cases as cases_core
from tracecat_registry.core.cases import (
    add_case_tag,
    assign_user,
    create_case,
    create_comment,
    delete_case,
    download_attachment,
    get_attachment,
    get_attachment_download_url,
    get_case,
    list_attachments,
    list_cases,
    list_comments,
    remove_case_tag,
    search_cases,
    update_case,
    update_comment,
    upload_attachment,
    upload_attachment_from_url,
)
from tracecat_registry.sdk.exceptions import (
    TracecatValidationError,
)


@pytest.fixture
def mock_cases_client():
    """Create a mock cases client for SDK path testing."""
    return AsyncMock()


@pytest.fixture(autouse=True)
def mock_get_context(mock_cases_client: AsyncMock):
    """Mock get_context to return a fake context with mock cases client."""
    fake_ctx = SimpleNamespace(cases=mock_cases_client)
    with patch.object(cases_core, "get_context", return_value=fake_ctx):
        yield


@pytest.fixture
def mock_case_dict():
    """Create a mock case dict for testing."""
    case_id = uuid.uuid4()
    now = datetime.now(UTC)
    return {
        "id": str(case_id),
        "summary": "Test Case",
        "description": "Test Description",
        "priority": "medium",
        "severity": "medium",
        "status": "new",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "case_number": 1234,
        "short_id": "CASE-1234",
        "payload": {"alert_type": "security", "severity": "high"},
        "tags": [],
        "assignee": None,
        "fields": {"field1": "value1", "field2": "value2"},
    }


@pytest.fixture
def mock_comment_dict():
    """Create a mock comment dict for testing."""
    comment_id = uuid.uuid4()
    now = datetime.now(UTC)
    return {
        "id": str(comment_id),
        "content": "Test Comment",
        "parent_id": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "user": None,
    }


@pytest.fixture
def mock_tag_dict():
    """Create a mock tag dict for testing."""
    tag_id = uuid.uuid4()
    return {
        "id": str(tag_id),
        "name": "Test Tag",
        "ref": "test-tag",
        "color": "#FF0000",
    }


@pytest.mark.anyio
class TestCoreCreate:
    """Test cases for the create UDF."""

    async def test_create_case_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful case creation with all fields."""
        mock_cases_client.create_case_simple.return_value = mock_case_dict

        result = await create_case(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            fields={"field1": "value1", "field2": "value2"},
        )

        mock_cases_client.create_case_simple.assert_called_once_with(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            fields={"field1": "value1", "field2": "value2"},
        )
        assert result == mock_case_dict

    async def test_create_case_no_fields(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test case creation without custom fields."""
        mock_cases_client.create_case_simple.return_value = mock_case_dict

        result = await create_case(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
        )

        mock_cases_client.create_case_simple.assert_called_once_with(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
        )
        assert result == mock_case_dict

    async def test_create_case_with_tags(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test creating a case with tags."""
        mock_cases_client.create_case_simple.return_value = mock_case_dict

        result = await create_case(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            tags=["tag1", "tag2"],
        )

        mock_cases_client.create_case_simple.assert_called_once_with(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            tags=["tag1", "tag2"],
        )
        assert result == mock_case_dict

    async def test_create_case_with_payload(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test creating a case with payload."""
        mock_cases_client.create_case_simple.return_value = mock_case_dict

        result = await create_case(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            payload={"alert_type": "security"},
        )

        mock_cases_client.create_case_simple.assert_called_once_with(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            payload={"alert_type": "security"},
        )
        assert result == mock_case_dict


@pytest.mark.anyio
class TestCoreUpdate:
    """Test cases for the update UDF."""

    async def test_update_all_fields(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test updating all fields of a case."""
        updated_case = {**mock_case_dict, "summary": "Updated Summary"}
        mock_cases_client.update_case_simple.return_value = updated_case

        case_id = mock_case_dict["id"]
        result = await update_case(
            case_id=case_id,
            summary="Updated Summary",
            description="Updated Description",
            priority="high",
            severity="high",
            status="in_progress",
            fields={"field1": "new_value", "field3": "value3"},
        )

        mock_cases_client.update_case_simple.assert_called_once_with(
            case_id,
            summary="Updated Summary",
            description="Updated Description",
            priority="high",
            severity="high",
            status="in_progress",
            fields={"field1": "new_value", "field3": "value3"},
        )
        assert result == updated_case

    async def test_update_case_append_description(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test appending to the existing case description when requested."""
        updated_case = {
            **mock_case_dict,
            "description": "Existing description.\nNew details.",
        }
        mock_cases_client.update_case_simple.return_value = updated_case

        case_id = mock_case_dict["id"]
        result = await update_case(
            case_id=case_id,
            description="New details.",
            append=True,
        )

        mock_cases_client.update_case_simple.assert_called_once_with(
            case_id,
            description="New details.",
            append_description=True,
        )
        assert result == updated_case

    async def test_update_partial_base_data(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test updating only some base fields (not all fields present)."""
        updated_case = {
            **mock_case_dict,
            "summary": "Updated Summary",
            "priority": "high",
        }
        mock_cases_client.update_case_simple.return_value = updated_case

        case_id = mock_case_dict["id"]
        result = await update_case(
            case_id=case_id,
            summary="Updated Summary",
            priority="high",
        )

        mock_cases_client.update_case_simple.assert_called_once_with(
            case_id,
            summary="Updated Summary",
            priority="high",
        )
        assert result == updated_case

    async def test_update_with_partial_field_data(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test update with partial field data (fields={field1: <some value>})."""
        updated_case = {
            **mock_case_dict,
            "fields": {"field1": "new_value", "field2": "value2"},
        }
        mock_cases_client.update_case_simple.return_value = updated_case

        case_id = mock_case_dict["id"]
        result = await update_case(
            case_id=case_id,
            fields={"field1": "new_value"},
        )

        mock_cases_client.update_case_simple.assert_called_once_with(
            case_id,
            fields={"field1": "new_value"},
        )
        assert result == updated_case

    async def test_update_with_tags(self, mock_cases_client: AsyncMock, mock_case_dict):
        """Test updating a case with new tags."""
        updated_case = {**mock_case_dict, "tags": ["new-tag1", "new-tag2"]}
        mock_cases_client.update_case_simple.return_value = updated_case

        case_id = mock_case_dict["id"]
        result = await update_case(
            case_id=case_id,
            tags=["new-tag1", "new-tag2"],
        )

        mock_cases_client.update_case_simple.assert_called_once_with(
            case_id,
            tags=["new-tag1", "new-tag2"],
        )
        assert result == updated_case


@pytest.mark.anyio
class TestCoreCreateComment:
    """Test cases for the create_comment UDF."""

    async def test_create_comment_success(
        self, mock_cases_client: AsyncMock, mock_case_dict, mock_comment_dict
    ):
        """Test successful comment creation."""
        mock_cases_client.create_comment_simple.return_value = mock_comment_dict

        case_id = mock_case_dict["id"]
        result = await create_comment(
            case_id=case_id,
            content="Test Comment",
        )

        mock_cases_client.create_comment_simple.assert_called_once_with(
            case_id,
            content="Test Comment",
        )
        assert result == mock_comment_dict

    async def test_create_comment_with_parent(
        self, mock_cases_client: AsyncMock, mock_case_dict, mock_comment_dict
    ):
        """Test creating a reply comment."""
        parent_id = str(uuid.uuid4())
        reply_comment = {**mock_comment_dict, "parent_id": parent_id}
        mock_cases_client.create_comment_simple.return_value = reply_comment

        case_id = mock_case_dict["id"]
        result = await create_comment(
            case_id=case_id,
            content="Reply Comment",
            parent_id=parent_id,
        )

        mock_cases_client.create_comment_simple.assert_called_once_with(
            case_id,
            content="Reply Comment",
            parent_id=parent_id,
        )
        assert result == reply_comment


@pytest.mark.anyio
class TestCoreUpdateComment:
    """Test cases for the update_comment UDF."""

    async def test_update_comment_success(
        self, mock_cases_client: AsyncMock, mock_comment_dict
    ):
        """Test successful comment update."""
        updated_comment = {**mock_comment_dict, "content": "Updated Comment"}
        mock_cases_client.update_comment_simple.return_value = updated_comment

        comment_id = mock_comment_dict["id"]
        result = await update_comment(
            comment_id=comment_id,
            content="Updated Comment",
        )

        mock_cases_client.update_comment_simple.assert_called_once_with(
            comment_id,
            content="Updated Comment",
        )
        assert result == updated_comment


@pytest.mark.anyio
class TestCoreGetCase:
    """Test cases for the get_case UDF."""

    async def test_get_case_success(self, mock_cases_client: AsyncMock, mock_case_dict):
        """Test successful case retrieval."""
        mock_cases_client.get_case.return_value = mock_case_dict

        case_id = mock_case_dict["id"]
        result = await get_case(case_id=case_id)

        mock_cases_client.get_case.assert_called_once_with(case_id)
        assert result == mock_case_dict


@pytest.mark.anyio
class TestCoreListCases:
    """Test cases for the list_cases UDF."""

    async def test_list_cases_no_params(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test listing cases without any parameters."""
        mock_cases_client.list_cases.return_value = {"items": [mock_case_dict]}

        result = await list_cases()

        mock_cases_client.list_cases.assert_called_once_with(limit=100)
        assert result == [mock_case_dict]

    async def test_list_cases_with_limit(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test listing cases with limit parameter."""
        mock_cases_client.list_cases.return_value = {"items": [mock_case_dict]}

        result = await list_cases(limit=5)

        mock_cases_client.list_cases.assert_called_once_with(limit=5)
        assert result == [mock_case_dict]

    async def test_list_cases_with_ordering(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test listing cases with ordering parameters."""
        mock_cases_client.list_cases.return_value = {"items": [mock_case_dict]}

        result = await list_cases(order_by="created_at", sort="desc")

        mock_cases_client.list_cases.assert_called_once_with(
            limit=100, order_by="created_at", sort="desc"
        )
        assert result == [mock_case_dict]

    async def test_list_cases_ignores_cursor_params_when_paginate_false(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test listing cases ignores cursor controls when paginate is false."""
        mock_cases_client.list_cases.return_value = {"items": [mock_case_dict]}

        result = await list_cases(
            limit=25,
            cursor="cursor-1",
            reverse=True,
            order_by="updated_at",
            sort="asc",
        )

        mock_cases_client.list_cases.assert_called_once_with(
            limit=25,
            order_by="updated_at",
            sort="asc",
        )
        assert result == [mock_case_dict]

    async def test_list_cases_with_paginate_true(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test listing cases with pagination metadata."""
        mock_cases_client.list_cases.return_value = {
            "items": [mock_case_dict],
            "next_cursor": "cursor-1",
            "prev_cursor": None,
            "has_more": True,
            "has_previous": False,
            "total_estimate": 1,
        }

        result = await list_cases(
            limit=5,
            paginate=True,
            cursor="cursor-1",
            reverse=True,
        )

        mock_cases_client.list_cases.assert_called_once_with(
            limit=5,
            cursor="cursor-1",
            reverse=True,
        )
        assert isinstance(result, dict)
        assert result["items"] == [mock_case_dict]
        assert result["next_cursor"] == "cursor-1"

    async def test_list_cases_empty_result(self, mock_cases_client: AsyncMock):
        """Test listing cases when no cases exist."""
        mock_cases_client.list_cases.return_value = {"items": []}

        result = await list_cases()

        mock_cases_client.list_cases.assert_called_once_with(limit=100)
        assert result == []

    async def test_list_cases_limit_validation(self, mock_cases_client: AsyncMock):
        """Test that list_cases raises ValueError when limit exceeds maximum."""
        from tracecat_registry import config

        with pytest.raises(
            TracecatValidationError,
            match=f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}",
        ):
            await list_cases(limit=config.TRACECAT__LIMIT_CURSOR_MAX + 1)


@pytest.mark.anyio
class TestCoreSearchCases:
    """Test cases for search_cases UDF behavior."""

    async def test_search_cases_no_params(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should call the search client method."""
        mock_cases_client.search_cases.return_value = {"items": [mock_case_dict]}

        result = await search_cases()

        mock_cases_client.search_cases.assert_called_once_with(limit=100)
        assert result == [mock_case_dict]

    async def test_search_cases_with_limit(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should forward limit parameter."""
        mock_cases_client.search_cases.return_value = {"items": [mock_case_dict]}

        result = await search_cases(limit=5)

        mock_cases_client.search_cases.assert_called_once_with(limit=5)
        assert result == [mock_case_dict]

    async def test_search_cases_with_paginate_true(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should return pagination metadata when requested."""
        mock_cases_client.search_cases.return_value = {
            "items": [mock_case_dict],
            "next_cursor": "cursor-1",
            "prev_cursor": None,
            "has_more": True,
            "has_previous": False,
            "total_estimate": 1,
        }

        result = await search_cases(
            limit=5,
            paginate=True,
            cursor="cursor-1",
            reverse=True,
        )

        mock_cases_client.search_cases.assert_called_once_with(
            limit=5,
            cursor="cursor-1",
            reverse=True,
        )
        assert isinstance(result, dict)
        assert result["items"] == [mock_case_dict]
        assert result["next_cursor"] == "cursor-1"

    async def test_search_cases_with_aggregation_returns_full_response(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should return aggregation metadata when requested."""
        mock_cases_client.search_cases.return_value = {
            "items": [mock_case_dict],
            "next_cursor": None,
            "prev_cursor": None,
            "has_more": False,
            "has_previous": False,
            "total_estimate": 1,
            "aggregation": {
                "agg": "sum",
                "group_by": "status",
                "agg_field": None,
                "value": None,
                "buckets": [{"group": "new", "value": 1}],
            },
        }

        result = await search_cases(
            group_by="status",
            agg="sum",
        )

        mock_cases_client.search_cases.assert_called_once_with(
            limit=100,
            group_by="status",
            agg="sum",
        )
        assert isinstance(result, dict)
        assert "aggregation" in result
        aggregation = result["aggregation"]
        assert aggregation is not None
        assert aggregation["agg"] == "sum"
        assert aggregation["buckets"][0]["group"] == "new"

    async def test_search_cases_with_ordering(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should forward ordering parameters."""
        mock_cases_client.search_cases.return_value = {"items": [mock_case_dict]}

        result = await search_cases(order_by="created_at", sort="desc")

        mock_cases_client.search_cases.assert_called_once_with(
            order_by="created_at",
            sort="desc",
            limit=100,
        )
        assert result == [mock_case_dict]

    async def test_search_cases_ignores_cursor_params_when_paginate_false(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should ignore cursor controls when paginate is false."""
        mock_cases_client.search_cases.return_value = {"items": [mock_case_dict]}

        result = await search_cases(
            limit=25,
            cursor="cursor-1",
            reverse=True,
            order_by="updated_at",
            sort="asc",
        )

        mock_cases_client.search_cases.assert_called_once_with(
            limit=25,
            order_by="updated_at",
            sort="asc",
        )
        assert result == [mock_case_dict]

    async def test_search_cases_forwards_all_filters(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """search_cases should forward all filter inputs to search_cases."""
        mock_cases_client.search_cases.return_value = {"items": [mock_case_dict]}
        now = datetime.now(UTC)

        result = await search_cases(
            search_term="investigate",
            status="in_progress",
            priority="high",
            severity=["critical"],
            tags=["tag-one", "tag-two"],
            assignee_id=["unassigned"],
            dropdown=["environment:prod"],
            start_time=now,
            end_time=now,
            updated_before=now,
            updated_after=now,
            limit=25,
            order_by="tasks",
            sort="desc",
        )

        mock_cases_client.search_cases.assert_called_once_with(
            search_term="investigate",
            status=["in_progress"],
            priority=["high"],
            severity=["critical"],
            tags=["tag-one", "tag-two"],
            assignee_id=["unassigned"],
            dropdown=["environment:prod"],
            start_time=now,
            end_time=now,
            updated_before=now,
            updated_after=now,
            limit=25,
            order_by="tasks",
            sort="desc",
        )
        assert result == [mock_case_dict]

    async def test_search_cases_empty_result(self, mock_cases_client: AsyncMock):
        """search_cases should return empty list when search returns none."""
        mock_cases_client.search_cases.return_value = {"items": []}

        result = await search_cases()

        mock_cases_client.search_cases.assert_called_once_with(limit=100)
        assert result == []

    async def test_search_cases_limit_validation(self, mock_cases_client: AsyncMock):
        """Test that search_cases raises ValueError when limit exceeds maximum."""
        from tracecat_registry import config

        with pytest.raises(
            TracecatValidationError,
            match=f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}",
        ):
            await search_cases(limit=config.TRACECAT__LIMIT_CURSOR_MAX + 1)


@pytest.mark.anyio
class TestCoreListComments:
    """Test cases for the list_comments UDF."""

    async def test_list_comments_success(
        self, mock_cases_client: AsyncMock, mock_case_dict, mock_comment_dict
    ):
        """Test successful retrieval of comments for a case."""
        mock_cases_client.list_comments.return_value = [mock_comment_dict]

        case_id = mock_case_dict["id"]
        result = await list_comments(case_id=case_id)

        mock_cases_client.list_comments.assert_called_once_with(case_id)
        assert result == [mock_comment_dict]


@pytest.mark.anyio
class TestCoreDeleteCase:
    """Test cases for the delete_case UDF."""

    async def test_delete_case_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful case deletion."""
        mock_cases_client.delete_case.return_value = None

        case_id = mock_case_dict["id"]
        result = await delete_case(case_id=case_id)

        mock_cases_client.delete_case.assert_called_once_with(case_id)
        assert result is None


@pytest.mark.anyio
class TestCoreAssignUser:
    """Test cases for the assign_user UDF."""

    async def test_assign_user_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful user assignment to a case."""
        assignee_id = str(uuid.uuid4())
        updated_case = {**mock_case_dict, "assignee_id": assignee_id}
        mock_cases_client.assign_user_simple.return_value = updated_case

        case_id = mock_case_dict["id"]
        result = await assign_user(
            case_id=case_id,
            assignee_id=assignee_id,
        )

        mock_cases_client.assign_user_simple.assert_called_once_with(
            case_id,
            assignee_id=assignee_id,
        )
        assert result == updated_case


@pytest.mark.anyio
class TestCoreCaseTags:
    """Test cases for case tag management UDFs."""

    async def test_add_case_tag_success(
        self, mock_cases_client: AsyncMock, mock_case_dict, mock_tag_dict
    ):
        """Test successful tag addition to a case."""
        mock_cases_client.add_tag.return_value = mock_tag_dict

        case_id = mock_case_dict["id"]
        result = await add_case_tag(
            case_id=case_id,
            tag="test-tag",
        )

        mock_cases_client.add_tag.assert_called_once_with(
            case_id,
            tag_id="test-tag",
            create_if_missing=False,
        )
        assert result == mock_tag_dict

    async def test_add_case_tag_create_if_missing(
        self, mock_cases_client: AsyncMock, mock_case_dict, mock_tag_dict
    ):
        """Test tag addition with create_if_missing flag."""
        mock_cases_client.add_tag.return_value = mock_tag_dict

        case_id = mock_case_dict["id"]
        result = await add_case_tag(
            case_id=case_id,
            tag="new-tag",
            create_if_missing=True,
        )

        mock_cases_client.add_tag.assert_called_once_with(
            case_id,
            tag_id="new-tag",
            create_if_missing=True,
        )
        assert result == mock_tag_dict

    async def test_remove_case_tag_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful tag removal from a case."""
        mock_cases_client.remove_tag.return_value = None

        case_id = mock_case_dict["id"]
        result = await remove_case_tag(
            case_id=case_id,
            tag="test-tag",
        )

        mock_cases_client.remove_tag.assert_called_once_with(case_id, tag_id="test-tag")
        assert result is None


@pytest.mark.anyio
class TestCoreAttachments:
    """Test cases for attachment UDFs."""

    async def test_upload_attachment_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful attachment upload."""
        attachment_dict = {
            "id": str(uuid.uuid4()),
            "filename": "test.pdf",
            "content_type": "application/pdf",
            "size": 1024,
        }
        mock_cases_client.create_attachment.return_value = attachment_dict

        case_id = mock_case_dict["id"]
        content = b"test content"
        content_base64 = base64.b64encode(content).decode("utf-8")

        result = await upload_attachment(
            case_id=case_id,
            file_name="test.pdf",
            content_base64=content_base64,
            content_type="application/pdf",
        )

        mock_cases_client.create_attachment.assert_called_once_with(
            case_id,
            filename="test.pdf",
            content_base64=content_base64,
            content_type="application/pdf",
        )
        assert result == attachment_dict

    async def test_upload_attachment_invalid_base64(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test upload attachment with invalid base64."""
        case_id = mock_case_dict["id"]

        with pytest.raises(TracecatValidationError, match="Invalid base64 encoding"):
            await upload_attachment(
                case_id=case_id,
                file_name="test.pdf",
                content_base64="not-valid-base64!!!",
                content_type="application/pdf",
            )

    async def test_list_attachments_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful listing of attachments."""
        attachments = [
            {"id": str(uuid.uuid4()), "filename": "file1.pdf"},
            {"id": str(uuid.uuid4()), "filename": "file2.pdf"},
        ]
        mock_cases_client.list_attachments.return_value = attachments

        case_id = mock_case_dict["id"]
        result = await list_attachments(case_id=case_id)

        mock_cases_client.list_attachments.assert_called_once_with(case_id)
        assert result == attachments

    async def test_download_attachment_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful attachment download."""
        attachment_id = str(uuid.uuid4())
        download_data = {
            "filename": "test.pdf",
            "content_base64": base64.b64encode(b"content").decode(),
            "content_type": "application/pdf",
        }
        mock_cases_client.download_attachment.return_value = download_data

        case_id = mock_case_dict["id"]
        result = await download_attachment(
            case_id=case_id,
            attachment_id=attachment_id,
        )

        mock_cases_client.download_attachment.assert_called_once()
        assert result == download_data

    async def test_get_attachment_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful attachment metadata retrieval."""
        attachment_id = str(uuid.uuid4())
        attachment_data = {
            "id": attachment_id,
            "filename": "test.pdf",
            "content_type": "application/pdf",
        }
        mock_cases_client.get_attachment_metadata.return_value = attachment_data

        case_id = mock_case_dict["id"]
        result = await get_attachment(
            case_id=case_id,
            attachment_id=attachment_id,
        )

        mock_cases_client.get_attachment_metadata.assert_called_once()
        assert result == attachment_data

    async def test_get_attachment_download_url_success(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test successful presigned URL generation."""
        attachment_id = str(uuid.uuid4())
        presigned_url = "https://s3.example.com/presigned-url"
        mock_cases_client.get_attachment_presigned_url.return_value = presigned_url

        case_id = mock_case_dict["id"]
        result = await get_attachment_download_url(
            case_id=case_id,
            attachment_id=attachment_id,
        )

        mock_cases_client.get_attachment_presigned_url.assert_called_once()
        assert result == presigned_url

    async def test_get_attachment_download_url_with_expiry(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test presigned URL generation with custom expiry."""
        attachment_id = str(uuid.uuid4())
        presigned_url = "https://s3.example.com/presigned-url"
        mock_cases_client.get_attachment_presigned_url.return_value = presigned_url

        case_id = mock_case_dict["id"]
        result = await get_attachment_download_url(
            case_id=case_id,
            attachment_id=attachment_id,
            expiry=3600,
        )

        mock_cases_client.get_attachment_presigned_url.assert_called_once()
        call_kwargs = mock_cases_client.get_attachment_presigned_url.call_args[1]
        assert call_kwargs.get("expiry") == 3600
        assert result == presigned_url

    async def test_get_attachment_download_url_invalid_expiry(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test presigned URL with invalid expiry raises error."""
        attachment_id = str(uuid.uuid4())
        case_id = mock_case_dict["id"]

        with pytest.raises(
            TracecatValidationError, match="Expiry must be a positive number"
        ):
            await get_attachment_download_url(
                case_id=case_id,
                attachment_id=attachment_id,
                expiry=-1,
            )

    async def test_get_attachment_download_url_expiry_too_long(
        self, mock_cases_client: AsyncMock, mock_case_dict
    ):
        """Test presigned URL with expiry exceeding 24 hours raises error."""
        attachment_id = str(uuid.uuid4())
        case_id = mock_case_dict["id"]

        with pytest.raises(
            TracecatValidationError, match="Expiry cannot exceed 24 hours"
        ):
            await get_attachment_download_url(
                case_id=case_id,
                attachment_id=attachment_id,
                expiry=86401,  # 24 hours + 1 second
            )


@pytest.mark.anyio
class TestCoreUploadAttachmentFromURL:
    """Tests for upload_attachment_from_url registry action."""

    @patch("tracecat_registry.core.cases._upload_attachment", new_callable=AsyncMock)
    @patch("tracecat_registry.core.cases.httpx.AsyncClient")
    async def test_upload_attachment_from_url_success(
        self,
        mock_httpx_client,
        mock_upload_attachment,
        mock_case_dict,
    ):
        """Ensure files downloaded via HTTP are passed to the uploader."""
        mock_response = MagicMock()
        mock_response.content = b"file-bytes"
        mock_response.headers = {"Content-Type": "application/pdf"}

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        mock_upload_attachment.return_value = {"id": "attachment-id"}

        case_id = mock_case_dict["id"]
        result = await upload_attachment_from_url(
            case_id=case_id,
            url="https://example.com/docs/report.pdf",
            headers={"Authorization": "Bearer token"},
            file_name="incident-report.pdf",
        )

        mock_client.get.assert_awaited_once_with(
            "https://example.com/docs/report.pdf",
            headers={"Authorization": "Bearer token"},
        )
        mock_upload_attachment.assert_awaited_once_with(
            case_id,
            "incident-report.pdf",
            b"file-bytes",
            "application/pdf",
        )

        assert result == {"id": "attachment-id"}

    @patch("tracecat_registry.core.cases._upload_attachment", new_callable=AsyncMock)
    @patch("tracecat_registry.core.cases.httpx.AsyncClient")
    async def test_upload_attachment_from_url_infers_filename(
        self,
        mock_httpx_client,
        mock_upload_attachment,
        mock_case_dict,
    ):
        """Test that filename is inferred from URL when not provided."""
        mock_response = MagicMock()
        mock_response.content = b"file-bytes"
        mock_response.headers = {"Content-Type": "application/pdf"}

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        mock_upload_attachment.return_value = {"id": "attachment-id"}

        case_id = mock_case_dict["id"]
        result = await upload_attachment_from_url(
            case_id=case_id,
            url="https://example.com/docs/report.pdf",
        )

        # Filename should be inferred as "report.pdf"
        mock_upload_attachment.assert_awaited_once_with(
            case_id,
            "report.pdf",
            b"file-bytes",
            "application/pdf",
        )
        assert result == {"id": "attachment-id"}

    @patch("tracecat_registry.core.cases.httpx.AsyncClient")
    async def test_upload_attachment_from_url_empty_content(
        self,
        mock_httpx_client,
        mock_case_dict,
    ):
        """Test that empty response content raises an error."""
        mock_response = MagicMock()
        mock_response.content = b""
        mock_response.headers = {"Content-Type": "application/pdf"}

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        case_id = mock_case_dict["id"]
        with pytest.raises(TracecatValidationError, match="No content found"):
            await upload_attachment_from_url(
                case_id=case_id,
                url="https://example.com/docs/empty.pdf",
            )

    @patch("tracecat_registry.core.cases.httpx.AsyncClient")
    async def test_upload_attachment_from_url_no_content_type(
        self,
        mock_httpx_client,
        mock_case_dict,
    ):
        """Test that missing content type raises an error."""
        mock_response = MagicMock()
        mock_response.content = b"file-bytes"
        mock_response.headers = {}

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_httpx_client.return_value.__aenter__.return_value = mock_client

        case_id = mock_case_dict["id"]
        with pytest.raises(TracecatValidationError, match="No content type found"):
            await upload_attachment_from_url(
                case_id=case_id,
                url="https://example.com/docs/report.pdf",
            )
