"""Tests for HTTP actions."""

import httpx
import pytest
import respx
from tenacity import RetryError
from tracecat_registry.base.core.http import (
    http_poll,
    http_request,
    httpx_to_response,
)

from tracecat.types.exceptions import TracecatException


# Test fixtures
@pytest.fixture
def mock_response() -> httpx.Response:
    """Create a mock HTTP response."""
    return httpx.Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        json={"message": "success"},
    )


@pytest.fixture
def mock_text_response() -> httpx.Response:
    """Create a mock HTTP text response."""
    return httpx.Response(
        status_code=200,
        headers={"Content-Type": "text/plain"},
        text="Hello, World!",
    )


@pytest.fixture
def mock_no_content_response() -> httpx.Response:
    """Create a mock HTTP 204 response."""
    return httpx.Response(
        status_code=204,
        headers={},
    )


# Test helper functions
def test_httpx_to_response_json(mock_response: httpx.Response) -> None:
    """Test converting JSON response to HTTPResponse."""
    result = httpx_to_response(mock_response)
    assert isinstance(result, dict)
    assert result["status_code"] == 200
    assert result["data"] == {"message": "success"}
    assert "content-type" in result["headers"]


def test_httpx_to_response_text(mock_text_response: httpx.Response) -> None:
    """Test converting text response to HTTPResponse."""
    result = httpx_to_response(mock_text_response)
    assert isinstance(result, dict)
    assert result["status_code"] == 200
    assert result["data"] == "Hello, World!"
    assert "content-type" in result["headers"]


def test_httpx_to_response_no_content(mock_no_content_response: httpx.Response) -> None:
    """Test converting 204 response to HTTPResponse."""
    result = httpx_to_response(mock_no_content_response)
    assert isinstance(result, dict)
    assert result["status_code"] == 204
    assert result["data"] is None


# Test HTTP request function
@pytest.mark.anyio
@respx.mock
async def test_http_request_success() -> None:
    """Test successful HTTP request using respx."""
    route = respx.get("https://api.example.com").mock(
        return_value=httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            json={"message": "success"},
        )
    )

    result = await http_request(
        url="https://api.example.com",
        method="GET",
        headers={"Accept": "application/json"},
    )

    assert route.called
    assert isinstance(result, dict)
    assert result["status_code"] == 200
    assert result["data"] == {"message": "success"}


@pytest.mark.anyio
@respx.mock
async def test_http_request_timeout() -> None:
    """Test HTTP request timeout."""
    route = respx.get("https://api.example.com").mock(
        side_effect=httpx.ReadTimeout("Timeout")
    )

    with pytest.raises(httpx.ReadTimeout):
        await http_request(
            url="https://api.example.com",
            method="GET",
            timeout=1.0,
        )

    assert route.called


@pytest.mark.anyio
@respx.mock
async def test_http_request_error() -> None:
    """Test HTTP request error handling."""
    route = respx.get("https://api.example.com").mock(
        return_value=httpx.Response(status_code=404, json={"error": "Not found"})
    )

    with pytest.raises(Exception) as excinfo:
        await http_request(
            url="https://api.example.com",
            method="GET",
        )

    assert route.called
    assert "404" in str(excinfo.value)
    assert "Not found" in str(excinfo.value)


