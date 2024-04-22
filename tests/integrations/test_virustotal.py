import os

import pytest
from httpx import Response

from tracecat.integrations.virustotal import (
    get_domain_report,
    get_file_report,
    get_ip_address_report,
    get_url_report,
)


@pytest.fixture(scope="module")
def virustotal_secret(create_mock_secret) -> dict[str, str | bytes]:
    mock_secret = create_mock_secret(
        "virustotal", {"VT_API_KEY": os.environ["VT_API_KEY"]}
    )
    mock_secret_obj = mock_secret.model_dump_json()
    return mock_secret_obj


@pytest.mark.parametrize(
    "file_hash",
    [
        "10c796b7308ac0b9c38f1caa95c798b2b28c46adaa037a9c3a9ebdd3569824e3"
    ],  # Example hash of Mirai malware
)
@pytest.mark.respx(assert_all_mocked=False)
def test_get_file_report(virustotal_secret, respx_mock, file_hash):
    respx_mock.base_url = os.environ["TRACECAT__API_URL"]
    respx_mock.get("/secrets/virustotal").mock(
        return_value=Response(status_code=200, content=virustotal_secret)
    )
    respx_mock.route(host="www.virustotal.com").pass_through()
    result = get_file_report(file_hash).get("data")
    assert result["id"] == file_hash


@pytest.mark.respx(assert_all_mocked=False)
def test_get_url_report(virustotal_secret, respx_mock):
    url = "http://example.com/"

    respx_mock.base_url = os.environ["TRACECAT__API_URL"]
    respx_mock.get("/secrets/virustotal").mock(
        return_value=Response(status_code=200, content=virustotal_secret)
    )
    respx_mock.route(host="www.virustotal.com").pass_through()
    result = get_url_report(url).get("data").get("attributes")
    assert result["url"] == url


@pytest.mark.respx(assert_all_mocked=False)
def test_get_domain_report(virustotal_secret, respx_mock):
    domain = "ycombinator.com"
    respx_mock.base_url = os.environ["TRACECAT__API_URL"]
    respx_mock.get("/secrets/virustotal").mock(
        return_value=Response(status_code=200, content=virustotal_secret)
    )
    respx_mock.route(host="www.virustotal.com").pass_through()
    result = get_domain_report(domain).get("data")
    assert result["id"] == domain


@pytest.mark.respx(assert_all_mocked=False)
def test_get_ip_address_report(virustotal_secret, respx_mock):
    ip = "8.8.8.8"  # Google's IP
    respx_mock.base_url = os.environ["TRACECAT__API_URL"]
    respx_mock.get("/secrets/virustotal").mock(
        return_value=Response(status_code=200, content=virustotal_secret)
    )
    respx_mock.route(host="www.virustotal.com").pass_through()
    result = get_ip_address_report(ip).get("data")
    assert result["id"] == ip
