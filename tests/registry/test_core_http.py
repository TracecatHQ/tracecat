"""Tests for HTTP actions."""

import base64

import httpx
import pytest
import respx
from tenacity import RetryError
from tracecat_registry.core.http import (
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


# Test file upload functionality
@pytest.mark.anyio
@respx.mock
async def test_http_request_with_file_upload_simple_base64() -> None:
    """Test HTTP request with a simple base64 string file upload."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )
    file_content_bytes = b"Hello, World!"
    base64_content = base64.b64encode(file_content_bytes).decode("utf-8")
    form_field_name = "test_file.txt"  # This will also be the filename

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={form_field_name: base64_content},
    )
    assert route.called
    assert result["status_code"] == 200
    content_type_header = route.calls.last.request.headers.get("content-type", "")
    assert "multipart/form-data" in content_type_header
    # TODO: Add specific checks for filename and content in multipart data if respx allows.


@pytest.mark.anyio
@respx.mock
async def test_http_request_with_file_upload_dict_metadata() -> None:
    """Test HTTP request with file upload using a dictionary with metadata."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )
    file_content_bytes = b"Custom field test!"
    base64_content = base64.b64encode(file_content_bytes).decode("utf-8")
    form_field_name = "logUpload"
    actual_filename = "custom_field_file.log"
    mime_type = "text/plain"

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={
            form_field_name: {
                "filename": actual_filename,
                "content_base64": base64_content,
                "content_type": mime_type,
            }
        },
    )
    assert route.called
    assert result["status_code"] == 200
    # TODO: Add specific checks for form_field_name, actual_filename, content, and content_type.


@pytest.mark.anyio
@respx.mock
async def test_http_request_with_multiple_files_and_form_data() -> None:
    """Test HTTP request with multiple files and additional form data."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"success": True})
    )

    file1_bytes = b"Test file content 1"
    file1_base64 = base64.b64encode(file1_bytes).decode("utf-8")
    file2_bytes = b"Test file content 2 - CSV data,col2\nval1,val2"
    file2_base64 = base64.b64encode(file2_bytes).decode("utf-8")

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        form_data={"user_id": "123", "description": "Multiple files test"},
        files={
            "attachment1": file1_base64,  # Simple upload
            "attachment2": {  # Upload with metadata
                "filename": "report.csv",
                "content_base64": file2_base64,
                "content_type": "text/csv",
            },
        },
    )
    assert route.called
    assert result["status_code"] == 200
    content_type_header = route.calls.last.request.headers.get("content-type", "")
    assert "multipart/form-data" in content_type_header


@pytest.mark.anyio
async def test_http_request_files_missing_content_base64_in_dict() -> None:
    """Test HTTP request fails if 'content_base64' is missing in a file dict."""
    with pytest.raises(
        TracecatException, match=r"Missing 'content_base64' for form field 'data_file'"
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={"data_file": {"filename": "test.txt"}},  # Missing content_base64
        )


@pytest.mark.anyio
async def test_http_request_files_invalid_form_field_name_null_byte() -> None:
    """Test HTTP request fails if a form_field_name contains a null byte."""
    base64_content = base64.b64encode(b"test").decode("utf-8")
    with pytest.raises(
        TracecatException, match=r"Invalid form_field_name.*contains null bytes"
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={"field\x00name": base64_content},
        )


@pytest.mark.anyio
async def test_http_request_files_empty_form_field_name() -> None:
    """Test HTTP request fails if a form_field_name is empty."""
    base64_content = base64.b64encode(b"test").decode("utf-8")
    with pytest.raises(
        TracecatException, match=r"Invalid form_field_name.*cannot be empty"
    ):
        await http_request(
            url="https://api.example.com", method="POST", files={"": base64_content}
        )


@pytest.mark.anyio
async def test_http_request_with_invalid_base64_in_files() -> None:
    """Test HTTP request with invalid base64 data in the files dictionary."""
    with pytest.raises(
        TracecatException, match=r"Invalid base64 data for file 'test.txt'"
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={"test.txt": "not-valid-base64!@#$"},
        )


@pytest.mark.anyio
async def test_http_request_file_size_limit_in_files() -> None:
    """Test HTTP request fails when a file in the files dict exceeds size limit."""
    large_content_bytes = b"x" * (101 * 1024 * 1024)  # 101 MB
    base64_content = base64.b64encode(large_content_bytes).decode("utf-8")

    with pytest.raises(
        TracecatException, match=r"File 'large_file.dat'.*exceeds maximum size limit"
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={"large_file.dat": base64_content},
        )


@pytest.mark.anyio
async def test_http_request_actual_filename_null_bytes_in_files_dict() -> None:
    """Test HTTP request fails with null bytes in actual_filename within a file dict."""
    base64_file_content = base64.b64encode(b"Test data").decode("utf-8")
    with pytest.raises(
        TracecatException,
        match=r"Invalid actual_filename.*test\x00.txt.*contains null bytes",
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={
                "upload_field": {
                    "filename": "test\x00.txt",
                    "content_base64": base64_file_content,
                }
            },
        )