@pytest.mark.anyio
@respx.mock
async def test_http_request_with_auth() -> None:
    """Test HTTP request with basic authentication."""
    route = respx.get("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"message": "authenticated"})
    )

    result = await http_request(
        url="https://api.example.com",
        method="GET",
        auth={"username": "user", "password": "pass"},
    )

    assert route.called
    assert route.calls.last.request.headers["Authorization"].startswith("Basic ")
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_with_params() -> None:
    """Test HTTP request with query parameters."""
    route = respx.get("https://api.example.com", params={"key": "value"}).mock(
        return_value=httpx.Response(status_code=200)
    )

    result = await http_request(
        url="https://api.example.com",
        method="GET",
        params={"key": "value"},
    )

    assert route.called
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_with_form_data() -> None:
    """Test HTTP request with form data."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200)
    )

    form_data = {"field": "value"}
    result = await http_request(
        url="https://api.example.com",
        method="POST",
        form_data=form_data,
    )

    assert route.called
    assert route.calls.last.request.content == b"field=value"
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_with_json_payload() -> None:
    """Test HTTP request with JSON payload."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200)
    )

    payload = {"data": "value"}
    result = await http_request(
        url="https://api.example.com",
        method="POST",
        payload=payload,
    )

    assert route.called
    assert route.calls.last.request.content.replace(b" ", b"") == b'{"data":"value"}'
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_failure() -> None:
    """Test HTTP request with server error response."""
    route = respx.get("https://api.example.com").mock(
        return_value=httpx.Response(
            status_code=500,
            headers={"Content-Type": "application/json"},
            json={"error": "Internal Server Error"},
        )
    )

    with pytest.raises(TracecatException) as excinfo:
        await http_request(
            url="https://api.example.com",
            method="GET",
        )

    assert route.called
    value = str(excinfo.value)
    assert "500" in value
    assert "Internal Server Error" in value


@pytest.mark.anyio
@respx.mock
async def test_http_request_bad_request() -> None:
    """Test HTTP request with 400 Bad Request response."""
    route = respx.get("https://api.example.com").mock(
        return_value=httpx.Response(
            status_code=400,
            headers={"Content-Type": "application/json"},
            json={"error": "Bad Request", "message": "Invalid parameters"},
        )
    )

    with pytest.raises(TracecatException) as excinfo:
        await http_request(
            url="https://api.example.com",
            method="GET",
        )

    assert route.called
    value = str(excinfo.value)
    assert "400" in value
    assert "Bad Request" in value
    assert "Invalid parameters" in value


# Test HTTP polling function
@pytest.mark.anyio
@respx.mock
async def test_http_poll_retry_codes() -> None:
    """Test HTTP polling with retry codes."""
    route = respx.get("https://api.example.com").mock(
        side_effect=[
            httpx.Response(status_code=202),  # First attempt - retry
            httpx.Response(status_code=202),  # Second attempt - retry
            httpx.Response(  # Final attempt - success
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"message": "success"},
            ),
        ]
    )

    result = await http_poll(
        url="https://api.example.com",
        method="GET",
        poll_retry_codes=202,
        poll_interval=0.1,
        poll_max_attempts=3,
    )

    assert route.call_count == 3
    assert isinstance(result, dict)
    assert result["status_code"] == 200
    assert result["data"] == {"message": "success"}


@pytest.mark.anyio
@respx.mock
async def test_http_poll_condition() -> None:
    """Test HTTP polling with custom condition."""
    route = respx.get("https://api.example.com").mock(
        side_effect=[
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": "pending"},
            ),
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": "pending"},
            ),
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": "completed"},
            ),
        ]
    )

    result = await http_poll(
        url="https://api.example.com",
        method="GET",
        poll_condition="lambda x: x['data']['status'] == 'pending'",
        poll_interval=0.1,
        poll_max_attempts=3,
    )

    assert route.call_count == 3
    assert isinstance(result, dict)
    assert result["status_code"] == 200
    assert isinstance(result["data"], dict)
    assert result["data"]["status"] == "completed"


@pytest.mark.anyio
@respx.mock
async def test_http_poll_jsonpath_condition() -> None:
    """Test HTTP polling with jsonpath condition."""
    route = respx.get("https://api.example.com").mock(
        side_effect=[
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": {"state": "pending", "progress": 50}},
            ),
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": {"state": "pending", "progress": 75}},
            ),
            httpx.Response(
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": {"state": "completed", "progress": 100}},
            ),
        ]
    )

    result = await http_poll(
        url="https://api.example.com",
        method="GET",
        # Use jsonpath to check nested status field
        poll_condition="lambda x: jsonpath('$.data.status.state', x) == 'pending'",
        poll_interval=0.1,
        poll_max_attempts=3,
    )

    assert route.call_count == 3
    assert isinstance(result, dict)
    assert result["status_code"] == 200
    assert isinstance(result["data"], dict)
    assert result["data"]["status"]["state"] == "completed"
    assert result["data"]["status"]["progress"] == 100


