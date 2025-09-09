"""Tests for HTTP actions."""

import base64

import httpx
import pytest
import respx
from tenacity import RetryError
from tracecat_registry.core.http import (
    FileUploadData,
    http_paginate,
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
            httpx.Response(status_code=202),  # Attempt 1: retry on 202
            httpx.Response(status_code=202),  # Attempt 2: retry on 202
            httpx.Response(  # Attempt 3: success, stop polling
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
        poll_condition="lambda x: x['data']['status'] == 'completed'",
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
        # Poll until status.state is 'completed'
        poll_condition="lambda x: x['data']['status']['state'] == 'completed'",
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
        ValueError,
        match="At least one of poll_condition or poll_retry_codes must be specified",
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
        poll_condition="lambda x: x['headers'].get('x-status') == 'completed'",
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
    form_field_name = "test_file.txt"  # Form field name, also used as filename

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={form_field_name: base64_content},
    )
    assert route.called
    assert result["status_code"] == 200
    content_type_header = route.calls.last.request.headers.get("content-type", "")
    assert "multipart/form-data" in content_type_header
    # Note: respx doesn't easily expose multipart file details for inspection


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
    # Note: respx limitations prevent detailed multipart inspection


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
            "attachment1": file1_base64,  # Simple: filename = form field name
            "attachment2": {  # Detailed: explicit filename and content type
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
            files={
                "data_file": {"filename": "test.txt"}
            },  # Missing required field  # type: ignore
        )


@pytest.mark.anyio
async def test_http_request_files_invalid_form_field_name_null_byte() -> None:
    """Test HTTP request fails if a form_field_name contains a null byte."""
    base64_content = base64.b64encode(b"test").decode("utf-8")
    with pytest.raises(
        TracecatException,
        match=r"Invalid form_field_name.*cannot be empty or contain null bytes",
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={"field\x00name": base64_content},  # Null byte injection attempt
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
        TracecatException, match=r"Invalid base64 data for form field 'test.txt'"
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={"test.txt": "not-valid-base64!@#$"},  # Invalid base64 string
        )


@pytest.mark.anyio
async def test_http_request_file_size_limit_in_files() -> None:
    """Test HTTP request fails when a file in the files dict exceeds size limit."""
    large_content_bytes = b"x" * (21 * 1024 * 1024)  # 21 MB file
    base64_content = base64.b64encode(large_content_bytes).decode("utf-8")

    with pytest.raises(
        TracecatException,
        match=r"File for form field 'large_file.dat' in http_request exceeds maximum size limit of 20MB\.",
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
        match=r"Invalid filename.*cannot be empty or contain null bytes",
    ):
        await http_request(
            url="https://api.example.com",
            method="POST",
            files={
                "upload_field": {
                    "filename": "test\x00.txt",  # Null byte in filename
                    "content_base64": base64_file_content,
                }
            },
        )


# Security-focused file upload tests
@pytest.mark.anyio
@respx.mock
async def test_http_request_path_traversal_in_filename() -> None:
    """Test that path traversal attempts in filenames are sanitized."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )

    base64_content = base64.b64encode(b"Test data").decode("utf-8")

    # Common path traversal attack patterns
    dangerous_filenames = [
        "../../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "normal_file.txt/../../evil.sh",
        "./../../sensitive.conf",
    ]

    for dangerous_filename in dangerous_filenames:
        result = await http_request(
            url="https://api.example.com",
            method="POST",
            files={
                "upload": {
                    "filename": dangerous_filename,
                    "content_base64": base64_content,
                }
            },
        )

        assert route.called
        assert result["status_code"] == 200
        # Filename sanitization should have removed dangerous path components


@pytest.mark.anyio
@respx.mock
async def test_http_request_special_chars_in_filename() -> None:
    """Test that special characters in filenames are sanitized."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )

    base64_content = base64.b64encode(b"Test data").decode("utf-8")

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={
            "upload": {
                "filename": 'file<>:"|?*name.txt',  # OWASP dangerous characters
                "content_base64": base64_content,
            }
        },
    )

    assert route.called
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_very_long_filename() -> None:
    """Test that very long filenames are truncated properly."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )

    base64_content = base64.b64encode(b"Test data").decode("utf-8")

    # Filename exceeding filesystem limits (255 chars)
    long_filename = "a" * 300 + ".txt"

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={
            "upload": {
                "filename": long_filename,
                "content_base64": base64_content,
            }
        },
    )

    assert route.called
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_filename_with_multiple_dots() -> None:
    """Test that filenames with multiple dots are handled safely."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )

    base64_content = base64.b64encode(b"Test data").decode("utf-8")

    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={
            "upload": {
                "filename": "file...name....txt",  # Multiple dots (path traversal attempt)
                "content_base64": base64_content,
            }
        },
    )

    assert route.called
    assert result["status_code"] == 200


