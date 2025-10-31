"""Core HTTP actions."""
# WARNING: Do not import __future__ annotations from typing
# Causes class types to resolve as strings, breaking TypedDict runtime behavior

from collections.abc import Callable
import base64
import binascii
from pathlib import Path
import tempfile
from json import JSONDecodeError
from typing import Annotated, Any, Literal, NotRequired, Required, TypedDict

import httpx
from pydantic import BaseModel, ConfigDict, HttpUrl
from tenacity import (
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_never,
    wait_exponential,
    wait_fixed,
)
import yaml
from tracecat.expressions.common import build_safe_lambda, eval_jsonpath
from tracecat.logger import logger
from typing_extensions import Doc

from tracecat.config import (
    TRACECAT__MAX_FILE_SIZE_BYTES,
    TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES,
    TRACECAT__MAX_UPLOAD_FILES_COUNT,
)

from tracecat.types.exceptions import TracecatException
from tracecat_registry import RegistrySecret, registry, secrets

RequestMethods = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
JSONObjectOrArray = dict[str, Any] | list[Any]


class FileUploadData(TypedDict):
    """Detailed file upload data structure."""

    filename: NotRequired[str]  # Filename sent in Content-Disposition header
    content_base64: Required[str]  # Base64 encoded file content
    content_type: NotRequired[str | None]  # Optional MIME type (e.g., "image/png")


class ParsedFileInput(BaseModel):
    """Parsed file input with extracted components."""

    filename: str
    content_base64: str
    content_type: str | None = None


class ValidatedFile(BaseModel):
    """Validated and processed file ready for upload."""

    sanitized_filename: str
    decoded_content: bytes

    model_config = ConfigDict(
        # Allow bytes type
        arbitrary_types_allowed=True
    )


Files = Annotated[
    dict[str, str | FileUploadData] | None,  # Form field name -> file data
    Doc(
        "Files to upload using multipart/form-data. "
        "The dictionary key is the form field name. "
        "The value can be a simple base64 encoded string (filename defaults to form field name), "
        "or a dictionary with 'filename', 'content_base64', and optional 'content_type'."
    ),
]

ssl_secret = RegistrySecret(
    name="ssl",
    optional_keys=["SSL_CLIENT_CERT", "SSL_CLIENT_KEY", "SSL_CLIENT_PASSWORD"],
    optional=True,
)
"""HTTP SSL certificate secrets.

By default, the HTTP action uses the CA bundle from Certifi.
This optional secret allows you to specify a custom client-side certificate to use for SSL verification.

- name: `ssl`
- optional keys:
    - `SSL_CLIENT_CERT`
    - `SSL_CLIENT_KEY`
    - `SSL_CLIENT_PASSWORD`

Note: `SSL_CLIENT_CERT` and `SSL_CLIENT_KEY` are text fields that contain the certificate and key respectively.
`SSL_CLIENT_PASSWORD` is optional.

"""


Url = Annotated[
    str,
    Doc("The destination of the HTTP request"),
    HttpUrl,
]
Method = Annotated[
    RequestMethods,
    Doc("HTTP request method"),
]
Headers = Annotated[
    dict[str, str] | None,
    Doc("HTTP request headers"),
]
Params = Annotated[
    dict[str, Any] | None,
    Doc("URL query parameters"),
]
Payload = Annotated[
    JSONObjectOrArray | None,
    Doc("JSON serializable data in request body (POST, PUT, and PATCH)"),
]
FormData = Annotated[
    dict[str, Any] | None,
    Doc("Form encoded data in request body (POST, PUT, and PATCH)"),
]
Auth = Annotated[
    dict[str, str] | None,
    Doc("Basic auth credentials with `username` and `password` keys"),
]
Timeout = Annotated[
    float,
    Doc("Timeout in seconds"),
]
FollowRedirects = Annotated[
    bool,
    Doc("Follow HTTP redirects"),
]
MaxRedirects = Annotated[
    int,
    Doc("Maximum number of redirects"),
]
VerifySSL = Annotated[
    bool,
    Doc("Verify SSL certificates. Defaults to True, disable at own risk."),
]


class HTTPResponse(TypedDict):
    status_code: int
    headers: dict[str, str]
    data: str | dict[str, Any] | list[Any] | None


