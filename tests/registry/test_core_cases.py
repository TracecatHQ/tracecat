"""Tests for core.cases UDFs in the registry."""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tracecat_registry.core.cases import (
    create_case,
    create_comment,
    get_case,
    list_cases,
    list_comments,
    search_cases,
    update_case,
    update_comment,
)

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    CaseCommentCreate,
    CaseCommentUpdate,
    CaseCreate,
    CaseUpdate,
)


@pytest.fixture
def mock_case():
    """Create a mock case for testing."""
    case = MagicMock()
    case.id = uuid.uuid4()
    case.summary = "Test Case"
    case.description = "Test Description"
    case.priority = CasePriority.MEDIUM
    case.severity = CaseSeverity.MEDIUM
    case.status = CaseStatus.NEW
    case.fields = MagicMock()
    case.fields.id = uuid.uuid4()
    # Add attributes needed for the updated get_case function
    case.created_at = datetime.now()
    case.updated_at = datetime.now()
    case.case_number = 1234

    # Set up model_dump to return a dict representation
    case.model_dump.return_value = {
        "id": str(case.id),
        "summary": case.summary,
        "description": case.description,
        "priority": case.priority.value,
        "severity": case.severity.value,
        "status": case.status.value,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "fields": {"field1": "value1", "field2": "value2"},
    }

    return case


@pytest.fixture
def mock_comment():
    """Create a mock comment for testing."""
    comment = MagicMock()
    comment.id = uuid.uuid4()
    comment.content = "Test Comment"
    comment.parent_id = None

    # Set up model_dump to return a dict representation
    comment.model_dump.return_value = {
        "id": str(comment.id),
        "content": comment.content,
        "parent_id": comment.parent_id,
    }

    return comment


@pytest.mark.anyio
class TestCoreCreate:
    """Test cases for the create UDF."""

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_create_case_success(self, mock_with_session, mock_case):
        """Test successful case creation with all fields."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.create_case.return_value = mock_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the create function
        result = await create_case(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
            fields={"field1": "value1", "field2": "value2"},
        )

        # Assert create_case was called with expected parameters
        mock_service.create_case.assert_called_once()
        call_args = mock_service.create_case.call_args[0][0]
        assert isinstance(call_args, CaseCreate)
        assert call_args.summary == "Test Case"
        assert call_args.description == "Test Description"
        assert call_args.priority == CasePriority.MEDIUM
        assert call_args.severity == CaseSeverity.MEDIUM
        assert call_args.status == CaseStatus.NEW
        assert call_args.fields == {"field1": "value1", "field2": "value2"}

        # Verify the result
        assert result == mock_case.model_dump.return_value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_create_case_no_fields(self, mock_with_session, mock_case):
        """Test case creation without custom fields."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.create_case.return_value = mock_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the create function without fields
        result = await create_case(
            summary="Test Case",
            description="Test Description",
            priority="medium",
            severity="medium",
            status="new",
        )

        # Assert create_case was called with expected parameters
        mock_service.create_case.assert_called_once()
        call_args = mock_service.create_case.call_args[0][0]
        assert isinstance(call_args, CaseCreate)
        assert call_args.fields is None

        # Verify the result
        assert result == mock_case.model_dump.return_value