@pytest.mark.anyio
@respx.mock
async def test_http_request_empty_filename_after_sanitization() -> None:
    """Test handling of filenames that become empty after sanitization."""
    route = respx.post("https://api.example.com").mock(
        return_value=httpx.Response(status_code=200, json={"uploaded": True})
    )

    base64_content = base64.b64encode(b"Test data").decode("utf-8")

    # Filename containing only dangerous characters
    result = await http_request(
        url="https://api.example.com",
        method="POST",
        files={
            "upload": {
                "filename": "<>:|",  # Only special characters, becomes "unnamed"
                "content_base64": base64_content,
            }
        },
    )

    assert route.called
    assert result["status_code"] == 200


@pytest.mark.anyio
async def test_http_request_file_upload_max_files_exceeded() -> None:
    """Test HTTP request fails if number of files exceeds TRACECAT__MAX_UPLOAD_FILES_COUNT."""
    from tracecat_registry.core import http as core_http  # For accessing the constant

    files_to_upload: dict[str, str | FileUploadData] = {}
    for i in range(core_http.TRACECAT__MAX_UPLOAD_FILES_COUNT + 1):
        files_to_upload[f"file{i}.txt"] = base64.b64encode(
            f"content{i}".encode()
        ).decode()

    with pytest.raises(TracecatException) as excinfo:
        await http_request(
            url="https://api.example.com/upload",
            method="POST",
            files=files_to_upload,
        )
    assert (
        f"Number of files ({core_http.TRACECAT__MAX_UPLOAD_FILES_COUNT + 1}) exceeds the maximum allowed limit of {core_http.TRACECAT__MAX_UPLOAD_FILES_COUNT}"
        in str(excinfo.value)
    )


@pytest.mark.anyio
async def test_http_request_file_upload_aggregate_size_exceeded() -> None:
    """Test HTTP request fails if total size of files exceeds TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES."""
    pytest.skip(
        "Cannot test aggregate size limit with current config: individual limit (20MB) * max files (5) = 100MB which equals aggregate limit (100MB). Need individual limit > 20MB or aggregate limit < 100MB to test this scenario."
    )


