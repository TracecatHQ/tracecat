import os

import pytest
from httpx import Response

from tracecat.integrations.urlscan import analyze_url


@pytest.fixture
def urlscan_secret(create_mock_secret) -> dict[str, str | bytes]:
    mock_secret = create_mock_secret(
        "urlscan", {"URLSCAN_API_KEY": os.environ["URLSCAN_API_KEY"]}
    )
    mock_secret_obj = mock_secret.model_dump_json()
    return mock_secret_obj


def test_analyze_url_live(urlscan_secret, respx_mock):
    test_url = "http://google.com"

    # Mock secrets manager
    route = respx_mock.get(f'{os.environ["TRACECAT__API_URL"]}/secrets/urlscan').mock(
        return_value=Response(status_code=200, content=urlscan_secret)
    )
    result = analyze_url(test_url)

    assert route.called
    # Assert that key interesting fields are present
    assert all(
        key in result["verdicts"]["urlscan"]
        for key in ["score", "categories", "brands"]
    )
    assert all(
        key in result["page"] for key in ["city", "country", "ip", "asnname", "asn"]
    )