@pytest.mark.anyio
@respx.mock
async def test_http_poll_max_attempts_exceeded() -> None:
    """Test HTTP polling when max attempts is exceeded."""
    route = respx.get("https://api.example.com").mock(
        side_effect=[httpx.Response(status_code=202) for _ in range(3)]
    )

    with pytest.raises(RetryError):
        await http_poll(
            url="https://api.example.com",
            method="GET",
            poll_retry_codes=202,
            poll_interval=0.1,
            poll_max_attempts=3,
        )

    assert route.call_count == 3


@pytest.mark.anyio
@respx.mock
async def test_http_poll_invalid_params() -> None:
    """Test HTTP polling with invalid parameters."""
    with pytest.raises(
        ValueError, match="At least one of retry_codes or predicate must be specified"
    ):
        await http_poll(
            url="https://api.example.com",
            method="GET",
            poll_interval=0.1,
            poll_max_attempts=3,
        )


@pytest.mark.anyio
@respx.mock
async def test_http_poll_timeout() -> None:
    """Test HTTP polling with timeout."""
    route = respx.get("https://api.example.com").mock(
        side_effect=httpx.ReadTimeout("Timeout")
    )

    with pytest.raises(httpx.ReadTimeout):
        await http_poll(
            url="https://api.example.com",
            method="GET",
            poll_retry_codes=202,
            timeout=1.0,
            poll_interval=0.1,
        )

    assert route.called


@pytest.mark.anyio
@respx.mock
async def test_http_poll_infinite_retries() -> None:
    """Test HTTP polling with infinite retries (max_attempts=0)."""
    responses = [httpx.Response(status_code=202) for _ in range(5)]
    responses.append(
        httpx.Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            json={"status": "done"},
        )
    )

    route = respx.get("https://api.example.com").mock(side_effect=responses)

    result = await http_poll(
        url="https://api.example.com",
        method="GET",
        poll_retry_codes=202,
        poll_interval=0.1,
        poll_max_attempts=0,  # infinite retries
    )

    assert route.call_count == 6
    assert result["status_code"] == 200
    assert isinstance(result["data"], dict)
    assert result["data"]["status"] == "done"


@pytest.mark.anyio
@respx.mock
async def test_http_poll_multiple_retry_codes() -> None:
    """Test HTTP polling with multiple retry status codes."""
    route = respx.get("https://api.example.com").mock(
        side_effect=[
            httpx.Response(status_code=202),  # Accepted
            httpx.Response(status_code=429),  # Too Many Requests
            httpx.Response(  # Success
                status_code=200,
                headers={"Content-Type": "application/json"},
                json={"status": "done"},
            ),
        ]
    )

    result = await http_poll(
        url="https://api.example.com",
        method="GET",
        poll_retry_codes=[202, 429],
        poll_interval=0.1,
    )

    assert route.call_count == 3
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_poll_complex_condition() -> None:
    """Test HTTP polling with a complex condition involving headers and status."""
    route = respx.get("https://api.example.com").mock(
        side_effect=[
            httpx.Response(
                status_code=200,
                headers={"x-status": "pending"},
                json={"data": "processing"},
            ),
            httpx.Response(
                status_code=200,
                headers={"x-status": "completed"},
                json={"data": "done"},
            ),
        ]
    )

    result = await http_poll(
        url="https://api.example.com",
        method="GET",
        poll_condition="lambda x: x['headers'].get('x-status') == 'pending'",
        poll_interval=0.1,
        poll_max_attempts=3,
    )

    assert route.call_count == 2
    assert result["headers"]["x-status"] == "completed"
