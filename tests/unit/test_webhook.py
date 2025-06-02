from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest
from fastapi import Request
from fastapi.datastructures import FormData

from tracecat.webhooks.dependencies import parse_webhook_payload


class TestParseWebhookPayload:
    """Test cases for parse_webhook_payload function."""

    @pytest.mark.anyio
    async def test_empty_body_returns_none(self):
        """Test that empty body returns None."""
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"")

        result = await parse_webhook_payload(request, "application/json")
        assert result is None

    @pytest.mark.anyio
    async def test_json_content_type(self):
        """Test parsing JSON content type."""
        test_data = {"key": "value", "number": 42}
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "application/json")
        assert result == test_data

    @pytest.mark.anyio
    async def test_json_content_type_with_charset(self):
        """Test parsing JSON content type with charset parameter."""
        test_data = {"key": "value", "number": 42}
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "application/json; charset=utf-8")
        assert result == test_data

    @pytest.mark.anyio
    async def test_ndjson_content_type(self):
        """Test parsing NDJSON content type."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=ndjson_body)

        result = await parse_webhook_payload(request, "application/x-ndjson")
        assert result == test_data

    @pytest.mark.anyio
    async def test_ndjson_content_type_with_charset(self):
        """Test parsing NDJSON content type with charset parameter."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=ndjson_body)

        result = await parse_webhook_payload(
            request, "application/x-ndjson; charset=utf-8"
        )
        assert result == test_data

    @pytest.mark.anyio
    async def test_jsonlines_content_type(self):
        """Test parsing jsonlines content type."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=ndjson_body)

        result = await parse_webhook_payload(request, "application/jsonlines")
        assert result == test_data

    @pytest.mark.anyio
    async def test_jsonl_content_type_with_charset(self):
        """Test parsing jsonl content type with charset parameter."""
        test_data = [{"line": 1}, {"line": 2}]
        ndjson_body = b'{"line": 1}\n{"line": 2}'
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=ndjson_body)

        result = await parse_webhook_payload(
            request, "application/jsonl; charset=utf-8"
        )
        assert result == test_data

    @pytest.mark.anyio
    async def test_form_urlencoded_content_type(self):
        """Test parsing form-urlencoded content type."""
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"key=value&number=42")

        # Mock the form() method
        form_data = FormData([("key", "value"), ("number", "42")])
        request.form = AsyncMock(return_value=form_data)

        result = await parse_webhook_payload(
            request, "application/x-www-form-urlencoded"
        )
        assert result == {"key": "value", "number": "42"}

    @pytest.mark.anyio
    async def test_form_urlencoded_content_type_with_charset(self):
        """Test parsing form-urlencoded content type with charset parameter."""
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=b"key=value&number=42")

        # Mock the form() method
        form_data = FormData([("key", "value"), ("number", "42")])
        request.form = AsyncMock(return_value=form_data)

        result = await parse_webhook_payload(
            request, "application/x-www-form-urlencoded; charset=utf-8"
        )
        assert result == {"key": "value", "number": "42"}

    @pytest.mark.anyio
    async def test_case_insensitive_content_type(self):
        """Test that content type matching is case insensitive."""
        test_data = {"key": "value"}
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "APPLICATION/JSON; CHARSET=UTF-8")
        assert result == test_data

    @pytest.mark.anyio
    async def test_content_type_with_whitespace(self):
        """Test that content type handles extra whitespace."""
        test_data = {"key": "value"}
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=orjson.dumps(test_data))

        result = await parse_webhook_payload(
            request, "  application/json ; charset=utf-8  "
        )
        assert result == test_data

    @pytest.mark.anyio
    async def test_none_content_type_defaults_to_json(self):
        """Test that None content type defaults to JSON parsing."""
        test_data = {"key": "value"}
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=orjson.dumps(test_data))

        result = await parse_webhook_payload(request, None)
        assert result == test_data

    @pytest.mark.anyio
    async def test_unknown_content_type_defaults_to_json(self):
        """Test that unknown content type defaults to JSON parsing."""
        test_data = {"key": "value"}
        request = MagicMock(spec=Request)
        request.body = AsyncMock(return_value=orjson.dumps(test_data))

        result = await parse_webhook_payload(request, "text/plain")
        assert result == test_data