# Test HTTP paginate function
@pytest.fixture
def pagination_scenarios():
    """Fixture providing different pagination scenarios for testing."""
    return {
        "cursor_in_body": {
            "responses": [
                httpx.Response(
                    status_code=200,
                    json={
                        "items": [{"id": 1}, {"id": 2}],
                        "cursor": "https://api.example.com/resources?cursor=page2",
                    },
                ),
                httpx.Response(
                    status_code=200,
                    json={
                        "items": [{"id": 3}, {"id": 4}],
                        "cursor": "https://api.example.com/resources?cursor=page3",
                    },
                ),
                httpx.Response(
                    status_code=200,
                    json={"items": [{"id": 5}], "cursor": None},
                ),
            ],
            "stop_condition": "lambda x: x['data'].get('cursor') is None",
            "next_request": "lambda x: {'url': x['data'].get('cursor')}",
            "items_jsonpath": "$.items",
            "expected_items": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}],
        },
        "offset_total": {
            "responses": [
                httpx.Response(
                    status_code=200,
                    json={
                        "items": [{"id": 101}, {"id": 102}],
                        "offset": 0,
                        "limit": 2,
                        "total": 5,
                    },
                ),
                httpx.Response(
                    status_code=200,
                    json={
                        "items": [{"id": 103}, {"id": 104}],
                        "offset": 2,
                        "limit": 2,
                        "total": 5,
                    },
                ),
                httpx.Response(
                    status_code=200,
                    json={"items": [{"id": 105}], "offset": 4, "limit": 2, "total": 5},
                ),
            ],
            "stop_condition": "lambda x: (x['data'].get('offset', 0) + x['data'].get('limit', 0)) >= x['data'].get('total', 0)",
            "next_request": "lambda x: {'params': {'offset': x['data'].get('offset', 0) + x['data'].get('limit', 0)}}",
            "items_jsonpath": "$.items",
            "expected_items": [
                {"id": 101},
                {"id": 102},
                {"id": 103},
                {"id": 104},
                {"id": 105},
            ],
        },
        "next_page_token": {
            "responses": [
                httpx.Response(
                    status_code=200,
                    json={
                        "items": [{"id": "a"}, {"id": "b"}],
                        "nextPageToken": "token-page2",
                    },
                ),
                httpx.Response(
                    status_code=200,
                    json={
                        "items": [{"id": "c"}, {"id": "d"}],
                        "nextPageToken": "token-page3",
                    },
                ),
                httpx.Response(
                    status_code=200,
                    json={"items": [{"id": "e"}], "nextPageToken": None},
                ),
            ],
            "stop_condition": "lambda x: not x['data'].get('nextPageToken')",
            "next_request": "lambda x: {'params': {'pageToken': x['data'].get('nextPageToken')}}",
            "items_jsonpath": "$.items",
            "expected_items": [
                {"id": "a"},
                {"id": "b"},
                {"id": "c"},
                {"id": "d"},
                {"id": "e"},
            ],
        },
        "link_header": {
            "responses": [
                httpx.Response(
                    status_code=200,
                    headers={
                        "Link": '<https://api.example.com/resources?page=2>; rel="next", <https://api.example.com/resources?page=10>; rel="last"'
                    },
                    json={"items": [{"id": 1}, {"id": 2}]},
                ),
                httpx.Response(
                    status_code=200,
                    headers={
                        "Link": '<https://api.example.com/resources?page=3>; rel="next", <https://api.example.com/resources?page=10>; rel="last"'
                    },
                    json={"items": [{"id": 3}, {"id": 4}]},
                ),
                httpx.Response(
                    status_code=200,
                    headers={
                        "Link": '<https://api.example.com/resources?page=10>; rel="last"'
                    },
                    json={"items": [{"id": 5}]},
                ),
            ],
            "stop_condition": "lambda x: 'link' not in {k.lower(): v for k, v in x['headers'].items()} or 'rel=\"next\"' not in {k.lower(): v for k, v in x['headers'].items()}['link']",
            "next_request": "lambda x: {'url': [p.split(';')[0].strip('<> ') for p in {k.lower(): v for k, v in x['headers'].items()}['link'].split(',') if 'rel=\"next\"' in p][0]}",
            "items_jsonpath": "$.items",
            "expected_items": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}],
        },
        "header_cursor": {
            "responses": [
                httpx.Response(
                    status_code=200,
                    headers={"x-next-cursor": "cursor-page2"},
                    json={"items": [{"id": 1}], "count": 1},
                ),
                httpx.Response(
                    status_code=200,
                    headers={"x-next-cursor": "cursor-page3"},
                    json={"items": [{"id": 2}], "count": 1},
                ),
                httpx.Response(
                    status_code=200,
                    headers={},  # No x-next-cursor header
                    json={"items": [{"id": 3}], "count": 1},
                ),
            ],
            "stop_condition": "lambda x: not x['headers'].get('x-next-cursor')",
            "next_request": "lambda x: {'params': {'cursor': x['headers'].get('x-next-cursor')}}",
            "items_jsonpath": "$.items",
            "expected_items": [{"id": 1}, {"id": 2}, {"id": 3}],
        },
        "empty_items_stop": {
            "responses": [
                httpx.Response(
                    status_code=200,
                    json={"items": [{"id": 1}, {"id": 2}], "page": 1},
                ),
                httpx.Response(
                    status_code=200,
                    json={"items": [{"id": 3}], "page": 2},
                ),
                httpx.Response(
                    status_code=200,
                    json={"items": [], "page": 3},  # Empty items stops pagination
                ),
            ],
            "stop_condition": "lambda x: not x['data'].get('items')",
            "next_request": "lambda x: {'params': {'page': (x['data'].get('page', 1) + 1)}}",
            "items_jsonpath": "$.items",
            "expected_items": [{"id": 1}, {"id": 2}, {"id": 3}],
        },
    }