@pytest.mark.anyio
class TestCoreUpdate:
    """Test cases for the update UDF."""

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_case_not_found(self, mock_with_session):
        """Test case not found during update."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = None

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function and expect an error
        case_id = str(uuid.uuid4())
        with pytest.raises(ValueError, match=f"Case with ID {case_id} not found"):
            await update_case(case_id=case_id, summary="New Summary")

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_all_fields(self, mock_with_session, mock_case):
        """Test updating all fields of a case."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case
        mock_service.fields.get_fields.return_value = {
            "field1": "value1",
            "field2": "value2",
        }

        updated_case = MagicMock()
        updated_case.model_dump.return_value = {
            "id": str(mock_case.id),
            "summary": "Updated Summary",
            "description": "Updated Description",
            "priority": CasePriority.HIGH.value,
            "severity": CaseSeverity.HIGH.value,
            "status": CaseStatus.IN_PROGRESS.value,
            "fields": {"field1": "new_value", "field2": "value2", "field3": "value3"},
        }
        mock_service.update_case.return_value = updated_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function
        result = await update_case(
            case_id=str(mock_case.id),
            summary="Updated Summary",
            description="Updated Description",
            priority="high",
            severity="high",
            status="in_progress",
            fields={"field1": "new_value", "field3": "value3"},
        )

        # Assert update_case was called with expected parameters
        mock_service.update_case.assert_called_once()
        case_arg, update_arg = mock_service.update_case.call_args[0]
        assert case_arg is mock_case
        assert isinstance(update_arg, CaseUpdate)
        assert update_arg.summary == "Updated Summary"
        assert update_arg.description == "Updated Description"
        assert update_arg.priority == CasePriority.HIGH
        assert update_arg.severity == CaseSeverity.HIGH
        assert update_arg.status == CaseStatus.IN_PROGRESS
        assert update_arg.fields == {"field1": "new_value", "field3": "value3"}

        # Verify the result
        assert result == updated_case.model_dump.return_value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_partial_base_data(self, mock_with_session, mock_case):
        """Test updating only some base fields (not all fields present)."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case

        updated_case = MagicMock()
        updated_case.model_dump.return_value = {
            "id": str(mock_case.id),
            "summary": "Updated Summary",
            "description": mock_case.description,  # Unchanged
            "priority": CasePriority.HIGH.value,
            "severity": mock_case.severity.value,  # Unchanged
            "status": mock_case.status.value,  # Unchanged
            "fields": {"field1": "value1", "field2": "value2"},  # Unchanged
        }
        mock_service.update_case.return_value = updated_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function with only some fields
        result = await update_case(
            case_id=str(mock_case.id),
            summary="Updated Summary",
            priority="high",
        )

        # Assert update_case was called with expected parameters
        mock_service.update_case.assert_called_once()
        case_arg, update_arg = mock_service.update_case.call_args[0]
        assert case_arg is mock_case
        assert isinstance(update_arg, CaseUpdate)
        assert update_arg.summary == "Updated Summary"
        assert update_arg.description is None  # Not updated
        assert update_arg.priority == CasePriority.HIGH
        assert update_arg.severity is None  # Not updated
        assert update_arg.status is None  # Not updated
        assert update_arg.fields is None  # Not updated

        # Check that only the specified fields are in the update parameters
        expected_params = {
            "summary": "Updated Summary",
            "priority": CasePriority.HIGH,
        }
        actual_params = {
            k: v for k, v in update_arg.model_dump().items() if v is not None
        }
        assert all(actual_params[k] == v for k, v in expected_params.items())

        # Verify the result
        assert result == updated_case.model_dump.return_value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_with_partial_field_data(self, mock_with_session, mock_case):
        """Test update with partial field data (fields={field1: <some value>})."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case
        mock_service.fields.get_fields.return_value = {
            "field1": "value1",
            "field2": "value2",
        }

        updated_case = MagicMock()
        updated_case.model_dump.return_value = {
            "id": str(mock_case.id),
            "summary": mock_case.summary,  # Unchanged
            "description": mock_case.description,  # Unchanged
            "priority": mock_case.priority.value,  # Unchanged
            "severity": mock_case.severity.value,  # Unchanged
            "status": mock_case.status.value,  # Unchanged
            "fields": {
                "field1": "new_value",
                "field2": "value2",
            },  # Only field1 updated
        }
        mock_service.update_case.return_value = updated_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function with only field data
        result = await update_case(
            case_id=str(mock_case.id),
            fields={"field1": "new_value"},  # Only update field1
        )

        # Assert update_case was called with expected parameters
        mock_service.update_case.assert_called_once()
        case_arg, update_arg = mock_service.update_case.call_args[0]
        assert case_arg is mock_case
        assert isinstance(update_arg, CaseUpdate)
        assert update_arg.summary is None  # Not updated
        assert update_arg.description is None  # Not updated
        assert update_arg.priority is None  # Not updated
        assert update_arg.severity is None  # Not updated
        assert update_arg.status is None  # Not updated
        assert update_arg.fields == {"field1": "new_value"}  # Only field1 updated

        # Ensure only fields parameter was passed
        params = update_arg.model_dump(exclude_unset=True)
        assert list(params.keys()) == ["fields"]

        # Verify the result
        assert result == updated_case.model_dump.return_value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_zeroing_out_field(self, mock_with_session, mock_case):
        """Test update with field data zeroing out a field (fields={field1: None})."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case
        mock_service.fields.get_fields.return_value = {
            "field1": "value1",
            "field2": "value2",
        }

        updated_case = MagicMock()
        updated_case.model_dump.return_value = {
            "id": str(mock_case.id),
            "summary": mock_case.summary,  # Unchanged
            "description": mock_case.description,  # Unchanged
            "priority": mock_case.priority.value,  # Unchanged
            "severity": mock_case.severity.value,  # Unchanged
            "status": mock_case.status.value,  # Unchanged
            "fields": {"field1": None, "field2": "value2"},  # field1 zeroed out
        }
        mock_service.update_case.return_value = updated_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function with field1=None
        result = await update_case(
            case_id=str(mock_case.id),
            fields={"field1": None},  # Zero out field1
        )

        # Assert update_case was called with expected parameters
        mock_service.update_case.assert_called_once()
        case_arg, update_arg = mock_service.update_case.call_args[0]
        assert case_arg is mock_case
        assert isinstance(update_arg, CaseUpdate)
        assert update_arg.fields == {"field1": None}  # field1 set to None

        # Verify the result
        assert result == updated_case.model_dump.return_value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_with_empty_field_data(self, mock_with_session, mock_case):
        """Test update with empty field data (fields={})."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case

        updated_case = MagicMock()
        updated_case.model_dump.return_value = {
            "id": str(mock_case.id),
            "summary": mock_case.summary,  # Unchanged
            "description": mock_case.description,  # Unchanged
            "priority": mock_case.priority.value,  # Unchanged
            "severity": mock_case.severity.value,  # Unchanged
            "status": mock_case.status.value,  # Unchanged
            "fields": None,  # Service will not try to update fields
        }
        mock_service.update_case.return_value = updated_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function with empty fields dict
        result = await update_case(
            case_id=str(mock_case.id),
            fields={},  # Empty dict - service will not try to update fields
        )

        # Assert update_case was called with expected parameters
        mock_service.update_case.assert_called_once()
        case_arg, update_arg = mock_service.update_case.call_args[0]
        assert case_arg is mock_case
        assert isinstance(update_arg, CaseUpdate)
        assert update_arg.fields == {}  # Action passes empty dict to service

        # Verify the result
        assert result == updated_case.model_dump.return_value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_update_base_attribute_to_none(self, mock_with_session, mock_case):
        """Test zeroing out a base attribute by setting it to None."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case

        updated_case = MagicMock()
        updated_case.model_dump.return_value = {
            "id": str(mock_case.id),
            "summary": mock_case.summary,  # Unchanged
            "description": None,  # Set to None
            "priority": mock_case.priority.value,  # Unchanged
            "severity": mock_case.severity.value,  # Unchanged
            "status": mock_case.status.value,  # Unchanged
            "fields": {"field1": "value1", "field2": "value2"},  # Unchanged
        }
        mock_service.update_case.return_value = updated_case

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update function with description=None
        result = await update_case(
            case_id=str(mock_case.id),
            description=None,
        )

        # Assert update_case was called with expected parameters
        mock_service.update_case.assert_called_once()
        case_arg, update_arg = mock_service.update_case.call_args[0]
        assert case_arg is mock_case
        assert isinstance(update_arg, CaseUpdate)
        assert update_arg.description is None  # Explicitly set to None

        # Check that description is included in the full model dump
        full_params = update_arg.model_dump()
        assert "description" in full_params
        assert full_params["description"] is None

        # Verify the result
        assert result == updated_case.model_dump.return_value


@pytest.mark.anyio
class TestCoreCreateComment:
    """Test cases for the create_comment UDF."""

    @patch("tracecat_registry.core.cases.CaseCommentsService.with_session")
    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_create_comment_success(
        self,
        mock_cases_with_session,
        mock_comments_with_session,
        mock_case,
        mock_comment,
    ):
        """Test successful comment creation."""
        # Set up the mock case service context manager
        mock_case_service = AsyncMock()
        mock_case_service.get_case.return_value = mock_case

        mock_case_ctx = AsyncMock()
        mock_case_ctx.__aenter__.return_value = mock_case_service
        mock_cases_with_session.return_value = mock_case_ctx

        # Set up the mock comment service context manager
        mock_comment_service = AsyncMock()
        mock_comment_service.create_comment.return_value = mock_comment

        mock_comment_ctx = AsyncMock()
        mock_comment_ctx.__aenter__.return_value = mock_comment_service
        mock_comments_with_session.return_value = mock_comment_ctx

        # Call the create_comment function
        result = await create_comment(
            case_id=str(mock_case.id),
            content="Test Comment",
        )

        # Assert get_case was called for the case
        mock_case_service.get_case.assert_called_once_with(mock_case.id)

        # Assert create_comment was called with expected parameters
        mock_comment_service.create_comment.assert_called_once()
        case_arg, comment_arg = mock_comment_service.create_comment.call_args[0]
        assert case_arg is mock_case
        assert isinstance(comment_arg, CaseCommentCreate)
        assert comment_arg.content == "Test Comment"
        assert comment_arg.parent_id is None

        # Verify the result
        assert result == mock_comment.model_dump.return_value


@pytest.mark.anyio
class TestCoreUpdateComment:
    """Test cases for the update_comment UDF."""

    @patch("tracecat_registry.core.cases.CaseCommentsService.with_session")
    async def test_update_comment_success(self, mock_with_session, mock_comment):
        """Test successful comment update."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_comment.return_value = mock_comment

        updated_comment = MagicMock()
        updated_comment.model_dump.return_value = {
            "id": str(mock_comment.id),
            "content": "Updated Comment",
            "parent_id": mock_comment.parent_id,
        }
        mock_service.update_comment.return_value = updated_comment

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update_comment function
        result = await update_comment(
            comment_id=str(mock_comment.id),
            content="Updated Comment",
        )

        # Assert update_comment was called with expected parameters
        mock_service.update_comment.assert_called_once()
        comment_arg, update_arg = mock_service.update_comment.call_args[0]
        assert comment_arg is mock_comment
        assert isinstance(update_arg, CaseCommentUpdate)
        assert update_arg.content == "Updated Comment"

        # Verify the result
        assert result == updated_comment.model_dump.return_value

    @patch("tracecat_registry.core.cases.CaseCommentsService.with_session")
    async def test_update_comment_zeroing_content(
        self, mock_with_session, mock_comment
    ):
        """Test zeroing out comment content by setting it to None."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_comment.return_value = mock_comment

        updated_comment = MagicMock()
        updated_comment.model_dump.return_value = {
            "id": str(mock_comment.id),
            "content": None,  # Set to None
            "parent_id": mock_comment.parent_id,
        }
        mock_service.update_comment.return_value = updated_comment

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the update_comment function with content=None
        result = await update_comment(
            comment_id=str(mock_comment.id),
            content=None,
        )

        # Assert update_comment was called with expected parameters
        mock_service.update_comment.assert_called_once()
        comment_arg, update_arg = mock_service.update_comment.call_args[0]
        assert comment_arg is mock_comment
        assert isinstance(update_arg, CaseCommentUpdate)
        assert update_arg.content is None

        # Verify the result
        assert result == updated_comment.model_dump.return_value


@pytest.mark.anyio
class TestCoreGetCase:
    """Test cases for the get_case UDF."""

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_get_case_success(self, mock_with_session, mock_case):
        """Test successful case retrieval."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case

        # Import SqlType for the test

        # Mock the fields service
        mock_service.fields = AsyncMock()
        # Mock get_fields to return some field values
        mock_service.fields.get_fields.return_value = {
            "field1": "value1",
            "field2": "value2",
        }

        # Mock list_fields to return field definitions
        field_def1 = {
            "name": "field1",
            "type": "TEXT",
            "nullable": True,
            "default": None,
            "comment": "Field 1",
        }
        field_def2 = {
            "name": "field2",
            "type": "TEXT",
            "nullable": True,
            "default": None,
            "comment": "Field 2",
        }
        mock_service.fields.list_fields.return_value = [field_def1, field_def2]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the get_case function
        result = await get_case(case_id=str(mock_case.id))

        # Assert get_case was called with expected parameters
        mock_service.get_case.assert_called_once_with(mock_case.id)

        # Assert fields methods were called
        mock_service.fields.get_fields.assert_called_once_with(mock_case)
        mock_service.fields.list_fields.assert_called_once()

        # Verify the core structure of the result
        assert result["id"] == str(mock_case.id)
        assert result["summary"] == mock_case.summary
        assert result["description"] == mock_case.description
        assert result["short_id"] == f"CASE-{mock_case.case_number:04d}"

        # Dates are returned as ISO strings when using model_dump(mode="json")
        assert "created_at" in result
        assert "updated_at" in result

        # Check status, priority, and severity are returned as string values
        assert result["status"] == mock_case.status.value
        assert result["priority"] == mock_case.priority.value
        assert result["severity"] == mock_case.severity.value

        # Check the fields array in the result
        assert len(result["fields"]) == 2
        assert result["fields"][0]["id"] == "field1"
        assert result["fields"][0]["value"] == "value1"
        assert result["fields"][1]["id"] == "field2"
        assert result["fields"][1]["value"] == "value2"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_get_case_not_found(self, mock_with_session):
        """Test case not found during retrieval."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.get_case.return_value = None

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the get_case function and expect an error
        case_id = str(uuid.uuid4())
        with pytest.raises(ValueError, match=f"Case with ID {case_id} not found"):
            await get_case(case_id=case_id)


@pytest.mark.anyio
class TestCoreListCases:
    """Test cases for the list_cases UDF."""

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_list_cases_no_params(self, mock_with_session, mock_case):
        """Test listing cases without any parameters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.list_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the list_cases function
        result = await list_cases()

        # Assert list_cases was called with expected parameters
        mock_service.list_cases.assert_called_once_with(
            limit=None, order_by=None, sort=None
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check that the required fields are present
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"
        assert "created_at" in case_result
        assert "updated_at" in case_result
        assert case_result["status"] == mock_case.status.value
        assert case_result["priority"] == mock_case.priority.value
        assert case_result["severity"] == mock_case.severity.value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_list_cases_with_limit(self, mock_with_session, mock_case):
        """Test listing cases with limit parameter."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.list_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the list_cases function with limit
        result = await list_cases(limit=5)

        # Assert list_cases was called with expected parameters
        mock_service.list_cases.assert_called_once_with(
            limit=5, order_by=None, sort=None
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_list_cases_with_ordering(self, mock_with_session, mock_case):
        """Test listing cases with ordering parameters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.list_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the list_cases function with ordering
        result = await list_cases(order_by="created_at", sort="desc")

        # Assert list_cases was called with expected parameters
        mock_service.list_cases.assert_called_once_with(
            limit=None, order_by="created_at", sort="desc"
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values match expected
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["status"] == mock_case.status.value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_list_cases_empty_result(self, mock_with_session):
        """Test listing cases when no cases exist."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.list_cases.return_value = []

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the list_cases function
        result = await list_cases()

        # Assert list_cases was called with expected parameters
        mock_service.list_cases.assert_called_once_with(
            limit=None, order_by=None, sort=None
        )

        # Verify the result is an empty list
        assert result == []


@pytest.mark.anyio
class TestCoreSearchCases:
    """Test cases for the search_cases UDF."""

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_no_params(self, mock_with_session, mock_case):
        """Test searching cases without any parameters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function
        result = await search_cases()

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once_with(
            search_term=None,
            status=None,
            priority=None,
            severity=None,
            limit=None,
            order_by=None,
            sort=None,
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check that the required fields are present
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"
        assert "created_at" in case_result
        assert "updated_at" in case_result
        assert case_result["status"] == mock_case.status.value
        assert case_result["priority"] == mock_case.priority.value
        assert case_result["severity"] == mock_case.severity.value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_search_term(self, mock_with_session, mock_case):
        """Test searching cases with search_term parameter."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with search_term
        result = await search_cases(search_term="test")

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once_with(
            search_term="test",
            status=None,
            priority=None,
            severity=None,
            limit=None,
            order_by=None,
            sort=None,
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_status(self, mock_with_session, mock_case):
        """Test searching cases with status parameter."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with status
        result = await search_cases(status="in_progress")

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once()
        call_args = mock_service.search_cases.call_args[1]
        assert call_args["search_term"] is None
        assert call_args["status"] == CaseStatus.IN_PROGRESS
        assert call_args["priority"] is None
        assert call_args["severity"] is None
        assert call_args["limit"] is None
        assert call_args["order_by"] is None
        assert call_args["sort"] is None

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_priority(self, mock_with_session, mock_case):
        """Test searching cases with priority parameter."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with priority
        result = await search_cases(priority="high")

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once()
        call_args = mock_service.search_cases.call_args[1]
        assert call_args["search_term"] is None
        assert call_args["status"] is None
        assert call_args["priority"] == CasePriority.HIGH
        assert call_args["severity"] is None
        assert call_args["limit"] is None
        assert call_args["order_by"] is None
        assert call_args["sort"] is None

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_severity(self, mock_with_session, mock_case):
        """Test searching cases with severity parameter."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with severity
        result = await search_cases(severity="critical")

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once()
        call_args = mock_service.search_cases.call_args[1]
        assert call_args["search_term"] is None
        assert call_args["status"] is None
        assert call_args["priority"] is None
        assert call_args["severity"] == CaseSeverity.CRITICAL
        assert call_args["limit"] is None
        assert call_args["order_by"] is None
        assert call_args["sort"] is None

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_limit(self, mock_with_session, mock_case):
        """Test searching cases with limit parameter."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with limit
        result = await search_cases(limit=5)

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once_with(
            search_term=None,
            status=None,
            priority=None,
            severity=None,
            limit=5,
            order_by=None,
            sort=None,
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_ordering(self, mock_with_session, mock_case):
        """Test searching cases with ordering parameters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with ordering
        result = await search_cases(order_by="created_at", sort="desc")

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once_with(
            search_term=None,
            status=None,
            priority=None,
            severity=None,
            limit=None,
            order_by="created_at",
            sort="desc",
        )

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values match expected
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["status"] == mock_case.status.value

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_with_multiple_params(
        self, mock_with_session, mock_case
    ):
        """Test searching cases with multiple parameters."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = [mock_case]

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function with multiple parameters
        result = await search_cases(
            search_term="test",
            status="new",
            priority="high",
            severity="critical",
            limit=10,
            order_by="updated_at",
            sort="asc",
        )

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once()
        call_args = mock_service.search_cases.call_args[1]
        assert call_args["search_term"] == "test"
        assert call_args["status"] == CaseStatus.NEW
        assert call_args["priority"] == CasePriority.HIGH
        assert call_args["severity"] == CaseSeverity.CRITICAL
        assert call_args["limit"] == 10
        assert call_args["order_by"] == "updated_at"
        assert call_args["sort"] == "asc"

        # Verify result structure
        assert len(result) == 1
        case_result = result[0]

        # Check key values
        assert case_result["id"] == str(mock_case.id)
        assert case_result["summary"] == mock_case.summary
        assert case_result["short_id"] == f"CASE-{mock_case.case_number:04d}"

    @patch("tracecat_registry.core.cases.CasesService.with_session")
    async def test_search_cases_empty_result(self, mock_with_session):
        """Test searching cases when no cases match the criteria."""
        # Set up the mock service context manager
        mock_service = AsyncMock()
        mock_service.search_cases.return_value = []

        # Set up the context manager's __aenter__ to return the mock service
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_service
        mock_with_session.return_value = mock_ctx

        # Call the search_cases function
        result = await search_cases(search_term="nonexistent")

        # Assert search_cases was called with expected parameters
        mock_service.search_cases.assert_called_once_with(
            search_term="nonexistent",
            status=None,
            priority=None,
            severity=None,
            limit=None,
            order_by=None,
            sort=None,
        )

        # Verify the result is an empty list
        assert result == []


@pytest.mark.anyio
class TestCoreListComments:
    """Test cases for the list_comments UDF."""

    @patch("tracecat_registry.core.cases.get_async_session_context_manager")
    async def test_list_comments_success(
        self,
        mock_get_session,
        mock_case,
    ):
        """Test successful retrieval of comments for a case."""
        # Set up the mock session context manager
        mock_session = AsyncMock()

        # Set up the case service with the session
        mock_case_service = AsyncMock()
        mock_case_service.get_case.return_value = mock_case

        # Set up the comments service with the session
        mock_comments_service = AsyncMock()

        # Create mock comments with users
        mock_comment1 = MagicMock()
        mock_comment1.model_dump.return_value = {
            "id": str(uuid.uuid4()),
            "content": "Comment 1",
            "parent_id": None,
        }

        mock_comment2 = MagicMock()
        mock_comment2.model_dump.return_value = {
            "id": str(uuid.uuid4()),
            "content": "Comment 2",
            "parent_id": None,
        }

        mock_user1 = MagicMock()
        mock_user1.model_dump.return_value = {
            "id": str(uuid.uuid4()),
            "username": "user1",
        }

        mock_user2 = None  # Anonymous comment

        # Set up return value for list_comments
        mock_comments_service.list_comments.return_value = [
            (mock_comment1, mock_user1),
            (mock_comment2, mock_user2),
        ]

        # Create mock constructors
        mock_cases_service_class = MagicMock()
        mock_cases_service_class.return_value = mock_case_service

        mock_comments_service_class = MagicMock()
        mock_comments_service_class.return_value = mock_comments_service

        # Mock the CasesService and CaseCommentsService constructors
        with patch(
            "tracecat_registry.core.cases.CasesService", mock_cases_service_class
        ):
            with patch(
                "tracecat_registry.core.cases.CaseCommentsService",
                mock_comments_service_class,
            ):
                # Set up the context manager's __aenter__ to return the mock session
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__.return_value = mock_session
                mock_get_session.return_value = mock_ctx

                # Call the list_comments function
                result = await list_comments(
                    case_id=str(mock_case.id),
                )

                # Assert services were initialized with session
                mock_cases_service_class.assert_called_once_with(mock_session)
                mock_comments_service_class.assert_called_once_with(mock_session)

                # Assert get_case was called for the case
                mock_case_service.get_case.assert_called_once_with(mock_case.id)

                # Assert list_comments was called with the case
                mock_comments_service.list_comments.assert_called_once_with(mock_case)

                # Verify the result
                assert len(result) == 2

                # First comment should have user info
                assert result[0]["content"] == "Comment 1"
                assert "user" in result[0]
                assert result[0]["user"]["username"] == "user1"

                # Second comment should have user as None
                assert result[1]["content"] == "Comment 2"
                assert "user" in result[1]
                assert result[1]["user"] is None

    @patch("tracecat_registry.core.cases.get_async_session_context_manager")
    async def test_list_comments_case_not_found(self, mock_get_session):
        """Test list_comments when case is not found."""
        # Set up the mock session
        mock_session = AsyncMock()

        # Set up the case service that returns None for get_case
        mock_case_service = AsyncMock()
        mock_case_service.get_case.return_value = None

        # Create mock constructor
        mock_cases_service_class = MagicMock()
        mock_cases_service_class.return_value = mock_case_service

        # Mock the CasesService constructor
        with patch(
            "tracecat_registry.core.cases.CasesService", mock_cases_service_class
        ):
            # Set up the context manager's __aenter__ to return the mock session
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value = mock_session
            mock_get_session.return_value = mock_ctx

            # Call the list_comments function and expect an error
            case_id = str(uuid.uuid4())
            with pytest.raises(ValueError, match=f"Case with ID {case_id} not found"):
                await list_comments(case_id=case_id)
