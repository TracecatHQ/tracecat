"""Core HTTP actions."""
# XXX(WARNING): Do not import __future__ annotations from typing
# This will cause class types to be resolved as strings

from collections.abc import Callable
import base64
import binascii
import tempfile
from json import JSONDecodeError
from typing import Annotated, Any, Literal, TypedDict

import httpx
from pydantic import HttpUrl
from tenacity import (
    retry,
    retry_if_result,
    stop_after_attempt,
    stop_never,
    wait_exponential,
    wait_fixed,
)
import yaml
from tracecat.expressions.common import build_safe_lambda
from tracecat.logger import logger
from typing_extensions import Doc

from tracecat.config import TRACECAT__MAX_FILE_SIZE_BYTES
from tracecat.types.exceptions import TracecatException
from tracecat_registry import RegistrySecret, registry, secrets

RequestMethods = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
JSONObjectOrArray = dict[str, Any] | list[Any]


class FileUploadData(TypedDict, total=False):
    """Detailed file upload data structure."""

    filename: str  # The actual filename to be used in Content-Disposition
    content_base64: str  # Base64 encoded file content
    content_type: str | None  # Optional MIME type for the file


Files = Annotated[
    dict[str, str | FileUploadData] | None,  # Key is the form_field_name
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
            # Only cert_path is provided (e.g. a PEM file with both cert and key)
            return cert_path

        # No client certificate material provided, or only key without cert (which is invalid for httpx cert tuple)
        return None

    def __exit__(self, exc_type, exc_val, traceback):
        for temp_file in self._temp_files:
            try:
                temp_file.close()  # Closing triggers deletion due to delete=True
            except Exception:
                # Log if closing fails, but attempt to close all others.
                logger.error(
                    f"Error closing temporary certificate file {temp_file.name}",
                    exc_info=True,
                )


def httpx_to_response(response: httpx.Response) -> HTTPResponse:
    # Handle 204 No Content
    if response.status_code == 204:
        return HTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers.items()),
            data=None,  # No content
        )

    # TODO: Better parsing logic
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

    processed_httpx_files = {}
    for form_field_name, file_input in files.items():
        if not form_field_name or "\x00" in form_field_name:
            raise ValueError(
                f"Invalid form_field_name '{form_field_name}' in {action_name}: cannot be empty or contain null bytes."
            )

        actual_filename: str
        content_base64: str
        content_type: str | None = None

        if isinstance(file_input, str):
            actual_filename = form_field_name  # Default filename to form field name
            content_base64 = file_input
        elif isinstance(file_input, dict):
            if "content_base64" not in file_input:
                raise ValueError(
                    f"Missing 'content_base64' for form field '{form_field_name}' in {action_name}."
                )
            content_base64 = file_input["content_base64"]
            # Use provided filename, default to form_field_name if 'filename' is missing or empty
            actual_filename = file_input.get("filename") or form_field_name
            content_type = file_input.get("content_type")
        else:
            raise TypeError(
                f"Invalid file input type for form field '{form_field_name}' in {action_name}. "
                f"Expected base64 string or dictionary, got {type(file_input).__name__}."
            )

        if not actual_filename or "\x00" in actual_filename:
            raise ValueError(
                f"Invalid actual_filename '{actual_filename}' for form field '{form_field_name}' in {action_name}: "
                f"cannot be empty or contain null bytes."
            )

        try:
            decoded_content = base64.b64decode(content_base64, validate=True)
        except binascii.Error as e:
            raise ValueError(
                f"Invalid base64 data for file '{actual_filename}' (form field '{form_field_name}') in {action_name}: {str(e)}"
            ) from e

        if len(decoded_content) > TRACECAT__MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"File '{actual_filename}' (form field '{form_field_name}') in {action_name} exceeds maximum size limit of "
                f"{TRACECAT__MAX_FILE_SIZE_BYTES // 1024 // 1024}MB."
            )

        if content_type:
            processed_httpx_files[form_field_name] = (
                actual_filename,
                decoded_content,
                content_type,
            )
        else:
            processed_httpx_files[form_field_name] = (actual_filename, decoded_content)

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
    verify_ssl: VerifySSL = True,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL."""

    basic_auth = httpx.BasicAuth(**auth) if auth else None

    try:
        # Use the new _process_file_uploads function
        httpx_files_param = _process_file_uploads(files, action_name="http_request")
    except ValueError as e:
        logger.error(f"File processing error in http_request: {str(e)}")
        raise TracecatException(str(e)) from e

    client_cert_str = secrets.get("SSL_CLIENT_CERT")
    client_key_str = secrets.get("SSL_CLIENT_KEY")
    client_key_password = secrets.get("SSL_CLIENT_PASSWORD")

    # Use the new context manager
    with TemporaryClientCertificate(
        client_cert_str=client_cert_str,
        client_key_str=client_key_str,
        client_key_password=client_key_password,
    ) as cert_for_httpx:
        try:
            async with httpx.AsyncClient(
                cert=cert_for_httpx,  # Pass the result from the context manager
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
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_message = _http_status_error_to_message(e)
            logger.error(
                "HTTP request failed",
                status_code=e.response.status_code,
                error_message=error_message,
            )
            raise TracecatException(error_message)
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
            "Status codes on which the action will retry. If not specified, `poll_condition` must be provided."
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
            "User defined condition that determines whether to retry. The condition is a Python lambda function string. If not specified, `poll_retry_codes` must be provided."
        ),
    ] = None,
) -> HTTPResponse:
    """Perform a HTTP request to a given URL with optional polling."""

    basic_auth = httpx.BasicAuth(**auth) if auth else None
    # REMOVED: cert = get_certificate() # This was not present here but good to ensure no residue

    retry_codes = poll_retry_codes
    if isinstance(retry_codes, int):
        retry_codes = [retry_codes]

    predicate = build_safe_lambda(poll_condition) if poll_condition else None

    if not retry_codes and not predicate:
        raise ValueError("At least one of retry_codes or predicate must be specified")

    # The default predicate is to retry on the specified status codes
    def retry_status_code(response: httpx.Response) -> bool:
        if not retry_codes:
            return False
        return response.status_code in retry_codes

    # We also wanna support user defined predicate as a `python_lambda`
    def user_defined_predicate(response: httpx.Response) -> bool:
        if not predicate:
            return False
        data = _try_parse_response_data(response)
        args = PredicateArgs(
            headers=dict(response.headers.items()),
            data=data,
            status_code=response.status_code,
        )
        return predicate(args)

    @retry(
        stop=stop_after_attempt(poll_max_attempts)
        if poll_max_attempts > 0
        else stop_never,
        wait=wait_fixed(poll_interval) if poll_interval else wait_exponential(),
        retry=(
            retry_if_result(retry_status_code) | retry_if_result(user_defined_predicate)
        ),
        # Reraise critical errors immediately without retrying
        reraise=True,
    )
    async def call() -> httpx.Response:
        client_cert_str = secrets.get("SSL_CLIENT_CERT")
        client_key_str = secrets.get("SSL_CLIENT_KEY")
        client_key_password = secrets.get("SSL_CLIENT_PASSWORD")

        # Use the new context manager within the call function
        with TemporaryClientCertificate(
            client_cert_str=client_cert_str,
            client_key_str=client_key_str,
            client_key_password=client_key_password,
        ) as cert_for_httpx:
            try:
                async with httpx.AsyncClient(
                    cert=cert_for_httpx,  # Pass the result from the context manager
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
