import base64
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.pagination import (
    BaseCursorPaginator,
    CursorData,
    CursorPaginationParams,
)

pytestmark = pytest.mark.usefixtures("db")


class TestCursorPaginator:
    """Test the base cursor paginator functionality."""

    def test_encode_decode_cursor(self, session: AsyncSession):
        """Test cursor encoding and decoding."""
        paginator = BaseCursorPaginator(session)
        timestamp = datetime.now(UTC)
        entity_id = "test-id-123"

        # Test encoding with datetime sort value
        cursor = paginator.encode_cursor(
            entity_id, sort_column="created_at", sort_value=timestamp
        )
        assert isinstance(cursor, str)
        assert cursor  # Not empty

        # Test decoding
        decoded = paginator.decode_cursor(cursor)
        assert isinstance(decoded, CursorData)
        assert decoded.sort_column == "created_at"
        assert decoded.sort_value == timestamp
        assert decoded.id == entity_id

    def test_decode_invalid_cursor(self, session: AsyncSession):
        """Test decoding invalid cursors."""
        paginator = BaseCursorPaginator(session)

        # Test invalid base64
        with pytest.raises(ValueError, match="Invalid cursor format"):
            paginator.decode_cursor("invalid-base64!")

        # Test invalid JSON structure
        invalid_json = base64.urlsafe_b64encode(b"invalid json").decode()
        with pytest.raises(ValueError, match="Invalid cursor format"):
            paginator.decode_cursor(invalid_json)

        # Test missing fields
        incomplete_data = base64.urlsafe_b64encode(
            json.dumps({"ts": "2024-01-01T00:00:00Z"}).encode()
        ).decode()
        with pytest.raises(ValueError, match="Invalid cursor format"):
            paginator.decode_cursor(incomplete_data)

    def test_cursor_roundtrip_with_microseconds(self, session: AsyncSession):
        """Test that cursor encoding preserves microsecond precision."""
        paginator = BaseCursorPaginator(session)
        # Create timestamp with microseconds
        timestamp = datetime.now(UTC).replace(microsecond=123456)
        entity_id = "test-id"

        cursor = paginator.encode_cursor(
            entity_id, sort_column="created_at", sort_value=timestamp
        )
        decoded = paginator.decode_cursor(cursor)

        assert decoded.sort_column == "created_at"
        assert decoded.sort_value == timestamp
        assert isinstance(decoded.sort_value, datetime)
        assert decoded.sort_value.microsecond == 123456
        assert decoded.id == entity_id

    def test_cursor_pagination_params(self):
        """Test cursor pagination parameters."""
        # Test default values
        params = CursorPaginationParams()
        assert params.cursor is None
        assert params.limit == 20

        # Test with custom values
        params = CursorPaginationParams(cursor="test-cursor", limit=50)
        assert params.cursor == "test-cursor"
        assert params.limit == 50

        # Test validation
        with pytest.raises(ValueError):
            CursorPaginationParams(limit=0)  # Below minimum

        with pytest.raises(ValueError):
            CursorPaginationParams(limit=201)  # Above maximum

    def test_cursor_data_model(self):
        """Test CursorData model validation."""
        timestamp = datetime.now(UTC)
        entity_id = "test-id"

        cursor_data = CursorData(
            sort_column="created_at", sort_value=timestamp, id=entity_id
        )
        assert cursor_data.sort_column == "created_at"
        assert cursor_data.sort_value == timestamp
        assert cursor_data.id == entity_id

        # Test JSON serialization
        json_data = cursor_data.model_dump_json()
        assert isinstance(json_data, str)

        # Test deserialization
        parsed_data = json.loads(json_data)
        recreated = CursorData.model_validate(parsed_data)
        assert recreated.sort_column == "created_at"
        assert recreated.sort_value == timestamp
        assert recreated.id == entity_id