class TemporaryClientCertificate:
    """
    Manages temporary files for SSL client certificate and key.
    Ensures files are deleted upon exiting the context.
    """

    def __init__(
        self,
        client_cert_str: str | None = None,
        client_key_str: str | None = None,
        client_key_password: str | None = None,
    ):
        self.client_cert_str = client_cert_str
        self.client_key_str = client_key_str
        self.client_key_password = client_key_password
        self._temp_files: list[tempfile._TemporaryFileWrapper] = []

    def __enter__(self) -> str | tuple[str, str] | tuple[str, str, str] | None:
        cert_path: str | None = None
        key_path: str | None = None

        if self.client_cert_str:
            cert_file = tempfile.NamedTemporaryFile(
                mode="w", delete=True, encoding="utf-8"
            )
            self._temp_files.append(cert_file)
            cert_file.write(self.client_cert_str)
            cert_file.flush()
            cert_path = cert_file.name

        if self.client_key_str:
            key_file = tempfile.NamedTemporaryFile(
                mode="w", delete=True, encoding="utf-8"
            )
            self._temp_files.append(key_file)
            key_file.write(self.client_key_str)
            key_file.flush()
            key_path = key_file.name

        if cert_path and key_path:
            if self.client_key_password:
                return (cert_path, key_path, self.client_key_password)
            return (cert_path, key_path)
        elif cert_path:
            # Single file containing both cert and key (e.g., PEM format)
            return cert_path

        # No valid certificate configuration provided
        return None

    def __exit__(self, exc_type, exc_val, traceback):
        for temp_file in self._temp_files:
            try:
                temp_file.close()  # Triggers file deletion due to delete=True
            except Exception:
                # Log errors but continue cleanup for remaining files
                logger.error(
                    f"Error closing temporary certificate file {temp_file.name}",
                    exc_info=True,
                )


def httpx_to_response(response: httpx.Response) -> HTTPResponse:
    # Handle 204 No Content responses
    if response.status_code == 204:
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=None,  # RFC 7231: 204 responses MUST NOT contain a body
        )

    # Parse response based on content type
    content_type = response.headers.get("Content-Type")
    if content_type and content_type.startswith("application/json"):
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=response.json(),
        )
    else:
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=response.text,
        )


def _try_parse_response_data(response: httpx.Response) -> str | dict[str, Any]:
    try:
        return response.json()
    except JSONDecodeError:
        return response.text


def _format_response_data(response: httpx.Response) -> str:
    data = _try_parse_response_data(response)
    if isinstance(data, dict):
        return yaml.dump(data)
    return str(data)


STATUS_HANDLERS: dict[int, Callable[[httpx.HTTPStatusError], str]] = {
    400: lambda e: (
        f"400 Bad request for '{e.response.url}'."
        f"\n\n{_format_response_data(e.response)}"
        "\n\nThis error occurs when the server cannot understand the request.\nPlease check that:"
        "\n- The request URL is properly formatted"
        "\n- Query parameters are valid"
        "\n- Headers are correctly specified"
        "\n- Request body matches the API requirements"
    ),
    404: lambda e: (
        f"404 Not found for '{e.response.url}'."
        f"\n\nPlease check that the URL is correct and the resource exists:\n"
        f"\nHost: {e.response.url.host}"
        f"\nPort: {e.response.url.port or '-'}"
        f"\nPath: {e.response.url.path}"
    ),
    422: lambda e: (
        f"422 Unprocessable entity for '{e.response.url}'."
        f"\n\n{_format_response_data(e.response)}"
        "\n\nThis error occurs when the server cannot process the request payload.\nPlease check that:"
        "\n- The request body matches the expected format (e.g. valid JSON)"
        "\n- All required fields are included"
        "\n- Field values match the expected types and constraints"
    ),
    500: lambda e: (
        f"500 Internal server error for '{e.response.url}'."
        f"\n\n{_format_response_data(e.response)}"
        "\n\nThis error occurs when the server encounters an unexpected error while processing the request.\nPlease check that:"
        "\n- Check if the server is running and accessible"
        "\n- Review server logs for error details"
        "\n- Contact the server administrator if the issue persists"
        "\n- Try the request again after a few minutes"
    ),
    # Add more status codes as needed...
}


