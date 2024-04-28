import os
import time

import pytest
from httpx import Response

from tracecat.integrations.project_discovery import get_all_scan_results


@pytest.fixture
def project_discovery_secret(create_mock_secret) -> dict[str, str | bytes]:
    mock_secret = create_mock_secret(
        "project_discovery", {"PD_API_KEY": os.environ["PD_API_KEY"]}
    )
    mock_secret_obj = mock_secret.model_dump_json()
    return mock_secret_obj


@pytest.mark.respx(assert_all_mocked=False)
@pytest.mark.parametrize(
    "severity,time_filter,vuln_status",
    [
        (None, None, None),
        ("low", None, None),
        ("medium", "last_week", "fixed"),
        ("high", "last_month", "open"),
    ],
)
def test_get_all_scan_results(
    severity, time_filter, vuln_status, project_discovery_secret, respx_mock
):
    # Mock secrets manager
    tracecat_api_url = os.environ["TRACECAT__API_URL"]
    route = respx_mock.get(f"{tracecat_api_url}/secrets/emailrep").mock(
        return_value=Response(status_code=200, content=project_discovery_secret)
    )

    # Assuming the API key is required for live calls and is set in the environment
    result = get_all_scan_results(
        offset=10,
        limit=100,
        severity=severity,
        search=None,
        time=time_filter,
        vuln_status=vuln_status,
    )

    # Asserts to check if the API call was successful and returns the expected structure
    assert route.called
    assert isinstance(result, dict)
    assert "data" in result

    # Throttling API requests to avoid rate limiting
    time.sleep(3)
