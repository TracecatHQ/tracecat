import httpx
import pytest
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.exceptions import (
    TracecatAPIError,
    TracecatNotFoundError,
    TracecatValidationError,
)


def test_error_response_preserves_message_field() -> None:
    client = TracecatClient(api_url="http://api", token="", workspace_id="")
    response = httpx.Response(500, json={"message": "database temporarily unavailable"})

    with pytest.raises(TracecatAPIError) as exc_info:
        client._handle_error_response(response)

    assert exc_info.value.detail == "database temporarily unavailable"
    assert "database temporarily unavailable" in str(exc_info.value)


def test_error_response_preserves_structured_detail() -> None:
    client = TracecatClient(api_url="http://api", token="", workspace_id="")
    detail = {
        "code": "DATABASE_VALUE_TYPE_MISMATCH",
        "message": "A field received a value with an incompatible type.",
    }
    response = httpx.Response(422, json={"detail": detail})

    with pytest.raises(TracecatValidationError) as exc_info:
        client._handle_error_response(response)

    assert exc_info.value.detail == detail
    assert "DATABASE_VALUE_TYPE_MISMATCH" in str(exc_info.value)
    assert "incompatible type" in str(exc_info.value)


def test_not_found_response_preserves_message_field() -> None:
    client = TracecatClient(api_url="http://api", token="", workspace_id="")
    response = httpx.Response(404, json={"message": "variable not found"})

    with pytest.raises(TracecatNotFoundError) as exc_info:
        client._handle_error_response(response)

    assert exc_info.value.detail == "variable not found"
    assert "variable not found" in str(exc_info.value)
    assert "Resource 'variable not found' not found" not in str(exc_info.value)
