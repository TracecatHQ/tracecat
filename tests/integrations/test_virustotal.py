import pytest
from httpx import Client

from tracecat.integrations.virustotal import (
    get_domain_report,
    get_file_report,
    get_ip_address_report,
    get_url_report,
)

# Create a reusable HTTP client instance
client = Client(base_url="https://www.virustotal.com/api/v3/")


@pytest.mark.parametrize("file_hash", ["your_test_file_hash"])
def test_get_file_report(file_hash):
    response = get_file_report(file_hash)
    assert response.status_code == 200, "Expected a 200 OK status code"


def test_get_url_report():
    url = "http://example.com"
    response = get_url_report(url)
    assert response.status_code == 200, "Expected a 200 OK status code"


def test_get_domain_report():
    domain = "example.com"
    response = get_domain_report(domain)
    assert response.status_code == 200, "Expected a 200 OK status code"


def test_get_ip_address_report():
    ip = "8.8.8.8"
    response = get_ip_address_report(ip)
    assert response.status_code == 200, "Expected a 200 OK status code"