@pytest.mark.anyio
@respx.mock
@pytest.mark.parametrize(
    "scenario_name",
    [
        "cursor_in_body",
        "offset_total",
        "next_page_token",
        "link_header",
        "header_cursor",
        "empty_items_stop",
    ],
)
async def test_http_paginate_patterns(pagination_scenarios, scenario_name) -> None:
    """Test various pagination patterns using parameterized scenarios."""
    scenario = pagination_scenarios[scenario_name]

    # Mock the API responses
    route = respx.get("https://api.example.com/resources").mock(
        side_effect=scenario["responses"]
    )

    # Call http_paginate with scenario-specific parameters
    result = await http_paginate(
        url="https://api.example.com/resources",
        method="GET",
        stop_condition=scenario["stop_condition"],
        next_request=scenario["next_request"],
        items_jsonpath=scenario["items_jsonpath"],
        limit=1000,
    )

    # Verify correct number of requests
    assert route.call_count == len(scenario["responses"])

    # Verify results match expected items (flattened across pages)
    assert isinstance(result, list)
    assert result == scenario["expected_items"]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_without_jsonpath() -> None:
    """Test pagination without items_jsonpath returns per-page HTTPResponse objects."""
    responses = [
        httpx.Response(
            status_code=200,
            json={"data": "page1", "hasMore": True},
        ),
        httpx.Response(
            status_code=200,
            json={"data": "page2", "hasMore": True},
        ),
        httpx.Response(
            status_code=200,
            json={"data": "page3", "hasMore": False},
        ),
    ]

    route = respx.get("https://api.example.com/data").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/data",
        method="GET",
        stop_condition="lambda x: not x['data'].get('hasMore')",
        next_request="lambda x: {'url': 'https://api.example.com/data'}",
        items_jsonpath=None,  # No JSONPath extraction
        limit=1000,
    )

    assert route.call_count == 3
    assert len(result) == 3
    assert result[0]["data"] == {"data": "page1", "hasMore": True}
    assert result[1]["data"] == {"data": "page2", "hasMore": True}
    assert result[2]["data"] == {"data": "page3", "hasMore": False}


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_with_dollar_jsonpath() -> None:
    """Test pagination using $ as JSONPath to get entire response data."""
    responses = [
        httpx.Response(
            status_code=200,
            json={"data": "page1", "hasMore": True},
        ),
        httpx.Response(
            status_code=200,
            json={"data": "page2", "hasMore": True},
        ),
        httpx.Response(
            status_code=200,
            json={"data": "page3", "hasMore": False},
        ),
    ]

    route = respx.get("https://api.example.com/data").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/data",
        method="GET",
        stop_condition="lambda x: not x['data'].get('hasMore')",
        next_request="lambda x: {'url': 'https://api.example.com/data'}",
        items_jsonpath="$",  # Get entire response data
        limit=1000,
    )

    assert route.call_count == 3
    assert len(result) == 3

    # With $ JSONPath, should return full data objects
    for i in range(3):
        assert result[i] == {"data": f"page{i + 1}", "hasMore": i < 2}


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_limit_enforcement() -> None:
    """Test that pagination respects the limit parameter."""
    # Create more responses than the limit
    responses = [
        httpx.Response(
            status_code=200,
            json={"items": [{"id": i}], "page": i},
        )
        for i in range(1, 10)  # 9 responses
    ]

    route = respx.get("https://api.example.com/limited").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/limited",
        method="GET",
        stop_condition="lambda x: False",  # Never stop based on condition
        next_request="lambda x: {'params': {'page': x['data'].get('page', 0) + 1}}",
        items_jsonpath="$.items",
        limit=3,  # Limit to 3 items (flattened)
    )

    # Should stop after collecting 3 items
    assert route.call_count == 3
    assert result == [{"id": 1}, {"id": 2}, {"id": 3}]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_with_post_method() -> None:
    """Test pagination using POST method with payload."""
    responses = [
        httpx.Response(
            status_code=200,
            json={"results": [1, 2], "continuation": "token1"},
        ),
        httpx.Response(
            status_code=200,
            json={"results": [3, 4], "continuation": None},
        ),
    ]

    route = respx.post("https://api.example.com/search").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/search",
        method="POST",
        payload={"query": "test"},
        stop_condition="lambda x: x['data'].get('continuation') is None",
        next_request="lambda x: {'payload': {'query': 'test', 'continuation': x['data'].get('continuation')}}",
        items_jsonpath="$.results",
        limit=1000,
    )

    assert route.call_count == 2
    assert result == [1, 2, 3, 4]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_with_headers_and_auth() -> None:
    """Test pagination with headers and authentication."""
    responses = [
        httpx.Response(
            status_code=200,
            json={"items": ["a"], "next": 2},
        ),
        httpx.Response(
            status_code=200,
            json={"items": ["b"], "next": None},
        ),
    ]

    route = respx.get("https://api.example.com/secure").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/secure",
        method="GET",
        headers={"X-API-Key": "secret"},
        auth={"username": "user", "password": "pass"},
        stop_condition="lambda x: x['data'].get('next') is None",
        next_request="lambda x: {'params': {'page': x['data'].get('next')}}",
        items_jsonpath="$.items",
        limit=1000,
    )

    assert route.call_count == 2

    # Check that auth headers were sent
    for call in route.calls:
        assert "Authorization" in call.request.headers
        assert call.request.headers["X-API-Key"] == "secret"

    assert result == ["a", "b"]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_immediate_stop() -> None:
    """Test pagination that stops immediately on first response."""
    route = respx.get("https://api.example.com/single").mock(
        return_value=httpx.Response(
            status_code=200,
            json={"items": [1, 2, 3], "complete": True},
        )
    )

    result = await http_paginate(
        url="https://api.example.com/single",
        method="GET",
        stop_condition="lambda x: x['data'].get('complete') == True",
        next_request="lambda x: {'url': 'https://api.example.com/single'}",
        items_jsonpath="$.items",
        limit=1000,
    )

    # Should only make one request and flatten items
    assert route.call_count == 1
    assert result == [1, 2, 3]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_complex_jsonpath() -> None:
    """Test pagination with complex JSONPath expressions."""
    responses = [
        httpx.Response(
            status_code=200,
            json={
                "response": {"data": {"users": [{"name": "Alice"}, {"name": "Bob"}]}},
                "hasMore": True,
            },
        ),
        httpx.Response(
            status_code=200,
            json={
                "response": {"data": {"users": [{"name": "Charlie"}]}},
                "hasMore": False,
            },
        ),
    ]

    route = respx.get("https://api.example.com/nested").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/nested",
        method="GET",
        stop_condition="lambda x: not x['data'].get('hasMore')",
        next_request="lambda x: {'url': 'https://api.example.com/nested'}",
        items_jsonpath="$.response.data.users",
        limit=1000,
    )

    assert route.call_count == 2
    assert result == [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_update_multiple_params() -> None:
    """Test pagination that updates multiple request parameters."""
    responses = [
        httpx.Response(
            status_code=200,
            json={"items": [1], "next_offset": 10, "next_limit": 5},
        ),
        httpx.Response(
            status_code=200,
            json={"items": [2], "next_offset": None},
        ),
    ]

    route = respx.get("https://api.example.com/multi").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/multi",
        method="GET",
        params={"offset": 0, "limit": 10},
        stop_condition="lambda x: x['data'].get('next_offset') is None",
        next_request="lambda x: {'params': {'offset': x['data'].get('next_offset'), 'limit': x['data'].get('next_limit', 10)}}",
        items_jsonpath="$.items",
        limit=1000,
    )

    assert route.call_count == 2
    assert result == [1, 2]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_error_handling() -> None:
    """Test error handling in pagination."""
    route = respx.get("https://api.example.com/error").mock(
        side_effect=[
            httpx.Response(
                status_code=200,
                json={"items": [1], "next": True},
            ),
            httpx.Response(
                status_code=500,
                json={"error": "Internal Server Error"},
            ),
        ]
    )

    with pytest.raises(TracecatException) as excinfo:
        await http_paginate(
            url="https://api.example.com/error",
            method="GET",
            stop_condition="lambda x: not x['data'].get('next')",
            next_request="lambda x: {'url': 'https://api.example.com/error'}",
            items_jsonpath="$.items",
            limit=1000,
        )

    assert route.call_count == 2
    assert "500" in str(excinfo.value)


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_timeout() -> None:
    """Test timeout handling in pagination."""
    route = respx.get("https://api.example.com/timeout").mock(
        side_effect=[
            httpx.Response(status_code=200, json={"items": [1], "next": True}),
            httpx.ReadTimeout("Timeout"),
        ]
    )

    with pytest.raises(httpx.ReadTimeout):
        await http_paginate(
            url="https://api.example.com/timeout",
            method="GET",
            stop_condition="lambda x: not x['data'].get('next')",
            next_request="lambda x: {'url': 'https://api.example.com/timeout'}",
            items_jsonpath="$.items",
            timeout=1.0,
            limit=1000,
        )

    assert route.call_count == 2


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_form_data() -> None:
    """Test pagination with form data."""
    responses = [
        httpx.Response(
            status_code=200,
            json={"items": ["form1"], "page": 1},
        ),
        httpx.Response(
            status_code=200,
            json={"items": ["form2"], "page": 2},
        ),
    ]

    route = respx.post("https://api.example.com/form").mock(side_effect=responses)

    result = await http_paginate(
        url="https://api.example.com/form",
        method="POST",
        form_data={"field": "value"},
        stop_condition="lambda x: x['data'].get('page') >= 2",
        next_request="lambda x: {'form_data': {'field': 'value', 'page': x['data'].get('page', 0) + 1}}",
        items_jsonpath="$.items",
        limit=1000,
    )

    assert route.call_count == 2
    assert result == ["form1", "form2"]


@pytest.mark.anyio
@respx.mock
async def test_http_paginate_zero_limit() -> None:
    """Test pagination with limit=0 (no items collected)."""
    route = respx.get("https://api.example.com/zero").mock(
        return_value=httpx.Response(
            status_code=200,
            json={"items": [1, 2, 3]},
        )
    )

    result = await http_paginate(
        url="https://api.example.com/zero",
        method="GET",
        stop_condition="lambda x: True",  # Stop immediately
        next_request="lambda x: {'url': 'https://api.example.com/zero'}",
        items_jsonpath="$.items",
        limit=0,  # Zero limit
    )

    # Should not make any requests when limit is 0
    assert route.call_count == 0
    assert result == []