def _http_status_error_to_message(e: httpx.HTTPStatusError) -> str:
    if handler := STATUS_HANDLERS.get(e.response.status_code):
        return handler(e)
    else:
        details = _format_response_data(e.response)
        return f"HTTP request failed with status {e.response.status_code}. Details:\n\n```\n{details}\n```"


def _sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize a filename to prevent security issues.

    Based on OWASP recommendations for secure file upload handling.

    Args:
        filename: The filename to sanitize
        max_length: Maximum allowed length (default 255 for most filesystems)

    Returns:
        Sanitized filename

    Raises:
        ValueError: If filename cannot be made safe
    """
    # Strip directory components to prevent path traversal
    filename = Path(filename).name

    # Remove encoded directory separators
    filename = filename.replace("/", "").replace("\\", "")

    # Split and preserve file extension
    name_parts = filename.rsplit(".", 1)
    if len(name_parts) == 2:
        name, ext = name_parts
        # Only alphanumeric chars in extension, max 10 chars
        ext = "".join(c for c in ext if c.isalnum())[:10]
    else:
        name = filename
        ext = None

    # Remove dangerous characters per OWASP recommendations
    dangerous_chars = "<>:\"|?*;%$'`"
    sanitized_name = "".join(
        c for c in name if c not in dangerous_chars and ord(c) < 127
    )

    # Prevent path traversal via multiple dots
    while ".." in sanitized_name:
        sanitized_name = sanitized_name.replace("..", ".")

    # Fallback for empty names after sanitization
    if not sanitized_name or sanitized_name.strip() in (".", ""):
        sanitized_name = "unnamed"

    # Reconstruct filename
    if ext:
        final_filename = f"{sanitized_name}.{ext}"
    else:
        final_filename = sanitized_name

    # Truncate to filesystem limits while preserving extension
    if len(final_filename) > max_length:
        if ext:
            name_limit = max_length - len(ext) - 1
            final_filename = f"{sanitized_name[:name_limit]}.{ext}"
        else:
            final_filename = final_filename[:max_length]

    return final_filename


def _parse_file_input(
    form_field_name: str,
    file_input: str | FileUploadData,
) -> ParsedFileInput:
    """Parse file input to extract filename, content, and content type.

    Args:
        form_field_name: The form field name
        file_input: Either a base64 string or FileUploadData dict

    Returns:
        ParsedFileInput with filename, content_base64, and optional content_type

    Raises:
        TypeError: If file_input is not a valid type
        ValueError: If required fields are missing
    """
    if isinstance(file_input, str):
        # Simple upload: use form field as filename
        return ParsedFileInput(
            filename=form_field_name, content_base64=file_input, content_type=None
        )

    if isinstance(file_input, dict):
        if "content_base64" not in file_input:
            raise ValueError(
                f"Missing 'content_base64' for form field '{form_field_name}'"
            )

        content_base64 = file_input["content_base64"]
        # Use explicit filename or default to form field name
        filename = file_input.get("filename") or form_field_name
        content_type = file_input.get("content_type")

        return ParsedFileInput(
            filename=filename, content_base64=content_base64, content_type=content_type
        )

    raise TypeError(
        f"Invalid file input type for form field '{form_field_name}'. "
        f"Expected base64 string or dictionary, got {type(file_input).__name__}"
    )


def _validate_and_decode_file(
    filename: str,
    content_base64: str,
    form_field_name: str,
    action_name: str,
) -> ValidatedFile:
    """Validate filename and decode base64 content.

    Args:
        filename: The filename to validate and sanitize
        content_base64: Base64 encoded content
        form_field_name: Form field name for error context
        action_name: Action name for error context

    Returns:
        ValidatedFile with sanitized_filename and decoded_content

    Raises:
        ValueError: If validation fails
    """
    # Null byte security check (used in path injection attacks)
    if not filename or "\x00" in filename:
        raise ValueError(
            f"Invalid filename for form field '{form_field_name}' in {action_name}: "
            f"cannot be empty or contain null bytes."
        )

    # Apply OWASP security sanitization
    try:
        sanitized_filename = _sanitize_filename(filename)
    except Exception as e:
        raise ValueError(
            f"Unable to sanitize filename for form field '{form_field_name}' in {action_name}: {str(e)}"
        ) from e

    # Decode and validate base64 content
    try:
        decoded_content = base64.b64decode(content_base64, validate=True)
    except binascii.Error as e:
        raise ValueError(
            f"Invalid base64 data for form field '{form_field_name}' in {action_name}: {str(e)}"
        ) from e

    # Enforce global file size limits
    if len(decoded_content) > TRACECAT__MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File for form field '{form_field_name}' in {action_name} exceeds maximum size limit of "
            f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB."
        )

    return ValidatedFile(
        sanitized_filename=sanitized_filename, decoded_content=decoded_content
    )


def _process_file_uploads(
    files: Files, action_name: str = "http_action"
) -> dict[str, tuple[str, bytes] | tuple[str, bytes, str]] | None:
    """Processes a dictionary of files for multipart/form-data upload.

    Args:
        files: Dictionary where key is form_field_name and value is either
               a base64 string or a FileUploadData dictionary.
        action_name: Name of the calling action for error messages.

    Returns:
        A dictionary formatted for httpx's files parameter, or None if no files.
    Raises:
        ValueError: For invalid inputs, encoding errors, or size violations.
    """
    if not files:
        return None

    if len(files) > TRACECAT__MAX_UPLOAD_FILES_COUNT:
        raise ValueError(
            f"Number of files ({len(files)}) exceeds the maximum allowed limit "
            f"of {TRACECAT__MAX_UPLOAD_FILES_COUNT} in {action_name}."
        )

    processed_httpx_files = {}
    current_aggregate_size = 0

    for form_field_name, file_input in files.items():
        # Validate form field name (used in HTTP headers)
        if not form_field_name or "\x00" in form_field_name:
            raise ValueError(
                f"Invalid form_field_name '{form_field_name}' in {action_name}: "
                f"cannot be empty or contain null bytes."
            )

        # Extract file components using Pydantic model
        parsed_file = _parse_file_input(form_field_name, file_input)

        # Security validation and processing using Pydantic model
        validated_file = _validate_and_decode_file(
            parsed_file.filename,
            parsed_file.content_base64,
            form_field_name,
            action_name,
        )

        current_aggregate_size += len(validated_file.decoded_content)
        if current_aggregate_size > TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES:
            raise ValueError(
                f"Total size of files ({current_aggregate_size / 1024 / 1024:.2f}MB) "
                f"exceeds the aggregate limit of "
                f"{TRACECAT__MAX_AGGREGATE_UPLOAD_SIZE_BYTES / 1024 / 1024:.2f}MB "
                f"in {action_name}."
            )

        # Format for httpx multipart upload
        if parsed_file.content_type:
            processed_httpx_files[form_field_name] = (
                validated_file.sanitized_filename,
                validated_file.decoded_content,
                parsed_file.content_type,
            )
        else:
            processed_httpx_files[form_field_name] = (
                validated_file.sanitized_filename,
                validated_file.decoded_content,
            )

    return processed_httpx_files


@registry.register(
    namespace="core",
    description="Perform a HTTP request to a given URL.",
    default_title="HTTP request",
    secrets=[ssl_secret],
)
async def http_request(
    url: Url,
    method: Method,
    headers: Headers = None,
    params: Params = None,
    payload: Payload = None,
    form_data: FormData = None,
    files: Files = None,
    auth: Auth = None,
    timeout: Timeout = 10.0,
    follow_redirects: FollowRedirects = False,
    max_redirects: MaxRedirects = 20,
    ignore_status_codes: Annotated[
        list[int] | None,
        Doc(
            "If specified, these status codes will not be treated as errors. Defaults to None."
        ),
    ] = None,
    verify_ssl: VerifySSL = True,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL."""

    ignore_status_codes = ignore_status_codes or []
    basic_auth = httpx.BasicAuth(**auth) if auth else None

    try:
        # Process and validate file uploads
        httpx_files_param = _process_file_uploads(files, action_name="http_request")
    except (ValueError, TypeError) as e:
        logger.error(f"File processing error in http_request: {str(e)}")
        raise TracecatException(str(e)) from e

    client_cert_str = secrets.get_or_default("SSL_CLIENT_CERT")
    client_key_str = secrets.get_or_default("SSL_CLIENT_KEY")
    client_key_password = secrets.get_or_default("SSL_CLIENT_PASSWORD")

    # Manage SSL certificates with proper cleanup
    with TemporaryClientCertificate(
        client_cert_str=client_cert_str,
        client_key_str=client_key_str,
        client_key_password=client_key_password,
    ) as cert_for_httpx:
        try:
            async with httpx.AsyncClient(
                cert=cert_for_httpx,  # SSL cert configuration from context manager
                auth=basic_auth,
                timeout=httpx.Timeout(timeout),
                follow_redirects=follow_redirects,
                max_redirects=max_redirects,
                verify=verify_ssl,
            ) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=payload,
                    data=form_data,
                    files=httpx_files_param,
                )
            if (
                response.status_code >= 400
                and response.status_code not in ignore_status_codes
            ):
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_message = _http_status_error_to_message(e)
            logger.error(
                "HTTP request failed",
                status_code=e.response.status_code,
                error_message=error_message,
            )
            raise TracecatException(error_message) from e
        except httpx.ReadTimeout as e:
            logger.error(f"HTTP request timed out after {timeout} seconds.")
            raise e
        return httpx_to_response(response)


