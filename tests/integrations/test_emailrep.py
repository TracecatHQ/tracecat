import os

import pytest
from httpx import Response

from tracecat.integrations.emailrep import check_email_reputation


@pytest.fixture
def emailrep_secret(create_mock_secret) -> dict[str, str | bytes]:
    mock_secret = create_mock_secret(
        "emailrep", {"EMAILREP_API_KEY": os.environ["EMAILREP_API_KEY"]}
    )
    mock_secret_obj = mock_secret.model_dump_json()
    return mock_secret_obj


@pytest.mark.respx(assert_all_mocked=False)
def test_check_email_reputation(emailrep_secret, respx_mock):
    test_email = "john@google.com"

    # Mock secrets manager
    respx_mock.base_url = os.environ["TRACECAT__API_URL"]
    route = respx_mock.get("/secrets/emailrep").mock(
        return_value=Response(status_code=200, content=emailrep_secret)
    )

    result = check_email_reputation(email=test_email, app_name="test")
    assert route.called
    assert result["email"] == test_email
    assert "reputation" in result
    assert "suspicious" in result
    assert "summary" in result
