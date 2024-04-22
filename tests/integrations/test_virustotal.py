import os

import pytest
from httpx import Response

from tracecat.integrations.virustotal import (
    get_domain_report,
    get_file_report,
    get_ip_address_report,
    get_url_report,
)


@pytest.fixture
def virustotal_secret(create_mock_secret) -> dict[str, str | bytes]:
    mock_secret = create_mock_secret(
        "virustotal", {"VIRUSTOTAL_API_KEY": os.environ["VIRUSTOTAL_API_KEY"]}
    )
    serialized_secret = mock_secret.model_dump()
    return serialized_secret


@pytest.mark.parametrize(
    "file_hash",
    [
        "10c796b7308ac0b9c38f1caa95c798b2b28c46adaa037a9c3a9ebdd3569824e3"
    ],  # Example hash of Mirai malware
)
def test_get_file_report(virustotal_secret, respx_mock, file_hash):
    respx_mock.get(f'{os.environ["TRACECAT__API_URL"]}/secrets/virustotal').mock(
        return_value=Response(status_code=200, json=virustotal_secret)
    )
    result = get_file_report(file_hash)
    assert result["sha256"] == file_hash
    required_keys = [
        "sandbox_verdicts",
        "reputation",
        "last_analysis_results",
        "last_analysis_stats",
    ]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the file report"


def test_get_url_report(virustotal_secret, respx_mock):
    url = "http://example.com"
    respx_mock.get(f'{os.environ["TRACECAT__API_URL"]}/secrets/virustotal').mock(
        return_value=Response(status_code=200, json=virustotal_secret)
    )
    result = get_url_report(url)
    assert result["url"] == url
    required_keys = [
        "title",
        "last_analysis_results",
        "last_analysis_stats",
        "total_votes",
        "reputation",
    ]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the URL report"


def test_get_domain_report(virustotal_secret, respx_mock):
    domain = "ycombinator.com"
    respx_mock.get(f'{os.environ["TRACECAT__API_URL"]}/secrets/virustotal').mock(
        return_value=Response(status_code=200, json=virustotal_secret)
    )
    result = get_domain_report(domain)
    assert result["domain"] == domain
    required_keys = [
        "title",
        "last_analysis_results",
        "last_analysis_stats",
        "total_votes",
    ]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the domain report"


def test_get_ip_address_report(virustotal_secret, respx_mock):
    ip = "8.8.8.8"  # Google's IP
    respx_mock.get(f'{os.environ["TRACECAT__API_URL"]}/secrets/virustotal').mock(
        return_value=Response(status_code=200, json=virustotal_secret)
    )
    result = get_ip_address_report(ip)
    assert result["id"] == ip
    required_keys = ["title", "regional_internet_registry", "whois"]
    assert all(
        key in result for key in required_keys
    ), "Some keys are missing in the IP address report"