class PredicateArgs(TypedDict):
    headers: dict[str, Any]
    data: Any
    status_code: int


@registry.register(
    namespace="core",
    description="Perform a HTTP request to a given URL with polling.",
    default_title="HTTP poll",
    secrets=[ssl_secret],
)
async def http_poll(
    *,
    # Common
    url: Url,
    method: Method,
    headers: Headers = None,
    params: Params = None,
    payload: Payload = None,
    form_data: FormData = None,
    auth: Auth = None,
    timeout: Timeout = 10.0,
    follow_redirects: FollowRedirects = False,
    max_redirects: MaxRedirects = 20,
    verify_ssl: VerifySSL = True,
    # Polling
    poll_retry_codes: Annotated[
        int | list[int] | None,
        Doc(
            "Status codes on which the action will retry. Ignored if `poll_condition` is provided. If neither are specified, an error will be raised."
        ),
    ] = None,
    poll_interval: Annotated[
        float | None,
        Doc(
            "Interval in seconds between polling attempts. If not specified, defaults to polling with exponential wait."
        ),
    ] = None,
    poll_max_attempts: Annotated[
        int,
        Doc(
            "Maximum number of polling attempts. If set to 0, the action will poll indefinitely (until timeout)."
        ),
    ] = 10,
    poll_condition: Annotated[
        str | None,
        Doc(
            "Python lambda function when evaluated to True, stops polling. The function receives a dict with `headers`, `data`, and `status_code` fields."
        ),
    ] = None,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL with optional polling."""

    basic_auth = httpx.BasicAuth(**auth) if auth else None

    retry_codes = poll_retry_codes
    if isinstance(retry_codes, int):
        retry_codes = [retry_codes]

    predicate = build_safe_lambda(poll_condition) if poll_condition else None

    if not poll_condition and not retry_codes:
        raise ValueError(
            "At least one of poll_condition or poll_retry_codes must be specified"
        )

    # Retry based on HTTP status codes
    def retry_status_code(response: httpx.Response) -> bool:
        if not retry_codes:
            return False
        return response.status_code in retry_codes

    # Retry based on custom lambda condition
    def user_defined_predicate(response: httpx.Response) -> bool:
        if not predicate:
            return False
        data = _try_parse_response_data(response)
        args = PredicateArgs(
            headers=dict(response.headers.items()),
            data=data,
            status_code=response.status_code,
        )
        # Invert the result: retry when condition is NOT met (poll until condition is true)
        return not predicate(args)

    # Use poll_condition if defined, otherwise use retry_codes
    if poll_condition:
        retry_condition = retry_if_result(user_defined_predicate)
    else:
        retry_condition = retry_if_result(retry_status_code)

    @retry(
        stop=stop_after_attempt(poll_max_attempts)
        if poll_max_attempts > 0
        else stop_never,
        wait=wait_fixed(poll_interval)
        if poll_interval is not None
        else wait_exponential(
            multiplier=2,
            min=1,
            max=60,
        ),
        retry=retry_condition,
        reraise=True,
    )
    async def call() -> httpx.Response:
        client_cert_str = secrets.get_or_default("SSL_CLIENT_CERT")
        client_key_str = secrets.get_or_default("SSL_CLIENT_KEY")
        client_key_password = secrets.get_or_default("SSL_CLIENT_PASSWORD")

        # SSL certificate management for each polling attempt
        with TemporaryClientCertificate(
            client_cert_str=client_cert_str,
            client_key_str=client_key_str,
            client_key_password=client_key_password,
        ) as cert_for_httpx:
            try:
                async with httpx.AsyncClient(
                    cert=cert_for_httpx,  # SSL cert configuration from context manager
                    auth=basic_auth,
                    timeout=httpx.Timeout(timeout),
                    follow_redirects=follow_redirects,
                    max_redirects=max_redirects,
                    verify=verify_ssl,
                ) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        json=payload,
                        data=form_data,
                    )
                return response
            except httpx.ReadTimeout as e:
                logger.error(f"HTTP request timed out after {timeout} seconds.")
                raise e

    result = await call()
    return httpx_to_response(result)


@registry.register(
    namespace="core",
    description="Paginate through a HTTP response",
    default_title="HTTP paginate",
)
async def http_paginate(
    *,
    # Common
    url: Url,
    method: Method,
    headers: Headers = None,
    params: Params = None,
    payload: Payload = None,
    form_data: FormData = None,
    auth: Auth = None,
    timeout: Timeout = 10.0,
    follow_redirects: FollowRedirects = False,
    max_redirects: MaxRedirects = 20,
    verify_ssl: VerifySSL = True,
    # Pagination
    stop_condition: Annotated[
        str,
        Doc(
            "Python lambda function that determines when pagination should STOP. The function receives a dict with `headers`, `data`, and `status_code` fields."
        ),
    ],
    next_request: Annotated[
        str,
        Doc(
            "Python lambda function that returns the next request as a JSON of `url`, `method`, `headers`, `params`, `payload`, `form_data` to paginate to. The function receives a dict with `headers`, `data`, and `status_code` fields."
        ),
    ],
    items_jsonpath: Annotated[
        str | None,
        Doc("JSONPath expression that evaluates to the items to paginate through."),
    ] = None,
    limit: Annotated[
        int,
        Doc("Maximum number of items to paginate through. Defaults to 1000."),
    ] = 1000,
) -> list[Any]:
    """Paginate through a HTTP response.

    Returns a list of items when `items_jsonpath` is provided (flattened across pages),
    respecting the `limit` as item-level count. When `items_jsonpath` is None,
    returns a list of `HTTPResponse` objects (one per page), and `limit` applies per-page.
    """

    stop_condition_fn = build_safe_lambda(stop_condition)
    next_request_fn = build_safe_lambda(next_request)
    results: list[Any] = []
    # Short-circuit when no items are requested
    if limit == 0:
        return results
    while len(results) < limit:
        response = await http_request(
            url=url,
            method=method,
            headers=headers,
            params=params,
            payload=payload,
            form_data=form_data,
            files=None,
            auth=auth,
            timeout=timeout,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            verify_ssl=verify_ssl,
        )
        if items_jsonpath is not None:
            data = response["data"]
            # Apply jsonpath to either dict or list root. If not a dict/list, this will raise
            # a TracecatExpressionError, surfacing misuse clearly to users.
            if isinstance(data, dict):
                items_value = eval_jsonpath(items_jsonpath, data)
            else:
                items_value = data

            # Normalize the jsonpath result to a list of items
            if items_value is None:
                page_items: list[Any] = []
            elif isinstance(items_value, list):
                page_items = items_value
            else:
                page_items = [items_value]

            # Enforce item-level limit by extending up to remaining slots
            remaining = max(0, limit - len(results))
            if remaining > 0:
                results.extend(page_items[:remaining])
        else:
            # When no items_jsonpath is provided, append the full page response
            results.append(response)

        # Stop if reached limit or stop condition met
        if len(results) >= limit or stop_condition_fn(response):
            break
        request = next_request_fn(response)
        url = request.get("url", url)
        method = request.get("method", method)
        headers = request.get("headers", headers)
        params = request.get("params", params)
        payload = request.get("payload", payload)
        form_data = request.get("form_data", form_data)

    return results
